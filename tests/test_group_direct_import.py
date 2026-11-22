"""Direct Telegram group-to-contacts import and bulk assignment tests.

Uses a temporary SQLite file and synthetic scan objects. No Telegram traffic.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from broadcaster import core, group_export, group_import
from broadcaster.web import app


class DirectGroupImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        keys=("DATABASE_PATH","APP_ENCRYPTION_KEY","TELEGRAM_SESSION_DIR",
              "COOKIE_SECURE","SEND_ENABLED")
        self.before={key:os.environ.get(key) for key in keys}
        os.environ.update({
            "DATABASE_PATH":str(Path(self.tmp.name)/"test.sqlite3"),
            "APP_ENCRYPTION_KEY":Fernet.generate_key().decode(),
            "TELEGRAM_SESSION_DIR":str(Path(self.tmp.name)/"sessions"),
            "COOKIE_SECURE":"false",
            "SEND_ENABLED":"false",
        })
        core.init_db()
        core.create_admin("owner","test-password")
        with core.database() as db:
            core.create_user(db,"other","other-password")
            self.owner_id=db.execute("SELECT id FROM admins WHERE username='owner'").fetchone()["id"]
            self.other_id=db.execute("SELECT id FROM admins WHERE username='other'").fetchone()["id"]
            db.commit()
        self.http=TestClient(app,raise_server_exceptions=True)
        self.http.__enter__()
        response=self.http.post("/login",data={"username":"owner","password":"test-password"},
                                follow_redirects=False)
        self.assertEqual(response.status_code,303)
        with core.database() as db:
            self.csrf=core.session(db,self.http.cookies["tb_session"])["csrf"]
        group_export._scans.clear()

    def tearDown(self):
        self.http.__exit__(None,None,None)
        group_export._scans.clear()
        for k,v in self.before.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        self.tmp.cleanup()

    def make_scan(self, members=None, *, title="Prague Customers", state="complete"):
        if members is None:
            members=[
                group_export.ExportMember("Alice Smith","alice_member_2","", "123456789"),
                group_export.ExportMember("Bob Without Username","","", "234567890"),
                group_export.ExportMember("Charlie","charlie_member","+420777123456", "345678901"),
                group_export.ExportMember("Name only","","", ""),
            ]
        scan=group_export.GroupScan(
            token="scan-token-123", owner_id=self.owner_id,
            account_id="telegram-sender-one", peer_id=-1234,
            created_at=time.monotonic(), state=state,
            group_title=title, members=members,
        )
        group_export._scans[scan.token]=scan
        return scan

    def post_import(self, csrf=None, name="Prague Customers", token="scan-token-123"):
        return self.http.post(
            f"/groups/scans/{token}/import",
            data={"csrf":self.csrf if csrf is None else csrf,"group_name":name},
            follow_redirects=False,
        )

    def test_one_click_import_creates_same_named_group_and_id_only_contacts(self):
        self.make_scan()
        page=self.http.get("/groups/scans/scan-token-123")
        self.assertEqual(page.status_code,200)
        self.assertIn('value="Prague Customers"',page.text)
        self.assertIn("Import 4 members directly",page.text)
        self.assertIn("Telegram User ID",page.text)
        response=self.post_import()
        self.assertEqual(response.status_code,303)
        self.assertIn("/contacts/groups/",response.headers["location"])
        with core.database() as db:
            groups=core.contact_groups(db,self.owner_id)
            self.assertEqual(len(groups),1)
            self.assertEqual(groups[0]["name"],"Prague Customers")
            self.assertEqual(groups[0]["total"],3)
            self.assertEqual(groups[0]["eligible"],0)
            records=db.execute(
                "SELECT name,username,phone,user_id,consent FROM contacts WHERE owner_id=? ORDER BY name",
                (self.owner_id,),
            ).fetchall()
            self.assertEqual(len(records),3)
            self.assertEqual({r["user_id"] for r in records},
                             {"123456789","234567890","345678901"})
            self.assertTrue(all(r["consent"]==0 for r in records))
            bob=next(r for r in records if r["name"]=="Bob Without Username")
            self.assertEqual((bob["username"],bob["phone"]),("",""))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0],0)
        self.assertIn("3 contacts added",response.headers["location"].replace("%20"," "))

    def test_import_again_links_no_duplicates_or_changes_in_consent(self):
        self.make_scan()
        self.assertEqual(self.post_import().status_code,303)
        self.assertEqual(self.post_import().status_code,303)
        with core.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contact_groups").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contact_group_members").fetchone()[0],3)

    def test_preexisting_contact_is_linked_and_verified_id_filled_without_optin_change(self):
        scan=self.make_scan()
        group_id=None
        with core.database() as db:
            existing=core.insert_contact(
                db,core.normalize_contact("Original Display","@alice_member_2","","","Personal note"),
                self.owner_id,consent=True,
            )
            group_id=core.add_contact_group(db,self.owner_id,"Another List")
            core.set_contact_groups(db,self.owner_id,existing,[group_id])
            db.commit()
        response=self.post_import()
        self.assertEqual(response.status_code,303)
        with core.database() as db:
            row=db.execute("SELECT * FROM contacts WHERE id=?",(existing,)).fetchone()
            self.assertEqual(row["name"],"Original Display")
            self.assertEqual(row["user_id"],"123456789")
            self.assertEqual(row["notes"],"Personal note")
            self.assertEqual(row["consent"],1)
            membership=core.contact_group_map(db,self.owner_id)[existing]
            self.assertEqual(len(membership),2)
            self.assertIn(group_id,membership)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],3)

    def test_conflicting_or_archived_contact_never_silently_reactivated(self):
        self.make_scan(members=[
            group_export.ExportMember("Conflicting","alice_member_2","","123456789"),
            group_export.ExportMember("Archived","old_member12","","234567890"),
        ])
        with core.database() as db:
            conflict=core.insert_contact(
                db,core.normalize_contact("Existing","@alice_member_2","","999999999",""),
                self.owner_id,consent=True,
            )
            archived=core.insert_contact(
                db,core.normalize_contact("Archived","@old_member12","","234567890",""),
                self.owner_id,consent=True,
            )
            db.execute("UPDATE contacts SET archived=1 WHERE id=?",(archived,))
            db.commit()
        response=self.post_import()
        self.assertEqual(response.status_code,303)
        with core.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contact_group_members").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT consent FROM contacts WHERE id=?",(conflict,)).fetchone()[0],1)
            self.assertEqual(db.execute("SELECT archived FROM contacts WHERE id=?",(archived,)).fetchone()[0],1)
        self.assertIn("2 skipped",response.headers["location"].replace("%20"," "))

    def test_wrong_owner_token_csrf_and_invalid_name_are_rejected(self):
        scan=self.make_scan()
        self.assertEqual(self.post_import(csrf="wrong").status_code,403)
        self.assertEqual(self.post_import(name="bad;name").status_code,303)
        self.assertEqual(self.post_import(token="missing-token").status_code,404)
        with TestClient(app,raise_server_exceptions=True) as other:
            r=other.post("/login",data={"username":"other","password":"other-password"},
                         follow_redirects=False)
            self.assertEqual(r.status_code,303)
            with core.database() as db:
                csrf=core.session(db,other.cookies["tb_session"])["csrf"]
            forged=other.post("/groups/scans/scan-token-123/import",
                              data={"csrf":csrf,"group_name":"Prague Customers"},
                              follow_redirects=False)
            self.assertEqual(forged.status_code,404)
        with core.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contact_groups").fetchone()[0],0)
        with core.database() as db:
            with self.assertRaises(ValueError):
                group_import.import_scan_members(db,scan,self.other_id,"Prague Customers")

    def test_partial_scan_is_explicit_and_limits_are_enforced(self):
        scan=self.make_scan(state="partial")
        page=self.http.get("/groups/scans/scan-token-123")
        self.assertIn("partial",page.text.lower())
        self.assertEqual(self.post_import().status_code,303)
        scan.members=[group_export.ExportMember("Test","user_test9","",str(100000000+i))
                      for i in range(5001)]
        page=self.http.get("/groups/scans/scan-token-123")
        self.assertIn("Direct import is limited",page.text)
        with core.database() as db:
            with self.assertRaisesRegex(ValueError,"5,000"):
                group_import.import_scan_members(db,scan,self.owner_id,"Test Group")

    def test_groups_title_sanitization_and_exported_user_ids(self):
        scan=self.make_scan(title="  Public ; Group | North  ")
        page=self.http.get("/groups/scans/scan-token-123")
        self.assertIn('value="Public - Group - North"',page.text)
        result=self.http.get("/groups/scans/scan-token-123/export")
        self.assertEqual(result.status_code,200)
        self.assertIn("Telegram User ID",result.text)
        self.assertIn("234567890",result.text)

    def test_bulk_select_from_contacts_is_additive_not_destructive(self):
        self.make_scan(members=[
            group_export.ExportMember("Test","test_valid9","", "123456789")
        ])
        self.assertEqual(self.post_import().status_code,303)
        with core.database() as db:
            group=core.contact_groups(db,self.owner_id)[0]["id"]
            other_group=core.add_contact_group(db,self.owner_id,"Secondary")
            rows=db.execute("SELECT id FROM contacts").fetchall()
            one=rows[0]["id"]
            core.set_contact_groups(db,self.owner_id,one,[group,other_group])
            second=core.insert_contact(
                db,core.normalize_contact("Second","@second_valid99","","",""),
                self.owner_id,consent=False,
            )
            db.commit()
        page=self.http.get("/contacts")
        self.assertIn("Add selected",page.text)
        self.assertIn("bulk-contact-group-form",page.text)
        response=self.http.post("/contacts/bulk-group",
                                data={"csrf":self.csrf,"group_id":str(group),
                                      "contacts":[str(one),str(second)],"action":"add"},
                                follow_redirects=False)
        self.assertEqual(response.status_code,303)
        with core.database() as db:
            memberships=core.contact_group_map(db,self.owner_id)
            self.assertEqual(memberships[one],{group,other_group})
            self.assertEqual(memberships[second],{group})
        response=self.http.post("/contacts/bulk-group",
                                data={"csrf":self.csrf,"group_id":str(group),
                                      "contacts":[str(one)],"action":"remove"},
                                follow_redirects=False)
        self.assertEqual(response.status_code,303)
        with core.database() as db:
            memberships=core.contact_group_map(db,self.owner_id)
            self.assertEqual(memberships[one],{other_group})
            self.assertEqual(memberships[second],{group})

    def test_group_export_csv_can_still_be_imported_manually(self):
        raw=("Name,Username,Phone Number,Telegram User ID\n"
             "Only ID,,,456789012\n"
             "User and Phone,other_valid_123,+420700700700,567890123\n")
        result=self.http.post("/contacts/import",data={"csrf":self.csrf},
            files={"file":("members.csv",raw.encode("utf8"),"text/csv")},
            follow_redirects=False)
        self.assertEqual(result.status_code,303)
        with core.database() as db:
            rows=db.execute("SELECT name,username,phone,user_id,consent FROM contacts "
                            "WHERE owner_id=? ORDER BY name",(self.owner_id,)).fetchall()
            self.assertEqual(len(rows),2)
            ids={r["user_id"] for r in rows}
            self.assertEqual(ids,{"456789012","567890123"})
            self.assertTrue(all(r["consent"]==0 for r in rows))

    def test_cross_owner_bulk_selection_is_atomic(self):
        self.make_scan(members=[group_export.ExportMember("One","alpha_user_9","", "123456789")])
        self.post_import()
        with core.database() as db:
            group=core.contact_groups(db,self.owner_id)[0]["id"]
            own=db.execute("SELECT id FROM contacts WHERE owner_id=?",(self.owner_id,)).fetchone()["id"]
            foreign=core.insert_contact(
                db,core.normalize_contact("Outside","@outside_member","","",""),
                self.other_id,consent=True,
            )
            db.commit()
        result=self.http.post("/contacts/bulk-group",
                              data={"csrf":self.csrf,"group_id":str(group),
                                    "contacts":[str(own),str(foreign)],"action":"add"},
                              follow_redirects=False)
        self.assertEqual(result.status_code,303)
        with core.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contact_group_members").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contact_group_members WHERE group_id=?",
                                        (group,)).fetchone()[0],1)
        self.assertEqual(self.http.post("/contacts/bulk-group",
                              data={"csrf":"bad","group_id":str(group),
                                    "contacts":[str(own)],"action":"add"},
                              follow_redirects=False).status_code,403)


    def test_exported_scan_csv_restores_group_without_manual_assignment(self):
        self.make_scan(title="Prague Customers")
        exported=self.http.get("/groups/scans/scan-token-123/export")
        self.assertEqual(exported.status_code,200)
        self.assertIn("Telegram Group",exported.text)
        self.assertIn("Prague Customers",exported.text)
        result=self.http.post(
            "/contacts/import",data={"csrf":self.csrf},
            files={"file":("telegram-group-members.csv",exported.content,"text/csv")},
            follow_redirects=False,
        )
        self.assertEqual(result.status_code,303)
        with core.database() as db:
            group=core.contact_groups(db,self.owner_id)[0]
            self.assertEqual(group["name"],"Prague Customers")
            self.assertEqual(group["total"],3)
            self.assertEqual(group["eligible"],0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM contacts WHERE owner_id=?",
                (self.owner_id,),
            ).fetchone()[0],3)
        self.assertIn(f"/contacts/groups/{group['id']}",result.headers["location"])
        page=self.http.get(f"/contacts/groups/{group['id']}")
        self.assertIn("3 selected",page.text)

    def test_csv_reuses_existing_disabled_contact_and_preserves_other_groups(self):
        self.make_scan(members=[
            group_export.ExportMember("Alice New Name","alice_member_2","", "123456789")
        ])
        with core.database() as db:
            existing=core.insert_contact(
                db,core.normalize_contact(
                    "Original Name","@alice_member_2","","","Keep this note",
                ),self.owner_id,consent=True,enabled=False,
            )
            old_group=core.add_contact_group(db,self.owner_id,"Previous Group")
            core.set_contact_groups(db,self.owner_id,existing,[old_group])
            db.commit()
        data=self.http.get("/groups/scans/scan-token-123/export").content
        for _ in range(2):
            result=self.http.post(
                "/contacts/import",data={"csrf":self.csrf},
                files={"file":("members.csv",data,"text/csv")},
                follow_redirects=False,
            )
            self.assertEqual(result.status_code,303)
        with core.database() as db:
            groups=core.contact_groups(db,self.owner_id)
            new_group=next(g["id"] for g in groups if g["name"]=="Prague Customers")
            contact=db.execute("SELECT * FROM contacts WHERE id=?",(existing,)).fetchone()
            self.assertEqual(contact["name"],"Original Name")
            self.assertEqual(contact["notes"],"Keep this note")
            self.assertEqual(contact["user_id"],"123456789")
            self.assertEqual((contact["consent"],contact["enabled"]),(1,0))
            self.assertEqual(core.contact_group_map(db,self.owner_id)[existing],
                             {old_group,new_group})
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],1)

    def test_explicit_csv_group_and_flags_are_idempotent(self):
        raw=("Name,Username,Enabled,Opt In\n"
             "Disabled,@disabled_csv_1,no,no\n"
             "Opted In,@enabled_csv_1,yes,yes\n").encode()
        for _ in range(2):
            result=self.http.post(
                "/contacts/import",
                data={"csrf":self.csrf,"group_name":"Imported Archive"},
                files={"file":("contacts.csv",raw,"text/csv")},
                follow_redirects=False,
            )
            self.assertEqual(result.status_code,303)
        with core.database() as db:
            rows=db.execute(
                "SELECT name,enabled,consent FROM contacts WHERE owner_id=? "
                "ORDER BY name",(self.owner_id,),
            ).fetchall()
            self.assertEqual(len(rows),2)
            self.assertEqual((rows[0]["enabled"],rows[0]["consent"]),(0,0))
            self.assertEqual((rows[1]["enabled"],rows[1]["consent"]),(1,1))
            self.assertEqual(core.contact_groups(db,self.owner_id)[0]["total"],2)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM contact_group_members"
            ).fetchone()[0],2)
        self.assertIn("already",result.headers["location"])

    def test_csv_over_thousand_rows_is_not_silently_truncated(self):
        raw="Name,Telegram User ID,Telegram Group\n"+"".join(
            f"Person {i},{500000000+i},Big Group\n" for i in range(1001)
        )
        response=self.http.post(
            "/contacts/import",data={"csrf":self.csrf},
            files={"file":("large.csv",raw.encode(),"text/csv")},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code,303)
        with core.database() as db:
            group=core.contact_groups(db,self.owner_id)[0]
            self.assertEqual(group["total"],1001)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],1001)
        self.assertIn("Imported%201001%20contacts",response.headers["location"])

    def test_csv_exceeding_safety_limit_rejects_without_writes(self):
        raw="Name,Telegram User ID\n"+"".join(
            f"Person {i},{500000000+i}\n" for i in range(5001)
        )
        response=self.http.post(
            "/contacts/import",data={"csrf":self.csrf,"group_name":"Would Not Exist"},
            files={"file":("oversize.csv",raw.encode(),"text/csv")},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code,303)
        self.assertIn("rejected",response.headers["location"].lower())
        with core.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contact_groups").fetchone()[0],0)

    def test_csv_conflict_rolls_back_new_group_and_preserves_archived_record(self):
        with core.database() as db:
            old=core.insert_contact(
                db,core.normalize_contact(
                    "Archived","@archived_user1","","123456789","Do not restore"
                ),self.owner_id,consent=True,enabled=False,
            )
            db.execute("UPDATE contacts SET archived=1 WHERE id=?",(old,))
            db.commit()
        raw=("Name,Username,Telegram User ID,Telegram Group\n"
             "Archived,@archived_user1,123456789,Should Not Be Created\n")
        response=self.http.post(
            "/contacts/import",data={"csrf":self.csrf},
            files={"file":("archived.csv",raw.encode(),"text/csv")},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code,303)
        self.assertIn("1%20skipped",response.headers["location"])
        with core.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contact_groups").fetchone()[0],0)
            self.assertEqual(tuple(db.execute(
                "SELECT archived,enabled,consent FROM contacts WHERE id=?",(old,)
            ).fetchone()),(1,0,1))


if __name__=="__main__":
    unittest.main()

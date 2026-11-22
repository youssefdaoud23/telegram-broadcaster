"""Offline tests for private, overlapping contact groups and broadcast selection.

All tests use a temporary SQLite database and never access Telegram.
"""
from __future__ import annotations

import csv
import io
import os
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from broadcaster import core
from broadcaster.web import app


class ContactGroupsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        keys=("DATABASE_PATH","APP_ENCRYPTION_KEY","TELEGRAM_SESSION_DIR",
              "COOKIE_SECURE","SEND_ENABLED","SEND_MAX_RECIPIENTS")
        self.old={k:os.environ.get(k) for k in keys}
        os.environ.update({
            "DATABASE_PATH":str(Path(self.temp.name)/"groups.sqlite3"),
            "APP_ENCRYPTION_KEY":Fernet.generate_key().decode(),
            "TELEGRAM_SESSION_DIR":str(Path(self.temp.name)/"sessions"),
            "COOKIE_SECURE":"false",
            "SEND_ENABLED":"false",
        })
        os.environ.pop("SEND_MAX_RECIPIENTS",None)
        core.init_db()
        core.create_admin("owner","test-password")
        self.patch_refresh=patch("broadcaster.web.telegram.refresh_connected_accounts",return_value=[])
        self.patch_refresh.start()
        self.http=TestClient(app,raise_server_exceptions=True)
        self.http.__enter__()
        result=self.http.post(
            "/login",data={"username":"owner","password":"test-password"},
            follow_redirects=False,
        )
        self.assertEqual(result.status_code,303)
        with core.database() as db:
            session=core.session(db,self.http.cookies.get("tb_session"))
        self.owner_id=session["admin_id"]
        self.csrf=session["csrf"]
        self.sender=uuid.uuid4().hex
        with core.database() as db:
            db.execute(
                "INSERT INTO accounts(id,label,api_id,api_hash_enc,phone_enc,session_name,"
                "state,created_at,updated_at,owner_id) VALUES (?,'Offline sender',111,?,?,?,"
                "'connected',?,?,?)",
                (self.sender,core.encrypt("hash"),core.encrypt("+420123456789"),
                 self.sender,core.now(),core.now(),self.owner_id),
            )
            db.commit()

    def tearDown(self):
        self.http.__exit__(None,None,None)
        self.patch_refresh.stop()
        for k,v in self.old.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        self.temp.cleanup()

    def add_contact(self,name,username,*,consent=True,enabled=True):
        with core.database() as db:
            cid=core.insert_contact(
                db,core.normalize_contact(name,username,"","",""),
                self.owner_id,consent=consent,enabled=enabled,
            )
            db.commit()
        return cid

    def add_group(self,name):
        with core.database() as db:
            gid=core.add_contact_group(db,self.owner_id,name)
            db.commit()
        return gid

    def set_groups(self,contact_id,group_ids):
        with core.database() as db:
            core.set_contact_groups(db,self.owner_id,contact_id,group_ids)
            db.commit()

    def post(self,path,data):
        return self.http.post(path,data={"csrf":self.csrf,**data},follow_redirects=False)

    def test_group_creation_duplicate_names_and_private_group_pages(self):
        self.assertEqual(self.http.get("/contacts/groups").status_code,200)
        created=self.post("/contacts/groups",{"name":"Clients Prague"})
        self.assertEqual(created.status_code,303)
        self.assertTrue(created.headers["location"].startswith("/contacts/groups/"))
        gid=int(created.headers["location"].split("?")[0].rsplit("/",1)[-1])
        group=self.http.get(f"/contacts/groups/{gid}")
        self.assertEqual(group.status_code,200)
        self.assertIn("Clients Prague",group.text)
        self.assertIn("Broadcast to this group",group.text)
        repeat=self.post("/contacts/groups",{"name":"CLIENTS PRAGUE"})
        self.assertEqual(repeat.status_code,303)
        with core.database() as db:
            groups=core.contact_groups(db,self.owner_id)
        self.assertEqual(len(groups),1)
        self.assertEqual(groups[0]["name"],"Clients Prague")
        renamed=self.post(f"/contacts/groups/{gid}/rename",{"name":"Clients France"})
        self.assertEqual(renamed.status_code,303)
        with core.database() as db:
            self.assertEqual(core.contact_groups(db,self.owner_id)[0]["name"],"Clients France")

    def test_contact_may_belong_to_multiple_groups_and_edit_membership(self):
        a=self.add_group("Group of five")
        b=self.add_group("Group of seven")
        create=self.post("/contacts",{
            "name":"Alice","username":"@alice_group_123","enabled":"yes",
            "consent":"yes","group_ids":[str(a),str(b)],
        })
        self.assertEqual(create.status_code,303)
        with core.database() as db:
            row=db.execute("SELECT id FROM contacts WHERE name='Alice'").fetchone()
            cid=row["id"]
            self.assertEqual(core.contact_group_map(db,self.owner_id)[cid],{a,b})
        edit=self.http.get(f"/contacts/{cid}/edit")
        self.assertIn(f'value="{a}"',edit.text)
        self.assertIn(f'value="{b}"',edit.text)
        self.assertIn("select multiple",edit.text)
        changed=self.post(f"/contacts/{cid}/edit",{
            "name":"Alice Updated","username":"@alice_group_123",
            "enabled":"yes","consent":"yes","group_ids":[str(b)],
        })
        self.assertEqual(changed.status_code,303)
        with core.database() as db:
            self.assertEqual(core.contact_group_map(db,self.owner_id)[cid],{b})
            self.assertEqual(db.execute("SELECT name FROM contacts WHERE id=?",(cid,)).fetchone()[0],
                             "Alice Updated")
        contact_page=self.http.get(f"/contacts?group_id={b}")
        self.assertIn("Alice Updated",contact_page.text)
        self.assertIn("Group of seven",contact_page.text)
        self.assertEqual(self.http.get(f"/contacts?group_id={a}").text.count("Alice Updated"),0)

    def test_group_editor_can_bulk_assign_and_keep_nonconsenting_members(self):
        group=self.add_group("Special audience")
        a=self.add_contact("Alice","@group_alice01")
        b=self.add_contact("Bob","@group_bobby01",consent=False)
        c=self.add_contact("Charlie","@group_charly1")
        response=self.post(f"/contacts/groups/{group}/members",
                           {"contacts":[str(a),str(b)]})
        self.assertEqual(response.status_code,303)
        page=self.http.get(f"/contacts/groups/{group}")
        self.assertIn("2 selected",page.text)
        self.assertIn("No consent",page.text)
        with core.database() as db:
            record=core.contact_groups(db,self.owner_id)[0]
            self.assertEqual(record["total"],2)
            self.assertEqual(record["eligible"],1)
        response=self.post(f"/contacts/groups/{group}/members",
                           {"contacts":[str(a),str(c)]})
        self.assertEqual(response.status_code,303)
        with core.database() as db:
            self.assertEqual({k for k,v in core.contact_group_map(db,self.owner_id).items() if group in v},
                             {a,c})

    def test_compose_group_prefill_and_overlapping_group_union(self):
        left=self.add_group("Five contacts")
        right=self.add_group("Seven contacts")
        c1=self.add_contact("Alice","@union_alice")
        c2=self.add_contact("Bob","@union_bob01")
        c3=self.add_contact("Carol","@union_carol")
        blocked=self.add_contact("No optin","@union_noopt",consent=False)
        self.set_groups(c1,[left])
        self.set_groups(c2,[left,right])
        self.set_groups(c3,[right])
        self.set_groups(blocked,[right])
        page=self.http.get(f"/compose?group_id={right}")
        self.assertEqual(page.status_code,200)
        self.assertIn("Select recipients by group",page.text)
        self.assertIn(f'value="{right}" checked',page.text)
        self.assertIn(f'value="{c2}" checked',page.text)
        self.assertIn(f'value="{c3}" checked',page.text)
        self.assertNotIn(f'value="{blocked}"',page.text)
        self.assertIn(f'|{left}|',page.text)
        self.assertIn(f'|{right}|',page.text)
        # Two overlapping groups select the union of unique, eligible contacts.
        with core.database() as db:
            member=core.contact_group_map(db,self.owner_id)
            eligible=db.execute(
                "SELECT id FROM contacts WHERE owner_id=? AND archived=0 AND enabled=1 AND consent=1",
                (self.owner_id,),
            ).fetchall()
            recipients=sorted({row["id"] for row in eligible if member.get(row["id"],set())&{left,right}})
            self.assertEqual(recipients,sorted([c1,c2,c3]))
            draft=core.create_campaign(db,self.sender,"Group broadcast",recipients,None,self.owner_id)
            db.commit()
            details,deliveries=core.campaign_summary(db,draft,self.owner_id)
        self.assertEqual(details["state"],"draft")
        self.assertEqual(details["total"],3)
        self.assertEqual(len(deliveries),3)
        self.assertEqual(len({d["contact_id"] for d in deliveries}),3)

    def test_group_delete_does_not_delete_contacts_or_history(self):
        group=self.add_group("Temporary")
        cid=self.add_contact("Alice","@history_alice")
        self.set_groups(cid,[group])
        with core.database() as db:
            campaign=core.create_campaign(db,self.sender,"Original",[cid],None,self.owner_id)
            db.commit()
        response=self.post(f"/contacts/groups/{group}/delete",{})
        self.assertEqual(response.status_code,303)
        with core.database() as db:
            self.assertFalse(core.contact_groups(db,self.owner_id))
            self.assertFalse(core.contact_group_map(db,self.owner_id))
            self.assertIsNotNone(db.execute("SELECT id FROM contacts WHERE id=?",(cid,)).fetchone())
            self.assertIsNotNone(db.execute("SELECT id FROM campaigns WHERE id=?",(campaign,)).fetchone())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM deliveries WHERE campaign_id=?",(campaign,)).fetchone()[0],1)

    def test_group_csv_export_import_and_partial_row_rollback(self):
        a=self.add_group("Customers")
        b=self.add_group("Friends")
        c=self.add_contact("Existing","@csv_existing")
        self.set_groups(c,[a,b])
        exported=self.http.get("/contacts/export")
        self.assertEqual(exported.status_code,200)
        data=list(csv.DictReader(io.StringIO(exported.text)))
        self.assertEqual(data[0]["Groups"],"Customers; Friends")
        self.assertEqual(data[0]["Name"],"Existing")
        raw=("Name,Username,Opt In,Groups\n"
             "Valid,@csv_valid_1,yes,Customers; Friends\n"
             "Invalid,@csv_invalid_1,yes,Customers; Customers\n"
             "Missing,@csv_missing_1,yes,Nonexistent\n")
        imported=self.http.post("/contacts/import",data={"csrf":self.csrf},
                                files={"file":("members.csv",raw.encode(),"text/csv")},
                                follow_redirects=False)
        self.assertEqual(imported.status_code,303)
        self.assertIn("Imported%201%20contacts",imported.headers["location"])
        with core.database() as db:
            names=[r["name"] for r in core.active_contact_rows(db,self.owner_id)]
            self.assertEqual(sorted(names),["Existing","Valid"])
            row=db.execute("SELECT id FROM contacts WHERE name='Valid'").fetchone()
            self.assertEqual(core.contact_group_map(db,self.owner_id)[row["id"]],{a,b})

    def test_cross_workspace_group_access_and_owner_guard(self):
        owned=self.add_group("Owner Private")
        own_contact=self.add_contact("Owner","@owner_private1")
        with core.database() as db:
            member_id=core.create_user(db,"colleague","colleague-password")
            group_id=core.add_contact_group(db,member_id,"Colleague Private")
            member_contact=core.insert_contact(
                db,core.normalize_contact("Colleague","@colleague01","","",""),
                member_id,consent=True,
            )
            db.commit()
        with core.database() as db:
            with self.assertRaises(ValueError):
                core.set_contact_groups(db,self.owner_id,own_contact,[group_id])
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO contact_group_members(group_id,contact_id,added_at) VALUES (?,?,?)",
                    (owned,member_contact,core.now()),
                )
            # The DB trigger also rejects cross-owner changes to an existing link.
            core.set_contact_groups(db,self.owner_id,own_contact,[owned])
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE contact_group_members SET group_id=? WHERE group_id=? AND contact_id=?",
                    (group_id,owned,own_contact),
                )
        self.assertEqual(self.http.get(f"/contacts/groups/{group_id}").status_code,404)
        self.assertEqual(self.http.get(f"/compose?group_id={group_id}").status_code,404)
        self.assertEqual(self.http.get(f"/contacts?group_id={group_id}").status_code,404)
        self.assertEqual(self.post(f"/contacts/groups/{group_id}/delete",{}).status_code,404)
        rejected=self.post("/contacts",{
            "name":"Malicious","username":"@bad_group_user","group_ids":[str(group_id)],
        })
        self.assertEqual(rejected.status_code,303)
        with core.database() as db:
            self.assertIsNone(db.execute("SELECT id FROM contacts WHERE name='Malicious'").fetchone())
        with TestClient(app,raise_server_exceptions=True) as colleague:
            signin=colleague.post(
                "/login",data={"username":"colleague","password":"colleague-password"},
                follow_redirects=False,
            )
            self.assertEqual(signin.status_code,303)
            self.assertIn("Colleague Private",colleague.get("/contacts/groups").text)
            self.assertNotIn("Owner Private",colleague.get("/contacts/groups").text)

    def test_validation_and_csrf(self):
        gid=self.add_group("Valid")
        with core.database() as db:
            with self.assertRaisesRegex(ValueError,"Duplicate group"):
                core.set_contact_groups(db,self.owner_id,self.add_contact("A","@dup_group_a"),[gid,gid])
        self.assertEqual(self.post("/contacts/groups",{"name":"Bad;name"}).status_code,303)
        self.assertEqual(self.http.post(
            "/contacts/groups",data={"csrf":"bad","name":"Not created"},
            follow_redirects=False,
        ).status_code,403)
        with core.database() as db:
            self.assertEqual(len(core.contact_groups(db,self.owner_id)),1)


if __name__=="__main__":
    unittest.main()

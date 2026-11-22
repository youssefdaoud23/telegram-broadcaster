"""Consent and bulk archive operations never contact Telegram.

All tests run against temporary isolated SQLite and TestClient sessions.
"""
from __future__ import annotations

import os
import tempfile
import uuid
import unittest
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from broadcaster import core
from broadcaster.web import app

EVIDENCE = "Documented individual opt-in from each person in this selection"


class BulkConsentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        names=("DATABASE_PATH","APP_ENCRYPTION_KEY","TELEGRAM_SESSION_DIR",
               "COOKIE_SECURE","SEND_ENABLED")
        self.previous={name:os.environ.get(name) for name in names}
        os.environ.update({
            "DATABASE_PATH":str(Path(self.temp.name)/"consent.sqlite3"),
            "APP_ENCRYPTION_KEY":Fernet.generate_key().decode(),
            "TELEGRAM_SESSION_DIR":str(Path(self.temp.name)/"sessions"),
            "COOKIE_SECURE":"false",
            "SEND_ENABLED":"false",
        })
        core.init_db()
        core.create_admin("owner","test-password")
        with core.database() as db:
            core.create_user(db,"other","other-password")
            self.owner=db.execute("SELECT id FROM admins WHERE username='owner'").fetchone()["id"]
            self.other=db.execute("SELECT id FROM admins WHERE username='other'").fetchone()["id"]
            db.commit()
        self.client=TestClient(app,raise_server_exceptions=True)
        self.client.__enter__()
        r=self.client.post("/login",data={"username":"owner","password":"test-password"},
                           follow_redirects=False)
        self.assertEqual(r.status_code,303)
        with core.database() as db:
            self.csrf=core.session(db,self.client.cookies["tb_session"])["csrf"]
        self.sender=uuid.uuid4().hex

    def tearDown(self):
        self.client.__exit__(None,None,None)
        for key,value in self.previous.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value
        self.temp.cleanup()

    def contact(self,name,username,*,owner=None,consent=False,enabled=True):
        owner=self.owner if owner is None else owner
        with core.database() as db:
            cid=core.insert_contact(
                db,core.normalize_contact(name,username,"","",""),
                owner,consent=consent,enabled=enabled,
            )
            db.commit()
        return cid

    def group(self,name):
        with core.database() as db:
            gid=core.add_contact_group(db,self.owner,name)
            db.commit()
        return gid

    def membership(self,cid,groups):
        with core.database() as db:
            core.set_contact_groups(db,self.owner,cid,groups)
            db.commit()

    def consent(self,ids=(),*,action="grant",evidence=EVIDENCE,
                verified="yes",scope="selected",group=None,csrf=None):
        fields={
            "csrf":self.csrf if csrf is None else csrf,
            "action":action,"scope":scope,"evidence":evidence,
            "verified":verified,
        }
        if scope=="group":fields["group_id"]=str(group)
        else:fields["contacts"]=[str(i) for i in ids]
        return self.client.post("/contacts/bulk-consent",data=fields,
                                follow_redirects=False)

    def consent_map(self):
        with core.database() as db:
            return {row["id"]:row["consent"] for row in db.execute(
                "SELECT id,consent FROM contacts WHERE owner_id=?",(self.owner,)
            )}

    def events(self):
        with core.database() as db:
            return db.execute(
                "SELECT * FROM contact_consent_events WHERE owner_id=? ORDER BY id",
                (self.owner,),
            ).fetchall()

    def test_selection_grant_requires_individual_evidence_and_verification(self):
        alice=self.contact("Alice","@bulk_alice1")
        bob=self.contact("Bob","@bulk_bobby1")
        for verified,evidence in (("",EVIDENCE),("yes",""),("yes","group membership"),("no",EVIDENCE)):
            response=self.consent([alice,bob],verified=verified,evidence=evidence)
            self.assertEqual(response.status_code,303)
            self.assertEqual(self.consent_map(),{alice:0,bob:0})
            self.assertEqual(len(self.events()),0)
        response=self.consent([alice,bob])
        self.assertEqual(response.status_code,303)
        self.assertEqual(self.consent_map(),{alice:1,bob:1})
        audit=self.events()
        self.assertEqual(len(audit),2)
        self.assertTrue(all(row["actor_id"]==self.owner and row["owner_id"]==self.owner
                            and row["source"]=="selected_contacts"
                            and row["evidence"]==EVIDENCE
                            and row["previous_consent"]==0
                            and row["new_consent"]==1 for row in audit))
        # Reapplying must not multiply consent events.
        self.consent([alice,bob])
        self.assertEqual(len(self.events()),2)
        view=self.client.get("/contacts/consent-history")
        self.assertEqual(view.status_code,200)
        self.assertIn(EVIDENCE,view.text)
        self.assertIn("Alice",view.text)

    def test_withdraw_for_selected_contacts_requires_no_positive_attestation(self):
        alice=self.contact("Alice","@bulk_alice1",consent=True)
        bob=self.contact("Bob","@bulk_bobby1",consent=True)
        result=self.consent([alice,bob],action="withdraw",verified="",evidence="")
        self.assertEqual(result.status_code,303)
        self.assertEqual(self.consent_map(),{alice:0,bob:0})
        audit=self.events()
        self.assertEqual(len(audit),2)
        self.assertTrue(all(row["new_consent"]==0 and row["previous_consent"]==1
                            for row in audit))
        self.assertIn("withdrawn",self.client.get("/contacts/consent-history").text.lower())
        self.consent([alice,bob],action="withdraw",verified="",evidence="")
        self.assertEqual(len(self.events()),2)

    def test_whole_group_consent_does_not_include_unrelated_or_archived_contacts(self):
        group=self.group("Seven customers")
        other=self.group("Overlapping")
        c1=self.contact("One","@bulk_one12")
        c2=self.contact("Two","@bulk_two12")
        unrelated=self.contact("Elsewhere","@bulk_elsewhere")
        self.membership(c1,[group,other])
        self.membership(c2,[group])
        r=self.consent(scope="group",group=group)
        self.assertEqual(r.status_code,303)
        self.assertEqual(self.consent_map(),{c1:1,c2:1,unrelated:0})
        events=self.events()
        self.assertEqual(len(events),2)
        self.assertTrue(all(row["source"]==f"whole_group:{group}" for row in events))
        # Consent is per person globally, not independently per group.
        self.consent(scope="group",group=group,action="withdraw",verified="",evidence="")
        self.assertEqual(self.consent_map(),{c1:0,c2:0,unrelated:0})
        with core.database() as db:
            self.assertEqual(core.contact_group_map(db,self.owner)[c1],{group,other})

    def test_invalid_or_cross_workspace_ids_do_not_modify_anything(self):
        own=self.contact("Alice","@bulk_alice1")
        foreign=self.contact("Other","@bulk_foreign",owner=self.other)
        failed=self.consent([own,foreign])
        self.assertEqual(failed.status_code,303)
        self.assertEqual(self.consent_map()[own],0)
        self.assertFalse(self.events())
        self.consent([own,own])
        self.assertFalse(self.events())
        self.consent([own],csrf="wrong")
        self.assertFalse(self.events())
        with core.database() as db:
            gid=core.add_contact_group(db,self.other,"Foreign")
            db.commit()
        forged=self.consent(scope="group",group=gid)
        self.assertEqual(forged.status_code,303)
        self.assertFalse(self.events())
        self.assertEqual(self.consent_map()[own],0)

    def test_malformed_scope_and_group_id_fail_without_side_effects(self):
        alice=self.contact("Alice","@bulk_alice1")
        for data in (
            {"scope":"group","group_id":"not-a-number","action":"grant"},
            {"scope":"group","group_id":"","action":"grant"},
            {"scope":"invalid","group_id":"22","action":"grant"},
        ):
            response=self.client.post("/contacts/bulk-consent",
                data={"csrf":self.csrf,"verified":"yes","evidence":EVIDENCE,**data},
                follow_redirects=False)
            self.assertEqual(response.status_code,303)
        self.assertEqual(self.consent_map()[alice],0)
        self.assertFalse(self.events())

    def test_selected_archive_preserves_delivery_snapshots(self):
        a=self.contact("Alice","@bulk_alice1",consent=True)
        b=self.contact("Bob","@bulk_bobby1",consent=True)
        with core.database() as db:
            db.execute(
                "INSERT INTO accounts (id,label,api_id,api_hash_enc,phone_enc,"
                "session_name,state,created_at,updated_at,owner_id) "
                "VALUES (?,'Test sender',333,?,?,?,'connected',?,?,?)",
                (self.sender,core.encrypt("hash"),core.encrypt("+420700000000"),
                 self.sender,core.now(),core.now(),self.owner),
            )
            campaign=core.create_campaign(db,self.sender,"Hello",[a,b],None,self.owner)
            db.commit()
        response=self.client.post("/contacts/bulk-remove",data={
            "csrf":self.csrf,"contacts":[str(a),str(b)]
        },follow_redirects=False)
        self.assertEqual(response.status_code,303)
        with core.database() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM contacts WHERE archived=1 AND enabled=0"
            ).fetchone()[0],2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM deliveries WHERE campaign_id=?",
                                        (campaign,)).fetchone()[0],2)
            self.assertIsNotNone(db.execute("SELECT id FROM campaigns WHERE id=?",(campaign,)).fetchone())
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM contacts WHERE archived=0 AND consent=1"
            ).fetchone()[0],0)

    def test_archive_invalid_selection_is_atomic(self):
        a=self.contact("Alice","@bulk_alice1")
        other=self.contact("Outside","@bulk_other11",owner=self.other)
        for ids in ([a,other],[a,a],[],[a,999999]):
            response=self.client.post("/contacts/bulk-remove",
                data={"csrf":self.csrf,"contacts":[str(x) for x in ids]},
                follow_redirects=False)
            self.assertEqual(response.status_code,303)
            with core.database() as db:
                self.assertEqual(db.execute(
                    "SELECT archived FROM contacts WHERE id=?",(a,)
                ).fetchone()[0],0)
        self.assertEqual(self.client.post("/contacts/bulk-remove",
            data={"csrf":"bad","contacts":[str(a)]},follow_redirects=False).status_code,403)

    def test_ui_controls_work_with_no_groups_and_show_active_group_controls(self):
        cid=self.contact("Alice","@bulk_alice1")
        page=self.client.get("/contacts")
        self.assertEqual(page.status_code,200)
        self.assertIn('id="bulk-select-all"',page.text)
        self.assertIn('name="contacts"',page.text)
        self.assertIn('action="/contacts/bulk-remove"',page.text)
        self.assertIn('action="/contacts/bulk-consent"',page.text)
        self.assertIn("Record verified opt-in",page.text)
        self.assertIn("Remove selected contacts",page.text)
        group=self.group("All contacts")
        self.membership(cid,[group])
        page=self.client.get(f"/contacts/groups/{group}")
        self.assertIn("Group Active status",page.text)
        self.assertIn(f'action="/contacts/groups/{group}/status"',page.text)
        self.assertIn("Set entire group Active",page.text)
        self.assertIn("Set entire group Inactive",page.text)
        self.assertNotIn("Consent for the entire group",page.text)

    def test_consent_updates_affect_queued_and_drafted_targets(self):
        a=self.contact("Alice","@bulk_alice1",consent=True)
        with core.database() as db:
            db.execute(
                "INSERT INTO accounts (id,label,api_id,api_hash_enc,phone_enc,"
                "session_name,state,created_at,updated_at,owner_id) "
                "VALUES (?,'Test sender',333,?,?,?,'connected',?,?,?)",
                (self.sender,core.encrypt("hash"),core.encrypt("+420700000000"),
                 self.sender,core.now(),core.now(),self.owner),
            )
            cid=core.create_campaign(db,self.sender,"Draft message",[a],None,self.owner)
            db.commit()
        self.consent([a],action="withdraw",verified="",evidence="User opted out")
        with core.database() as db:
            with self.assertRaisesRegex(ValueError,"opted in"):
                core.campaign_targets(db,self.sender,"Another message",[a],None,self.owner)
            self.assertEqual(db.execute(
                "SELECT consent FROM contacts WHERE id=?",(a,)
            ).fetchone()[0],0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM deliveries WHERE campaign_id=?",(cid,)
            ).fetchone()[0],1)


if __name__=="__main__":
    unittest.main()

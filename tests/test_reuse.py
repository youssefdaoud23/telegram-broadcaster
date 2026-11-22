"""Offline regression coverage for reusing history and editing unsent drafts."""
from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from broadcaster import core
from broadcaster.web import app


class BroadcastReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old={k:os.environ.get(k) for k in (
            "DATABASE_PATH","APP_ENCRYPTION_KEY","TELEGRAM_SESSION_DIR",
            "SEND_ENABLED","SEND_MAX_RECIPIENTS","COOKIE_SECURE",
        )}
        os.environ.update({
            "DATABASE_PATH":str(Path(self.temp.name)/"reuse.sqlite3"),
            "APP_ENCRYPTION_KEY":Fernet.generate_key().decode(),
            "TELEGRAM_SESSION_DIR":str(Path(self.temp.name)/"sessions"),
            "SEND_ENABLED":"false",
            "COOKIE_SECURE":"false",
        })
        os.environ.pop("SEND_MAX_RECIPIENTS",None)
        core.init_db()
        core.create_admin("owner","owner-password")
        self.patch_session=patch("broadcaster.web.telegram.refresh_connected_accounts",
                                 return_value=[])
        self.patch_session.start()
        self.client=TestClient(app,raise_server_exceptions=True)
        self.client.__enter__()
        login=self.client.post("/login",
                               data={"username":"owner","password":"owner-password"},
                               follow_redirects=False)
        self.assertEqual(login.status_code,303)
        with core.database() as db:
            session=core.session(db,self.client.cookies.get("tb_session"))
            self.owner_id=session["admin_id"]
            self.csrf=session["csrf"]
            self.sender=uuid.uuid4().hex
            db.execute(
                "INSERT INTO accounts(id,label,api_id,api_hash_enc,phone_enc,"
                "session_name,state,created_at,updated_at,owner_id) "
                "VALUES (?, 'Offline sender', 123456, ?, ?, ?, 'connected', ?, ?, ?)",
                (self.sender,core.encrypt("dummyhash"),
                 core.encrypt("+420123456789"),self.sender,
                 core.now(),core.now(),self.owner_id),
            )
            self.a=core.insert_contact(db,core.normalize_contact(
                "Alice","@alicebroadcast","","",""),self.owner_id,consent=True)
            self.b=core.insert_contact(db,core.normalize_contact(
                "Bob","@bobbroadcast","","",""),self.owner_id,consent=True)
            self.c=core.insert_contact(db,core.normalize_contact(
                "Carol","@carolbroadcast","","",""),self.owner_id,consent=True)
            self.original=core.create_campaign(
                db,self.sender,"Original campaign message",[self.a,self.b,self.c],
                None,self.owner_id,
            )
            db.execute("UPDATE campaigns SET state='finished' WHERE id=?",(self.original,))
            db.execute(
                "UPDATE deliveries SET state='sent' WHERE campaign_id=? AND contact_id=?",
                (self.original,self.a),
            )
            db.execute(
                "UPDATE deliveries SET state='failed' WHERE campaign_id=? AND contact_id=?",
                (self.original,self.b),
            )
            db.execute(
                "UPDATE deliveries SET state='unknown' WHERE campaign_id=? AND contact_id=?",
                (self.original,self.c),
            )
            db.commit()

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.patch_session.stop()
        for k,v in self.old.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        self.temp.cleanup()

    def test_reuse_history_prefills_all_eligible_and_warns_about_prior_sends(self):
        response=self.client.get(f"/compose?from_campaign={self.original}")
        self.assertEqual(response.status_code,200)
        self.assertIn("Original campaign message",response.text)
        self.assertIn("previously sent contacts",response.text)
        self.assertIn(f'name="reuse_source_id" value="{self.original}"',response.text)
        self.assertIn(f'value="{self.sender}" selected',response.text)
        for contact in (self.a,self.b,self.c):
            self.assertIn(f'value="{contact}" checked',response.text)
        self.assertIn("Nothing sends automatically",response.text)
        self.assertIn('data-reuse-preset="pending"',response.text)
        self.assertIn('data-reuse-preset="failed"',response.text)
        self.assertIn('data-reuse-preset="sent"',response.text)
        self.assertIn('data-reuse-preset="unknown"',response.text)
        self.assertIn(f'value="{self.a}" checked data-previous-state="sent"',response.text)
        self.assertIn(f'value="{self.b}" checked data-previous-state="failed"',response.text)
        self.assertIn(f'value="{self.c}" checked data-previous-state="unknown"',response.text)
        history=self.client.get("/campaigns")
        self.assertIn(f"/compose?from_campaign={self.original}",history.text)
        details=self.client.get(f"/campaigns/{self.original}")
        self.assertIn("Reuse and edit as a new broadcast",details.text)
        with core.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0],1)

    def test_resend_creates_new_editable_draft_without_mutating_history_or_sending(self):
        with patch("broadcaster.web.telegram.enqueue",
                   side_effect=AssertionError("No Telegram messages allowed")):
            result=self.client.post(
                "/compose/preview",
                data={"csrf":self.csrf,"account_id":self.sender,
                      "message":"Changed message for Bob only",
                      "contacts":[str(self.b)],
                      "reuse_source_id":self.original},
                follow_redirects=False,
            )
        self.assertEqual(result.status_code,303)
        created=result.headers["location"].split("?",1)[0].split("/")[-1]
        with core.database() as db:
            source,source_deliveries=core.campaign_summary(
                db,self.original,self.owner_id,
            )
            copy,copy_deliveries=core.campaign_summary(db,created,self.owner_id)
            link=db.execute(
                "SELECT source_campaign_id FROM campaign_reuses WHERE new_campaign_id=?",
                (created,),
            ).fetchone()
        self.assertEqual(source["state"],"finished")
        self.assertEqual(source["message"],"Original campaign message")
        self.assertEqual([d["state"] for d in source_deliveries],["sent","failed","unknown"])
        self.assertEqual(copy["state"],"draft")
        self.assertEqual(copy["message"],"Changed message for Bob only")
        self.assertEqual([(d["contact_id"],d["state"]) for d in copy_deliveries],
                         [(self.b,"pending")])
        self.assertEqual(link["source_campaign_id"],self.original)
        self.assertIn(self.original,self.client.get(f"/campaigns/{created}").text)
        self.assertEqual(self.client.get(f"/campaigns/{created}/edit").status_code,200)

    def test_edit_draft_updates_message_sender_and_recipients_atomically(self):
        with core.database() as db:
            draft=core.create_campaign(
                db,self.sender,"Old draft",[self.a,self.b],None,self.owner_id
            )
            db.commit()
        page=self.client.get(f"/campaigns/{draft}/edit")
        self.assertEqual(page.status_code,200)
        self.assertIn("Old draft",page.text)
        self.assertIn(f'value="{self.a}" checked',page.text)
        updated=self.client.post(
            f"/campaigns/{draft}/edit",
            data={"csrf":self.csrf,"account_id":self.sender,
                  "message":"New private message","contacts":[str(self.c)]},
            follow_redirects=False,
        )
        self.assertEqual(updated.status_code,303)
        with core.database() as db:
            item,deliveries=core.campaign_summary(db,draft,self.owner_id)
        self.assertEqual(item["state"],"draft")
        self.assertEqual(item["message"],"New private message")
        self.assertEqual(item["total"],1)
        self.assertEqual([(d["contact_id"],d["state"]) for d in deliveries],
                         [(self.c,"pending")])

    def test_submitted_or_unknown_drafts_are_not_editable(self):
        self.assertEqual(
            self.client.get(f"/campaigns/{self.original}/edit").status_code,409
        )
        response=self.client.post(
            f"/campaigns/{self.original}/edit",
            data={"csrf":self.csrf,"account_id":self.sender,
                  "message":"Tampered","contacts":[str(self.a)]},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code,303)
        with core.database() as db:
            row=db.execute(
                "SELECT state,message FROM campaigns WHERE id=?",(self.original,)
            ).fetchone()
        self.assertEqual((row["state"],row["message"]),
                         ("finished","Original campaign message"))

    def test_disabled_contacts_not_preselected_and_can_not_be_reused(self):
        with core.database() as db:
            db.execute("UPDATE contacts SET consent=0 WHERE id=?",(self.a,))
            db.commit()
        page=self.client.get(f"/compose?from_campaign={self.original}")
        self.assertNotIn(f'value="{self.a}" checked',page.text)
        result=self.client.post(
            "/compose/preview",
            data={"csrf":self.csrf,"account_id":self.sender,
                  "message":"Do not send","contacts":[str(self.a)],
                  "reuse_source_id":self.original},
            follow_redirects=False,
        )
        self.assertEqual(result.status_code,303)
        self.assertTrue(result.headers["location"].startswith("/compose"))
        with core.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0],1)

    def test_cross_user_reuse_and_draft_edit_are_forbidden(self):
        core.create_admin("colleague","colleague-password")
        member=TestClient(app,raise_server_exceptions=True)
        member.__enter__()
        try:
            signed_in=member.post(
                "/login",data={"username":"colleague","password":"colleague-password"},
                follow_redirects=False,
            )
            self.assertEqual(signed_in.status_code,303)
            with core.database() as db:
                csrf=core.session(db,member.cookies.get("tb_session"))["csrf"]
            self.assertEqual(member.get(
                f"/compose?from_campaign={self.original}").status_code,404)
            self.assertEqual(member.get(
                f"/campaigns/{self.original}/edit").status_code,404)
            forbidden=member.post(
                "/compose/preview",
                data={"csrf":csrf,"account_id":self.sender,
                      "message":"No access","contacts":[str(self.a)],
                      "reuse_source_id":self.original},
                follow_redirects=False,
            )
            self.assertEqual(forbidden.status_code,404)
            forbidden_edit=member.post(
                f"/campaigns/{self.original}/edit",
                data={"csrf":csrf,"account_id":self.sender,
                      "message":"No access","contacts":[str(self.a)]},
                follow_redirects=False,
            )
            self.assertEqual(forbidden_edit.status_code,404)
            with core.database() as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0],1
                )
        finally:
            member.__exit__(None,None,None)
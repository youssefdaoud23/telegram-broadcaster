"""Session status regression tests - fully offline, never send a Telegram message."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from broadcaster import core, telegram
from broadcaster.web import app


class SessionStateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        keys=("DATABASE_PATH","APP_ENCRYPTION_KEY","TELEGRAM_SESSION_DIR",
              "SEND_ENABLED","SEND_MAX_RECIPIENTS","COOKIE_SECURE")
        self.old={k:os.environ.get(k) for k in keys}
        os.environ.update({
            "DATABASE_PATH":str(Path(self.temp.name)/"sessions.sqlite3"),
            "APP_ENCRYPTION_KEY":Fernet.generate_key().decode(),
            "TELEGRAM_SESSION_DIR":str(Path(self.temp.name)/"sessions"),
            "SEND_ENABLED":"false",
            "COOKIE_SECURE":"false",
        })
        os.environ.pop("SEND_MAX_RECIPIENTS",None)
        core.init_db()
        core.create_admin("owner","testpassword")
        with core.database() as db:
            self.owner_id=db.execute("SELECT id FROM admins LIMIT 1").fetchone()[0]
        self.account_id=uuid.uuid4().hex
        with core.database() as db:
            db.execute(
                "INSERT INTO accounts(id,label,api_id,api_hash_enc,phone_enc,session_name,"
                "expected_username,username,state,created_at,updated_at,owner_id) "
                "VALUES (?,?,?,?,?,?,?,?, 'connected',?,?,?)",
                (self.account_id,"Test account",123456,core.encrypt("hash"),
                 core.encrypt("+420123456789"),self.account_id,
                 "@testuser12","@testuser12",core.now(),core.now(),self.owner_id),
            )
            self.contact_id=core.insert_contact(
                db,core.normalize_contact("Test contact","@testrecipient12","","",""),
                self.owner_id,consent=True,
            )
            db.commit()
        telegram._status_checks.clear()
        self.http=TestClient(app,raise_server_exceptions=True)
        self.http.__enter__()
        r=self.http.post("/login",data={"username":"owner","password":"testpassword"},
                         follow_redirects=False)
        self.assertEqual(r.status_code,303)
        with core.database() as db:
            self.csrf=core.session(db,self.http.cookies["tb_session"])["csrf"]

    def tearDown(self):
        self.http.__exit__(None,None,None)
        telegram._status_checks.clear()
        for k,v in self.old.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        self.temp.cleanup()

    def client(self,*,authorized=True,network_failure=False):
        fake=SimpleNamespace()
        fake.connect=AsyncMock(side_effect=OSError("transient") if network_failure else None)
        fake.is_user_authorized=AsyncMock(return_value=authorized)
        fake.get_me=AsyncMock(return_value=SimpleNamespace(
            username="testuser12",first_name="Test",
        ))
        fake.disconnect=AsyncMock()
        fake.send_message=AsyncMock(side_effect=AssertionError("Never send in tests"))
        return fake

    def account_state(self):
        with core.database() as db:
            return db.execute(
                "SELECT state,last_error,username FROM accounts WHERE id=?",
                (self.account_id,),
            ).fetchone()

    def test_automatic_refresh_marks_revoked_session_disconnected(self):
        fake=self.client(authorized=False)
        with patch("broadcaster.telegram.new_client",return_value=fake):
            res=self.http.get("/accounts")
        self.assertEqual(res.status_code,200)
        self.assertIn("disconnected",res.text.lower())
        self.assertIn("Reconnection required",res.text)
        self.assertIn("/accounts/refresh",res.text)
        self.assertEqual(self.account_state()["state"],"disconnected")
        self.assertIn("revoked",self.account_state()["last_error"])
        self.assertGreaterEqual(fake.is_user_authorized.await_count,1)
        fake.send_message.assert_not_awaited()
        # A disconnected sender is excluded from new broadcasts.
        compose=self.http.get("/compose")
        self.assertEqual(compose.status_code,200)
        self.assertNotIn('value="'+self.account_id+'"',compose.text)
        setup=self.http.get(f"/accounts/{self.account_id}/setup")
        self.assertEqual(setup.status_code,200)
        self.assertIn("Send / resend Telegram code",setup.text)

    def test_explicit_verify_saves_revocation_without_sending(self):
        fake=self.client(authorized=False)
        with patch("broadcaster.telegram.new_client",return_value=fake):
            response=self.http.post(
                f"/accounts/{self.account_id}/verify",
                data={"csrf":self.csrf},follow_redirects=False,
            )
        self.assertEqual(response.status_code,303)
        self.assertIn("revoked",response.headers["location"].lower())
        self.assertEqual(self.account_state()["state"],"disconnected")
        fake.send_message.assert_not_awaited()

    def test_transient_connection_error_keeps_existing_status(self):
        fake=self.client(network_failure=True)
        with patch("broadcaster.telegram.new_client",return_value=fake):
            page=self.http.get("/accounts")
        self.assertEqual(page.status_code,200)
        self.assertIn("Some Telegram sessions could not be checked",page.text)
        self.assertEqual(self.account_state()["state"],"connected")
        self.assertEqual(self.account_state()["last_error"],"")

    def test_manual_refresh_bypasses_recent_success_cache(self):
        ok=self.client(authorized=True)
        revoked=self.client(authorized=False)
        with patch("broadcaster.telegram.new_client",return_value=ok):
            self.assertEqual(self.http.get("/accounts").status_code,200)
        self.assertEqual(self.account_state()["state"],"connected")
        with patch("broadcaster.telegram.new_client",return_value=revoked):
            r=self.http.post("/accounts/refresh",
                             data={"csrf":self.csrf},follow_redirects=False)
        self.assertEqual(r.status_code,303)
        self.assertEqual(self.account_state()["state"],"disconnected")
        revoked.is_user_authorized.assert_awaited()
        revoked.send_message.assert_not_awaited()

    def test_get_me_none_treats_session_as_revoked(self):
        fake=self.client(authorized=True)
        fake.get_me=AsyncMock(return_value=None)
        with patch("broadcaster.telegram.new_client",return_value=fake):
            with self.assertRaisesRegex(telegram.TelegramProblem,"revoked"):
                asyncio.run(telegram.verify_connected(self.account_id))
        self.assertEqual(self.account_state()["state"],"disconnected")

    def test_revoked_auth_key_error_marks_account_disconnected(self):
        from telethon import errors
        fake=self.client(authorized=True)
        fake.is_user_authorized=AsyncMock(side_effect=errors.AuthKeyUnregisteredError(None))
        with patch("broadcaster.telegram.new_client",return_value=fake):
            with self.assertRaisesRegex(telegram.TelegramProblem,"revoked"):
                asyncio.run(telegram.verify_connected(self.account_id))
        self.assertEqual(self.account_state()["state"],"disconnected")
        fake.send_message.assert_not_awaited()

    def test_old_draft_cannot_send_from_disconnected_account(self):
        with core.database() as db:
            campaign=core.create_campaign(
                db,self.account_id,"Never send",[self.contact_id],None,self.owner_id
            )
            db.commit()
        fake=self.client(authorized=False)
        with patch("broadcaster.telegram.new_client",return_value=fake):
            self.assertEqual(self.http.get("/accounts").status_code,200)
        self.assertEqual(self.account_state()["state"],"disconnected")
        os.environ["SEND_ENABLED"]="true"
        with patch("broadcaster.web.telegram.enqueue",
                   side_effect=AssertionError("Do not enqueue")) as enqueue:
            response=self.http.post(
                f"/campaigns/{campaign}/send",
                data={"csrf":self.csrf}, follow_redirects=False,
            )
            self.assertEqual(response.status_code,303)
            self.assertIn("Sender%20session%20is%20disconnected",response.headers["location"])
            enqueue.assert_not_called()
        with core.database() as db:
            state=db.execute(
                "SELECT state FROM campaigns WHERE id=?",(campaign,)
            ).fetchone()["state"]
        self.assertEqual(state,"draft")
        fake.send_message.assert_not_awaited()

    def test_worker_stops_before_sending_if_session_revoked(self):
        with core.database() as db:
            campaign=core.create_campaign(
                db,self.account_id,"Do not send",[self.contact_id],None,self.owner_id
            )
            db.execute("UPDATE campaigns SET state='queued' WHERE id=?",(campaign,))
            db.commit()
        fake=self.client(authorized=False)
        os.environ["SEND_ENABLED"]="true"
        with patch("broadcaster.telegram.new_client",return_value=fake):
            asyncio.run(telegram.dispatch(campaign))
        self.assertEqual(self.account_state()["state"],"disconnected")
        fake.send_message.assert_not_awaited()
        with core.database() as db:
            self.assertEqual(
                db.execute("SELECT state FROM campaigns WHERE id=?",(campaign,)).fetchone()[0],
                "failed",
            )
"""Consent side doors, spam-restriction stop and per-address login throttling.

Fully offline: Telegram clients are fakes and no message is ever sent.
"""
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
from starlette.requests import Request
from telethon import errors, types

from broadcaster import core, telegram
from broadcaster.web import app, client_ip


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        keys = ("DATABASE_PATH", "APP_ENCRYPTION_KEY", "TELEGRAM_SESSION_DIR",
                "SEND_ENABLED", "SEND_MAX_RECIPIENTS", "COOKIE_SECURE", "CLIENT_IP_HEADER")
        self.old = {k: os.environ.get(k) for k in keys}
        os.environ.update({
            "DATABASE_PATH": str(Path(self.temp.name) / "hardening.sqlite3"),
            "APP_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "TELEGRAM_SESSION_DIR": str(Path(self.temp.name) / "sessions"),
            "SEND_ENABLED": "false",
            "COOKIE_SECURE": "false",
        })
        os.environ.pop("SEND_MAX_RECIPIENTS", None)
        os.environ.pop("CLIENT_IP_HEADER", None)
        core.init_db()
        core.create_admin("owner", "testpassword")
        with core.database() as db:
            self.owner_id = db.execute("SELECT id FROM admins LIMIT 1").fetchone()[0]
        self.clients: list[TestClient] = []
        self.http = self.client()
        self.csrf = self.sign_in(self.http)

    def tearDown(self):
        for http in self.clients:
            http.__exit__(None, None, None)
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.temp.cleanup()

    def client(self, host: str = "testclient") -> TestClient:
        http = TestClient(app, raise_server_exceptions=True, client=(host, 50000))
        http.__enter__()
        self.clients.append(http)
        return http

    def sign_in(self, http: TestClient, password: str = "testpassword") -> str:
        r = http.post("/login", data={"username": "owner", "password": password},
                      follow_redirects=False)
        self.assertEqual(r.headers["location"], "/")
        with core.database() as db:
            return core.session(db, http.cookies["tb_session"])["csrf"]

    def login_location(self, http: TestClient, password: str) -> str:
        return http.post("/login", data={"username": "owner", "password": password},
                         follow_redirects=False).headers["location"]

    def contact(self, username: str):
        with core.database() as db:
            return db.execute("SELECT * FROM contacts WHERE username=?", (username,)).fetchone()

    def consent_events(self):
        with core.database() as db:
            return db.execute(
                "SELECT previous_consent,new_consent,evidence,source "
                "FROM contact_consent_events ORDER BY id"
            ).fetchall()

    # --- #2: consent can only be granted with a recorded reason ---------------

    def test_add_form_requires_reason_to_grant_and_records_history(self):
        rejected = self.http.post("/contacts", data={
            "csrf": self.csrf, "name": "No Reason", "username": "@no_reason_1",
            "consent": "yes",
        }, follow_redirects=False)
        self.assertIn("record%20how%20it%20was%20obtained", rejected.headers["location"])
        self.assertIsNone(self.contact("@no_reason_1"))

        for weak in ("", "short", "Telegram group membership"):
            self.http.post("/contacts", data={
                "csrf": self.csrf, "name": "Weak", "username": "@weak_reason_1",
                "consent": "yes", "consent_evidence": weak,
            }, follow_redirects=False)
            self.assertIsNone(self.contact("@weak_reason_1"))

        self.http.post("/contacts", data={
            "csrf": self.csrf, "name": "With Reason", "username": "@with_reason_1",
            "consent": "yes", "consent_evidence": "Signed paper opt-in form 3 Nov",
        }, follow_redirects=False)
        self.assertEqual(self.contact("@with_reason_1")["consent"], 1)
        events = self.consent_events()
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["previous_consent"], events[0]["new_consent"],
                          events[0]["source"]), (0, 1, "contact_form"))
        self.assertEqual(events[0]["evidence"], "Signed paper opt-in form 3 Nov")

    def test_add_form_without_consent_needs_no_reason_and_writes_no_history(self):
        self.http.post("/contacts", data={
            "csrf": self.csrf, "name": "Plain", "username": "@plain_contact1",
        }, follow_redirects=False)
        self.assertEqual(self.contact("@plain_contact1")["consent"], 0)
        self.assertEqual(self.consent_events(), [])

    def test_edit_form_grant_needs_reason_and_both_directions_are_recorded(self):
        self.http.post("/contacts", data={
            "csrf": self.csrf, "name": "Edit Me", "username": "@edit_consent1",
        }, follow_redirects=False)
        cid = self.contact("@edit_consent1")["id"]
        base = {"csrf": self.csrf, "name": "Edit Me", "username": "@edit_consent1",
                "enabled": "yes"}

        self.http.post(f"/contacts/{cid}/edit", data={**base, "consent": "yes"},
                       follow_redirects=False)
        self.assertEqual(self.contact("@edit_consent1")["consent"], 0)
        self.assertEqual(self.consent_events(), [])

        self.http.post(f"/contacts/{cid}/edit", data={
            **base, "consent": "yes", "consent_evidence": "Replied YES to opt-in on 4 Nov",
        }, follow_redirects=False)
        self.assertEqual(self.contact("@edit_consent1")["consent"], 1)

        # Saving again with consent unchanged needs no reason and adds no event.
        self.http.post(f"/contacts/{cid}/edit", data={**base, "consent": "yes"},
                       follow_redirects=False)
        self.assertEqual(len(self.consent_events()), 1)

        self.http.post(f"/contacts/{cid}/edit", data=base, follow_redirects=False)
        self.assertEqual(self.contact("@edit_consent1")["consent"], 0)
        events = self.consent_events()
        self.assertEqual([(e["previous_consent"], e["new_consent"]) for e in events],
                         [(0, 1), (1, 0)])
        self.assertEqual(events[1]["evidence"], "Consent withdrawn in contact edit form")

    def test_csv_opt_in_column_never_grants_consent(self):
        raw = ("Name,Username,Opt In,Consent\n"
               "Csv Person,@csv_optin_one,yes,yes\n").encode()
        result = self.http.post("/contacts/import", data={"csrf": self.csrf},
                                files={"file": ("c.csv", raw, "text/csv")},
                                follow_redirects=False)
        self.assertIn("CSV%20Opt%20In%20is%20not%20imported", result.headers["location"])
        self.assertEqual(self.contact("@csv_optin_one")["consent"], 0)
        self.assertEqual(self.consent_events(), [])

    # --- #3: a spam restriction stops the whole broadcast -----------------------

    def _queued_campaign(self, count: int):
        account_id = uuid.uuid4().hex
        with core.database() as db:
            db.execute(
                "INSERT INTO accounts(id,label,api_id,api_hash_enc,phone_enc,session_name,"
                "expected_username,username,state,created_at,updated_at,owner_id) "
                "VALUES (?,?,?,?,?,?,?,?, 'connected',?,?,?)",
                (account_id, "Sender", 123456, core.encrypt("hash"),
                 core.encrypt("+420123456789"), account_id, "@sender_acct1",
                 "@sender_acct1", core.now(), core.now(), self.owner_id),
            )
            ids = [core.insert_contact(
                db, core.normalize_contact(f"R{i}", f"@recipient_{i:03d}", "", "", ""),
                self.owner_id, consent=True) for i in range(count)]
            campaign = core.create_campaign(db, account_id, "Hello", ids, None, self.owner_id)
            db.execute("UPDATE campaigns SET state='queued' WHERE id=?", (campaign,))
            db.commit()
        return campaign

    def _fake_sender(self, send_error: Exception):
        fake = SimpleNamespace()
        fake.connect = AsyncMock()
        fake.disconnect = AsyncMock()
        fake.is_user_authorized = AsyncMock(return_value=True)
        fake.get_me = AsyncMock(return_value=SimpleNamespace(username="sender_acct1"))
        fake.get_entity = AsyncMock(return_value=types.User(id=777000))
        fake.send_message = AsyncMock(side_effect=send_error)
        return fake

    def _deliveries(self, campaign):
        with core.database() as db:
            state = db.execute("SELECT state FROM campaigns WHERE id=?", (campaign,)).fetchone()[0]
            rows = db.execute("SELECT state,error FROM deliveries WHERE campaign_id=? ORDER BY id",
                              (campaign,)).fetchall()
        return state, rows

    def test_peer_flood_pauses_broadcast_without_trying_other_recipients(self):
        campaign = self._queued_campaign(3)
        fake = self._fake_sender(errors.PeerFloodError(request=None))
        os.environ["SEND_ENABLED"] = "true"
        with patch("broadcaster.telegram.new_client", return_value=fake), \
                patch("broadcaster.telegram.asyncio.sleep", AsyncMock()):
            asyncio.run(telegram.dispatch(campaign))
        self.assertEqual(fake.send_message.await_count, 1)
        state, rows = self._deliveries(campaign)
        self.assertEqual(state, "paused")
        self.assertEqual(rows[0]["state"], "blocked")
        self.assertIn("PEER_FLOOD", rows[0]["error"])
        self.assertEqual([r["state"] for r in rows[1:]], ["pending", "pending"])

    def test_flood_wait_still_pauses_broadcast(self):
        campaign = self._queued_campaign(2)
        fake = self._fake_sender(errors.FloodWaitError(request=None, capture=30))
        os.environ["SEND_ENABLED"] = "true"
        with patch("broadcaster.telegram.new_client", return_value=fake), \
                patch("broadcaster.telegram.asyncio.sleep", AsyncMock()):
            asyncio.run(telegram.dispatch(campaign))
        self.assertEqual(fake.send_message.await_count, 1)
        state, rows = self._deliveries(campaign)
        self.assertEqual(state, "paused")
        self.assertEqual([r["state"] for r in rows], ["blocked", "pending"])

    # --- #4: lockout is per address, not global per username --------------------

    def test_guesses_from_one_address_do_not_lock_out_another(self):
        attacker = self.client("198.51.100.66")
        for _ in range(core.LOGIN_MAX_PER_USER_IP):
            self.assertIn("Incorrect", self.login_location(attacker, "wrong-password"))
        self.assertIn("Too%20many", self.login_location(attacker, "testpassword"))
        owner = self.client("203.0.113.10")
        self.assertEqual(self.login_location(owner, "testpassword"), "/")

    def test_one_address_trying_many_usernames_is_capped(self):
        sprayer = self.client("198.51.100.77")
        for i in range(core.LOGIN_MAX_PER_IP):
            sprayer.post("/login", data={"username": f"guess{i}", "password": "x"},
                         follow_redirects=False)
        self.assertIn("Too%20many", self.login_location(sprayer, "testpassword"))
        self.assertEqual(self.login_location(self.client("203.0.113.11"), "testpassword"), "/")

    def test_successful_sign_in_clears_only_that_address(self):
        with core.database() as db:
            core.failed_login(db, "owner", "198.51.100.1")
            core.failed_login(db, "owner", "198.51.100.2")
            core.clear_login_failures(db, "owner", "198.51.100.1")
            remaining = [r[0] for r in db.execute("SELECT ip FROM login_failures")]
        self.assertEqual(remaining, ["198.51.100.2"])

    def test_client_ip_header_is_trusted_only_from_a_private_peer(self):
        def request(peer: str, forwarded: str | None) -> Request:
            headers = [(b"cf-connecting-ip", forwarded.encode())] if forwarded else []
            return Request({"type": "http", "client": (peer, 1234), "headers": headers})

        self.assertEqual(client_ip(request("172.19.0.1", "198.51.100.9")), "172.19.0.1")
        os.environ["CLIENT_IP_HEADER"] = "CF-Connecting-IP"
        self.assertEqual(client_ip(request("172.19.0.1", "198.51.100.9")), "198.51.100.9")
        self.assertEqual(client_ip(request("127.0.0.1", "2001:db8::5")), "2001:db8::5")
        # A public peer cannot choose its own address with a forged header.
        self.assertEqual(client_ip(request("203.0.113.50", "198.51.100.9")), "203.0.113.50")
        # Garbage or a missing header falls back to the real peer.
        self.assertEqual(client_ip(request("172.19.0.1", "not-an-ip")), "172.19.0.1")
        self.assertEqual(client_ip(request("172.19.0.1", None)), "172.19.0.1")

    def test_cloudflare_visitors_are_throttled_separately(self):
        os.environ["CLIENT_IP_HEADER"] = "CF-Connecting-IP"
        tunnel = self.client("172.19.0.1")
        for _ in range(core.LOGIN_MAX_PER_USER_IP):
            tunnel.post("/login", data={"username": "owner", "password": "bad"},
                        headers={"CF-Connecting-IP": "198.51.100.200"}, follow_redirects=False)
        blocked = tunnel.post("/login", data={"username": "owner", "password": "testpassword"},
                              headers={"CF-Connecting-IP": "198.51.100.200"},
                              follow_redirects=False)
        self.assertIn("Too%20many", blocked.headers["location"])
        allowed = tunnel.post("/login", data={"username": "owner", "password": "testpassword"},
                              headers={"CF-Connecting-IP": "203.0.113.20"},
                              follow_redirects=False)
        self.assertEqual(allowed.headers["location"], "/")


if __name__ == "__main__":
    unittest.main()

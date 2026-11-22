"""Offline group export tests. All Telegram API operations are mocked."""
from __future__ import annotations

import csv
import io
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from telethon import types

from broadcaster import core, group_export
from broadcaster.web import app


class FakeClient:
    def __init__(self):
        self.connect = AsyncMock()
        self.is_user_authorized = AsyncMock(return_value=True)
        self.get_me = AsyncMock(return_value=SimpleNamespace(username="testgroupowner"))
        self.disconnect = AsyncMock()
        self.send_message = AsyncMock(side_effect=AssertionError("Export must never send"))
        group = MagicMock(spec=types.Chat)
        group.deactivated = False
        supergroup = MagicMock(spec=types.Channel)
        supergroup.megagroup = True
        channel = MagicMock(spec=types.Channel)
        channel.megagroup = False
        user = MagicMock(spec=types.User)
        self.dialogs = [
            SimpleNamespace(id=-7, entity=group, title="A private group"),
            SimpleNamespace(id=-10012, entity=supergroup, title="Community supergroup"),
            SimpleNamespace(id=-10013, entity=channel, title="Announcements channel"),
            SimpleNamespace(id=17, entity=user, title="Direct chat"),
        ]
        self.members = [
            SimpleNamespace(id=101, first_name="Alice", last_name="Tester",
                            username="Alice_T", phone="420777000111", bot=False, deleted=False),
            SimpleNamespace(id=102, first_name="No", last_name="Handle",
                            username=None, phone=None, bot=False, deleted=False),
            SimpleNamespace(id=101, first_name="Alice", last_name="Tester",
                            username="alice_t", phone="420777000111", bot=False, deleted=False),
            SimpleNamespace(id=103, first_name='=HYPERLINK("https://example.invalid","click")',
                            last_name="", username="evil=1", phone="=123",
                            bot=False, deleted=False),
            SimpleNamespace(id=104, first_name="Bob", last_name="Smith",
                            username="Bob_123", phone="+12025550123", bot=False, deleted=False),
            SimpleNamespace(id=105, first_name="Spam", last_name="Bot",
                            username="BotWriter", phone="420111111111", bot=True, deleted=False),
            SimpleNamespace(id=106, first_name="Deleted", last_name="User",
                            username="OldUser", phone="420222222222", bot=False, deleted=True),
        ]
        self.fail_after = None

    async def iter_dialogs(self, limit=None):
        for dialog in self.dialogs[:limit]:
            yield dialog

    async def iter_participants(self, entity, limit=None):
        for index, member in enumerate(self.members[:limit]):
            if self.fail_after is not None and index >= self.fail_after:
                raise RuntimeError("Simulated Telegram network failure")
            yield member


class GroupExportFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        names=("DATABASE_PATH","APP_ENCRYPTION_KEY","TELEGRAM_SESSION_DIR","COOKIE_SECURE")
        self.before = {k:os.environ.get(k) for k in names}
        os.environ.update({
            "DATABASE_PATH":str(Path(self.temp.name)/"test.sqlite3"),
            "APP_ENCRYPTION_KEY":Fernet.generate_key().decode(),
            "TELEGRAM_SESSION_DIR":str(Path(self.temp.name)/"sessions"),
            "COOKIE_SECURE":"false",
        })
        core.init_db()
        core.create_admin("owner","testpassword")
        with core.database() as db:
            self.owner_id=db.execute("SELECT id FROM admins WHERE username='owner'").fetchone()["id"]
            self.account=uuid.uuid4().hex
            db.execute(
                "INSERT INTO accounts(id,label,api_id,api_hash_enc,phone_enc,"
                "session_name,expected_username,username,state,created_at,updated_at,owner_id) "
                "VALUES (?, 'Offline sender', 10001, ?, ?, ?, '@testgroupowner',"
                " '@testgroupowner', 'connected', ?, ?, ?)",
                (self.account,core.encrypt("dummyhash"),
                 core.encrypt("+420000000000"),self.account,
                 core.now(),core.now(),self.owner_id),
            )
            core.create_user(db,"colleague","theirpassword")
            self.member_id=db.execute("SELECT id FROM admins WHERE username='colleague'").fetchone()["id"]
            db.commit()
        self.fake=FakeClient()
        self.telegram_patch=patch("broadcaster.group_export.telegram.new_client",return_value=self.fake)
        self.telegram_patch.start()
        self.refresh_patch=patch("broadcaster.web.telegram.refresh_connected_accounts",return_value=[])
        self.refresh_patch.start()
        group_export._scans.clear()
        self.http=TestClient(app,raise_server_exceptions=True)
        self.http.__enter__()
        signed=self.http.post("/login",data={"username":"owner","password":"testpassword"},
                              follow_redirects=False)
        self.assertEqual(signed.status_code,303)
        with core.database() as db:
            self.csrf=core.session(db,self.http.cookies["tb_session"])["csrf"]

    def tearDown(self):
        self.http.__exit__(None,None,None)
        self.refresh_patch.stop()
        self.telegram_patch.stop()
        group_export._scans.clear()
        for k,v in self.before.items():
            if v is None: os.environ.pop(k,None)
            else: os.environ[k]=v
        self.temp.cleanup()

    def _begin(self,peer_id=-7):
        r=self.http.post(
            "/groups/scans",
            data={"csrf":self.csrf,"account_id":self.account,"peer_id":str(peer_id)},
            follow_redirects=False,
        )
        self.assertEqual(r.status_code,303,r.text)
        self.assertTrue(r.headers["location"].startswith("/groups/scans/"))
        token=r.headers["location"].rsplit("/",1)[-1]
        return token

    def _wait(self,token):
        for _ in range(60):
            scan=group_export.get_scan(token,self.owner_id)
            if scan and scan.state not in ("queued","running"):
                return scan
            time.sleep(0.04)
        self.fail("The background group scan did not complete")

    def test_group_browser_lists_groups_not_private_chats_or_channels(self):
        page=self.http.get(f"/groups?account_id={self.account}")
        self.assertEqual(page.status_code,200,page.text[:700])
        self.assertIn("A private group",page.text)
        self.assertIn("Community supergroup",page.text)
        self.assertNotIn("Announcements channel",page.text)
        self.assertNotIn("Direct chat",page.text)
        self.assertIn('value="-7"',page.text)
        self.assertIn('id="group-search"',page.text)
        self.assertIn('data-group-name="a private group"',page.text)
        self.assertIn('<strong>A private group</strong>',page.text)
        self.assertIn('<strong>Community supergroup</strong>',page.text)
        self.assertIn('id="group-no-match"',page.text)
        self.fake.send_message.assert_not_awaited()

    def test_optional_phone_never_guesses_invalid_or_missing_values(self):
        from broadcaster.group_export import visible_phone, member_name
        self.assertEqual(visible_phone(SimpleNamespace(phone=None)), "")
        self.assertEqual(visible_phone(SimpleNamespace(phone=420123456)), "")
        self.assertEqual(visible_phone(SimpleNamespace(phone="=1+1")), "")
        self.assertEqual(visible_phone(SimpleNamespace(phone="420123456789")), "+420123456789")
        self.assertEqual(visible_phone(SimpleNamespace(phone="+420123456789")), "+420123456789")
        self.assertEqual(member_name(SimpleNamespace(first_name=" Anna  ", last_name=" Smith  ")),
                         "Anna Smith")
        self.fake.send_message.assert_not_awaited()

    def test_group_selection_layout_keeps_radio_compact(self):
        root=Path(__file__).resolve().parents[1]
        base=(root/"broadcaster/static/app.css").read_text()
        theme=(root/"broadcaster/static/telegram-theme.css").read_text()
        js=(root/"broadcaster/static/app.js").read_text()
        # The old broad text-input selector erroneously gave radio
        # buttons width:100% and pushed their group labels offscreen.
        self.assertIn(
            "input:not([type=checkbox]):not([type=file]):not([type=radio])",
            base,
        )
        self.assertIn('.group-export-item input[type="radio"]',theme)
        self.assertIn('grid-template-columns: 18px 39px minmax(0, 1fr)',theme)
        self.assertIn('.group-export-item[hidden] { display: none; }',theme)
        self.assertIn('grid-template-columns: minmax(0, 1fr)',theme)
        self.assertIn('const groupSearch=document.getElementById("group-search")',js)

    def test_exports_name_username_and_only_telegram_visible_phone(self):
        token=self._begin()
        scan=self._wait(token)
        self.assertEqual(scan.state,"complete")
        self.assertEqual(scan.seen,7)
        self.assertEqual(scan.missing_username,2)
        self.assertEqual(scan.skipped_bots,2)
        self.assertEqual(scan.usernames,["Alice_T","Bob_123"])
        self.assertEqual(len(scan.members),4)
        self.assertEqual(scan.phone_count,2)
        details=self.http.get(f"/groups/scans/{token}")
        self.assertEqual(details.status_code,200)
        self.assertIn("@Alice_T",details.text)
        self.assertIn("No Handle",details.text)
        self.assertIn("+420777000111",details.text)
        self.assertIn("Not visible",details.text)
        self.assertIn("Download 4 members",details.text)
        csv_result=self.http.get(f"/groups/scans/{token}/export")
        self.assertEqual(csv_result.status_code,200)
        self.assertIn("attachment",csv_result.headers["content-disposition"])
        self.assertIn("telegram-group-members.csv",csv_result.headers["content-disposition"])
        self.assertIn("text/csv",csv_result.headers["content-type"])
        rows=list(csv.reader(io.StringIO(csv_result.content.decode("utf-8-sig"))))
        self.assertEqual(rows[0],["Name","Username","Phone Number"])
        self.assertEqual(len(rows),5)
        by_name={row[0]:row[1:] for row in rows[1:]}
        self.assertEqual(by_name["Alice Tester"],["Alice_T","+420777000111"])
        self.assertEqual(by_name["Bob Smith"],["Bob_123","+12025550123"])
        self.assertEqual(by_name["No Handle"],["",""])
        self.assertEqual(
            by_name['\'=HYPERLINK("https://example.invalid","click")'],["",""]
        )
        self.assertNotIn("BotWriter",csv_result.text)
        self.assertNotIn("420111111111",csv_result.text)
        with core.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0],0)
        self.fake.send_message.assert_not_awaited()

    def test_unknown_group_is_rejected_without_join_or_send(self):
        token=self._begin(-99999)
        scan=self._wait(token)
        self.assertEqual(scan.state,"failed")
        self.assertIn("not found",scan.error)
        self.assertEqual(self.http.get(f"/groups/scans/{token}/export").status_code,409)
        self.fake.send_message.assert_not_awaited()

    def test_partial_scan_indicates_incomplete_export(self):
        self.fake.fail_after=2
        token=self._begin()
        scan=self._wait(token)
        self.assertEqual(scan.state,"partial")
        self.assertEqual(scan.seen,2)
        self.assertEqual(scan.usernames,["Alice_T"])
        self.assertEqual(len(scan.members),2)
        r=self.http.get(f"/groups/scans/{token}/export")
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.headers["x-group-scan-state"],"partial")
        self.assertIn("Alice_T",r.text)
        self.assertIn("No Handle",r.text)

    def test_limit_is_reported_not_silently_complete(self):
        with patch.object(group_export,"MAX_SCAN_USERS",2):
            token=self._begin()
            scan=self._wait(token)
        self.assertEqual(scan.state,"limited")
        self.assertIn("limit",scan.error)
        self.assertEqual(scan.seen,2)

    def test_cross_workspace_access_and_csrf_are_blocked(self):
        token=self._begin()
        self._wait(token)
        member=TestClient(app,raise_server_exceptions=True)
        member.__enter__()
        try:
            signed=member.post("/login",
                data={"username":"colleague","password":"theirpassword"},
                follow_redirects=False)
            self.assertEqual(signed.status_code,303)
            self.assertEqual(member.get(f"/groups?account_id={self.account}").status_code,404)
            self.assertEqual(member.get(f"/groups/scans/{token}").status_code,404)
            self.assertEqual(member.get(f"/groups/scans/{token}/export").status_code,404)
            with core.database() as db:
                member_csrf=core.session(db,member.cookies["tb_session"])["csrf"]
            # Forging another owner's connected sender cannot start a scan.
            response=member.post(
                "/groups/scans",
                data={"csrf":member_csrf,"account_id":self.account,"peer_id":"-7"},
                follow_redirects=False)
            self.assertEqual(response.status_code,303)
            self.assertTrue(response.headers["location"].startswith("/groups"))
        finally:
            member.__exit__(None,None,None)
        self.assertEqual(self.http.post(
            "/groups/scans",data={"csrf":"wrong","account_id":self.account,"peer_id":"-7"},
            follow_redirects=False).status_code,403)

    def test_results_expire_and_do_not_persist_to_database(self):
        token=self._begin()
        scan=self._wait(token)
        scan.created_at -= group_export.RESULT_TTL_SECONDS+1
        self.assertIsNone(group_export.get_scan(token,self.owner_id))
        self.assertEqual(self.http.get(f"/groups/scans/{token}").status_code,404)

    def test_scan_rejects_active_broadcast_for_the_same_sender(self):
        with core.database() as db:
            contact=core.insert_contact(
                db,core.normalize_contact("Test","@testpeople","","",""),
                self.owner_id,consent=True,
            )
            campaign=core.create_campaign(
                db,self.account,"Not sent",[contact],None,self.owner_id,
            )
            db.execute("UPDATE campaigns SET state='queued' WHERE id=?",(campaign,))
            db.commit()
        resp=self.http.post(
            "/groups/scans",
            data={"csrf":self.csrf,"account_id":self.account,"peer_id":"-7"},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code,303)
        self.assertTrue(resp.headers["location"].startswith("/groups"))
        self.assertEqual(group_export._scans,{})
        self.fake.send_message.assert_not_awaited()

    def test_scan_token_not_ready_for_download(self):
        from broadcaster.group_export import GroupScan
        temporary=GroupScan(
            token="pending-test-token",owner_id=self.owner_id,
            account_id=self.account,peer_id=-7,created_at=time.monotonic(),
        )
        group_export._scans[temporary.token]=temporary
        r=self.http.get(f"/groups/scans/{temporary.token}/export")
        self.assertEqual(r.status_code,409)

    def test_session_revoked_in_scan_is_marked_disconnected(self):
        self.fake.is_user_authorized=AsyncMock(return_value=False)
        token=self._begin()
        scan=self._wait(token)
        self.assertEqual(scan.state,"failed")
        with core.database() as db:
            r=db.execute("SELECT state FROM accounts WHERE id=?",(self.account,)).fetchone()
            self.assertEqual(r["state"],"disconnected")
        self.fake.send_message.assert_not_awaited()

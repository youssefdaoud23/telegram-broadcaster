"""Safe theme-switch tests. No real Telegram calls or messages."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from broadcaster import core
from broadcaster.web import app


class AppearanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        keys = ("DATABASE_PATH", "APP_ENCRYPTION_KEY", "TELEGRAM_SESSION_DIR",
                "SEND_ENABLED", "COOKIE_SECURE")
        self.old = {key: os.environ.get(key) for key in keys}
        os.environ.update({
            "DATABASE_PATH": str(Path(self.temp.name) / "themes.sqlite3"),
            "APP_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "TELEGRAM_SESSION_DIR": str(Path(self.temp.name) / "sessions"),
            "SEND_ENABLED": "false",
            "COOKIE_SECURE": "false",
        })
        core.init_db()
        core.create_admin("owner", "owner-pass")
        with core.database() as db:
            core.create_user(db, "colleague", "colleague-pass")
            db.commit()
        self.client = TestClient(app, raise_server_exceptions=True)
        self.client.__enter__()
        self.csrf = self.login(self.client, "owner", "owner-pass")

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for key, value in self.old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def login(self, client, name, password):
        response = client.post(
            "/login", data={"username": name, "password": password},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        with core.database() as db:
            return core.session(db, client.cookies["tb_session"])["csrf"]

    def choose(self, value, *, csrf=None, return_to="/settings/appearance", client=None):
        return (client or self.client).post(
            "/settings/appearance",
            data={"csrf": csrf if csrf is not None else self.csrf,
                  "site_template": value, "return_to": return_to},
            follow_redirects=False,
        )

    def test_default_classic_and_available_template_selection(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn('data-site-template="telegram"', page.text)
        self.assertIn('<option value="telegram" selected>', page.text)
        self.assertIn("Space Science", page.text)
        self.assertIn('href="/settings/appearance"', page.text)
        self.assertIn("/static/space-science.css", page.text)
        appearance = self.client.get("/settings/appearance")
        self.assertEqual(appearance.status_code, 200)
        self.assertIn("Telegram Classic", appearance.text)
        self.assertIn("Use Space Science", appearance.text)

    def test_switch_persists_across_pages_and_reverts(self):
        resp = self.choose("space", return_to="/contacts")
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].startswith("/contacts"))
        for path in ("/", "/contacts", "/contacts/groups", "/groups",
                     "/campaigns", "/compose", "/settings/appearance"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn('data-site-template="space"', response.text)
        self.assertIn("YOUR COMMUNICATIONS.", self.client.get("/").text)
        with core.database() as db:
            self.assertEqual(db.execute(
                "SELECT site_template FROM admins WHERE username='owner'"
            ).fetchone()[0], "space")
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM campaigns").fetchone()[0], 0)
        self.assertEqual(self.choose("telegram").status_code, 303)
        self.assertIn('data-site-template="telegram"', self.client.get("/").text)

    def test_bad_csrf_unsupported_value_and_external_redirect_blocked(self):
        self.assertEqual(self.choose("space", csrf="wrong").status_code, 403)
        self.assertEqual(self.choose("something-else").status_code, 400)
        with core.database() as db:
            self.assertEqual(db.execute(
                "SELECT site_template FROM admins WHERE username='owner'"
            ).fetchone()[0], "telegram")
        for value in ("//evil.example", "https://evil.example", "/settings/appearance?next=evil"):
            response = self.choose("space", return_to=value)
            self.assertEqual(response.status_code, 303)
            self.assertTrue(response.headers["location"].startswith("/settings/appearance"))

    def test_different_users_get_independent_saved_choices(self):
        self.choose("space")
        with TestClient(app) as member:
            other = self.login(member, "colleague", "colleague-pass")
            self.assertIn('data-site-template="telegram"', member.get("/").text)
            chosen = member.post("/settings/appearance",
                data={"csrf": other, "site_template": "space"}, follow_redirects=False)
            self.assertEqual(chosen.status_code, 303)
            self.choose("telegram")
            self.assertIn('data-site-template="space"', member.get("/").text)
            self.assertIn('data-site-template="telegram"', self.client.get("/").text)

    def test_two_color_modes_and_zero_third_party_assets(self):
        root = Path(__file__).resolve().parents[1]
        css = (root / "broadcaster/static/space-science.css").read_text()
        js = (root / "broadcaster/static/telegram-theme.js").read_text()
        self.assertIn(':root[data-site-template="space"]', css)
        self.assertIn(':root[data-site-template="space"][data-theme="light"]', css)
        self.assertIn("telegram-broadcaster-space-color", js)
        self.assertIn("telegram-broadcaster-theme", js)
        self.assertNotIn("http://", css)
        self.assertNotIn("https://", css)
        self.assertNotIn("@import", css)

    def test_legacy_admin_schema_migrates_and_preserves_preference(self):
        with core.database() as db:
            db.execute("ALTER TABLE admins DROP COLUMN site_template")
            db.commit()
        core.init_db()
        with core.database() as db:
            self.assertEqual(db.execute(
                "SELECT site_template FROM admins WHERE username='owner'"
            ).fetchone()[0], "telegram")
            user_id = db.execute("SELECT id FROM admins WHERE username='owner'").fetchone()[0]
            core.set_site_template(db, user_id, "space")
            db.commit()
        core.init_db()
        with core.database() as db:
            self.assertEqual(db.execute(
                "SELECT site_template FROM admins WHERE username='owner'"
            ).fetchone()[0], "space")

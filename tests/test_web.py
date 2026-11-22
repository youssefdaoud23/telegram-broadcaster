"""Offline HTTP smoke tests. Never call Telegram API or send a message."""
import os
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from broadcaster import core
from broadcaster.web import app


class WebsiteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old={k:os.environ.get(k) for k in (
            "DATABASE_PATH","APP_ENCRYPTION_KEY","TELEGRAM_SESSION_DIR",
            "SEND_ENABLED","SEND_MAX_RECIPIENTS","COOKIE_SECURE",
        )}
        os.environ["DATABASE_PATH"]=str(Path(self.temp.name)/"website.sqlite3")
        os.environ["APP_ENCRYPTION_KEY"]=Fernet.generate_key().decode()
        os.environ["TELEGRAM_SESSION_DIR"]=str(Path(self.temp.name)/"sessions")
        os.environ["SEND_ENABLED"]="false"
        os.environ["SEND_MAX_RECIPIENTS"]="3"
        os.environ["COOKIE_SECURE"]="false"
        core.init_db()
        core.create_admin("admin-test","safe sample password 123456!")
        self.testclient=TestClient(app,raise_server_exceptions=True)
        self.testclient.__enter__()

    def tearDown(self):
        self.testclient.__exit__(None,None,None)
        for k,v in self.old.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        self.temp.cleanup()

    def sign_in(self):
        response=self.testclient.post(
            "/login",data={"username":"admin-test","password":"safe sample password 123456!"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code,303)
        self.assertIn("tb_session",self.testclient.cookies)
        raw=self.testclient.cookies["tb_session"]
        with core.database() as db:
            person=core.session(db,raw)
        self.assertIsNotNone(person)
        return person["csrf"]

    def test_login_and_protected_pages(self):
        self.assertEqual(self.testclient.get("/login").status_code,200)
        anonymous=self.testclient.get("/",follow_redirects=False)
        self.assertEqual(anonymous.status_code,303)
        self.assertEqual(anonymous.headers["location"],"/login")
        csrf=self.sign_in()
        for path in ["/","/contacts","/accounts","/accounts/new","/compose","/campaigns"]:
            r=self.testclient.get(path)
            self.assertEqual(r.status_code,200,(path,r.text[:300]))
            self.assertIn("Telegram",r.text)
            self.assertEqual(r.headers.get("X-Frame-Options"),"DENY")
        bad=self.testclient.post("/contacts",data={"csrf":"incorrect","name":"Never","username":"@neveraccount"},follow_redirects=False)
        self.assertEqual(bad.status_code,403)

    def test_short_and_long_password_login(self):
        for username, password in (("short-user", "a"), ("long-user", "Q" * 600)):
            core.create_admin(username, password)
            response = self.testclient.post(
                "/login", data={"username": username, "password": password},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/")
            self.assertEqual(self.testclient.get("/").status_code, 200)

    def test_telegram_theme_assets_and_composer_preview(self):
        self.sign_in()
        home = self.testclient.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("/static/telegram-theme.css", home.text)
        self.assertIn("/static/telegram-theme.js", home.text)
        self.assertIn("data-theme-toggle", home.text)
        login = self.testclient.get("/login")
        self.assertEqual(login.status_code, 200)
        self.assertIn("data-theme-toggle", login.text)

        stylesheet = self.testclient.get("/static/telegram-theme.css")
        script = self.testclient.get("/static/telegram-theme.js")
        self.assertEqual(stylesheet.status_code, 200)
        self.assertEqual(script.status_code, 200)
        self.assertIn("data-theme", stylesheet.text)
        self.assertIn("telegram-broadcaster-theme", script.text)

        compose = self.testclient.get("/compose")
        self.assertEqual(compose.status_code, 200)
        self.assertIn('class="panel tg-recipient-panel"', compose.text)
        self.assertIn('class="panel tg-composer-panel"', compose.text)
        self.assertIn('id="tg-message-preview-text"', compose.text)
        self.assertIn('name="csrf"', compose.text)
        self.assertIn('action="/compose/preview"', compose.text)

    def test_add_contact_and_import(self):
        csrf=self.sign_in()
        response=self.testclient.post("/contacts",data={
            "csrf":csrf,"name":"Alice Tester","username":"@alicetester","consent":"yes","enabled":"yes"
        },follow_redirects=False)
        self.assertEqual(response.status_code,303)
        self.assertIn("Alice Tester",self.testclient.get("/contacts").text)
        self.assertEqual(self.testclient.get("/contacts/export").status_code,200)
        csvdata="Name,ID\nJoe Example,@joeexample\n"
        uploaded=self.testclient.post("/contacts/import",data={"csrf":csrf},
            files={"file":("contacts.csv",csvdata,"text/csv")},follow_redirects=False)
        self.assertEqual(uploaded.status_code,303)
        with core.database() as db:
            joe=db.execute("SELECT * FROM contacts WHERE username=?",("@joeexample",)).fetchone()
        self.assertEqual(joe["consent"],0)

    def test_create_account_and_draft_without_sending(self):
        csrf=self.sign_in()
        res=self.testclient.post("/accounts/new",data={
            "csrf":csrf,"label":"Example account","api_id":"1234567",
            "api_hash":"a"*32,"phone":"+420123456789","expected_username":"@example12345",
        },follow_redirects=False)
        self.assertEqual(res.status_code,303)
        with core.database() as db:
            row=db.execute("SELECT * FROM accounts LIMIT 1").fetchone()
            self.assertNotIn("a"*32,row["api_hash_enc"])
            self.assertNotIn("+420123456789",row["phone_enc"])
            db.execute("UPDATE accounts SET state='connected',username='@example12345' WHERE id=?",(row["id"],))
            details=core.normalize_contact("Alice","@alicetest123","","","")
            contact_id=core.insert_contact(db,details,consent=True)
            db.commit()
        self.assertEqual(self.testclient.get("/compose").status_code,200)
        preview=self.testclient.post("/compose/preview",data={
            "csrf":csrf,"account_id":row["id"],"message":"Test message","contacts":str(contact_id)
        },follow_redirects=False)
        self.assertEqual(preview.status_code,303)
        cid=preview.headers["location"].split("?")[0].split("/")[-1]
        page=self.testclient.get("/campaigns/"+cid)
        self.assertEqual(page.status_code,200)
        self.assertIn("Test message",page.text)
        self.assertIn("disabled",page.text.lower())
        no_send=self.testclient.post("/campaigns/"+cid+"/send",data={"csrf":csrf},follow_redirects=False)
        self.assertEqual(no_send.status_code,303)
        with core.database() as db:
            status=db.execute("SELECT state FROM campaigns WHERE id=?",(cid,)).fetchone()["state"]
        self.assertEqual(status,"draft")

if __name__=="__main__":
    unittest.main()
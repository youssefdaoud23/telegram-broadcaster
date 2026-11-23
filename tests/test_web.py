"""Offline HTTP smoke tests. Never call Telegram API or send a message."""
import os
import tempfile
import unittest
from unittest.mock import patch
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
        with core.database() as db:
            self.owner_id=db.execute(
                "SELECT id FROM admins WHERE username='admin-test'"
            ).fetchone()["id"]
        # Synthetic connected test accounts must never connect to Telegram.
        self.refresh_patcher=patch("broadcaster.web.telegram.refresh_connected_accounts",return_value=[])
        self.refresh_patcher.start()
        self.testclient=TestClient(app,raise_server_exceptions=True)
        self.testclient.__enter__()

    def tearDown(self):
        self.testclient.__exit__(None,None,None)
        self.refresh_patcher.stop()
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

    def test_send_endpoint_schedules_on_running_loop_without_using_telegram(self):
        import asyncio
        import uuid
        from unittest.mock import patch

        csrf = self.sign_in()
        account_id = uuid.uuid4().hex
        with core.database() as db:
            db.execute(
                "INSERT INTO accounts(id,label,api_id,api_hash_enc,phone_enc,session_name,state,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,'connected',?,?)",
                (account_id, "Offline sender", 12345, core.encrypt("dummy"), core.encrypt("+420123456789"),
                 account_id, core.now(), core.now()),
            )
            db.execute("UPDATE accounts SET owner_id=? WHERE owner_id IS NULL", (self.owner_id,))
            contact_id = core.insert_contact(
                db, core.normalize_contact("Offline contact", "@offlineexample", "", "", ""),
                consent=True,
             owner_id=self.owner_id)
            campaign_id = core.create_campaign(db, account_id, "Offline test only", [contact_id], 3, owner_id=self.owner_id)
            db.commit()

        # We must never create a TelegramClient or send a live message in tests.
        # The replacement verifies the endpoint has an actual running event loop.
        scheduled = []
        def fake_enqueue(value):
            self.assertIsNotNone(asyncio.get_running_loop())
            scheduled.append(value)

        os.environ["SEND_ENABLED"] = "true"
        with patch("broadcaster.web.telegram.enqueue", side_effect=fake_enqueue):
            response = self.testclient.post(
                f"/campaigns/{campaign_id}/send",
                data={"csrf": csrf}, follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            self.assertIn("Sending%20started", response.headers["location"])
            duplicate = self.testclient.post(
                f"/campaigns/{campaign_id}/send",
                data={"csrf": csrf}, follow_redirects=False,
            )
            self.assertEqual(duplicate.status_code, 303)

        self.assertEqual(scheduled, [campaign_id])
        with core.database() as db:
            campaign = db.execute("SELECT state FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
            delivery = db.execute("SELECT state FROM deliveries WHERE campaign_id=?", (campaign_id,)).fetchone()
        self.assertEqual(campaign["state"], "queued")
        self.assertEqual(delivery["state"], "pending")

    def test_unlimited_composer_accepts_more_than_1000_recipients(self):
        import uuid
        csrf=self.sign_in()
        account_id=uuid.uuid4().hex
        with core.database() as db:
            db.execute(
                "INSERT INTO accounts(id,label,api_id,api_hash_enc,phone_enc,session_name,state,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,'connected',?,?)",
                (account_id, "Offline", 12345, core.encrypt("hash"), core.encrypt("+420123456789"),
                 account_id, core.now(), core.now()),
            )
            db.execute("UPDATE accounts SET owner_id=? WHERE owner_id IS NULL", (self.owner_id,))
            ids=[]
            for index in range(1005):
                ids.append(core.insert_contact(db,core.normalize_contact(
                    f"Sample{index}",f"@webcapacity{index}","","",""
                ), consent=True, owner_id=self.owner_id))
            db.commit()
        os.environ.pop("SEND_MAX_RECIPIENTS",None)
        compose=self.testclient.get("/compose")
        self.assertEqual(compose.status_code,200)
        self.assertIn("No application recipient limit",compose.text)
        response=self.testclient.post(
            "/compose/preview",
            data={"csrf":csrf,"account_id":account_id,"message":"Draft only",
                  "contacts":[str(i) for i in ids]},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code,303)
        cid=response.headers["location"].split("?")[0].rsplit("/",1)[-1]
        with core.database() as db:
            row=db.execute("SELECT state,total FROM campaigns WHERE id=?",(cid,)).fetchone()
            self.assertEqual((row["state"],row["total"]),("draft",1005))

    def test_interrupted_try_again_creates_reviewable_draft_only(self):
        import uuid
        from unittest.mock import patch

        csrf=self.sign_in()
        sender=uuid.uuid4().hex
        with core.database() as db:
            db.execute(
                "INSERT INTO accounts(id,label,api_id,api_hash_enc,phone_enc,session_name,state,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,'connected',?,?)",
                (sender,"Offline sender",12345,core.encrypt("testhash"),core.encrypt("+420123456789"),
                 sender,core.now(),core.now()),
            )
            db.execute("UPDATE accounts SET owner_id=? WHERE owner_id IS NULL", (self.owner_id,))
            ids=[]
            for n in range(2):
                ids.append(core.insert_contact(
                    db,core.normalize_contact(f"Test{n}",f"@retrywebtest{n}","","",""),
                    consent=True,
                 owner_id=self.owner_id))
            source=core.create_campaign(db,sender,"Not sent automatically",ids,3, owner_id=self.owner_id)
            db.execute("UPDATE campaigns SET state='interrupted' WHERE id=?",(source,))
            db.execute(
                "UPDATE deliveries SET state='unknown' WHERE campaign_id=? AND contact_id=?",
                (source,ids[0]),
            )
            db.commit()

        history=self.testclient.get("/campaigns")
        self.assertEqual(history.status_code,200)
        self.assertIn(f"/campaigns/{source}/retry",history.text)
        details=self.testclient.get(f"/campaigns/{source}")
        self.assertIn("Try again",details.text)
        bad=self.testclient.post(
            f"/campaigns/{source}/retry",data={"csrf":"wrong"},
            follow_redirects=False,
        )
        self.assertEqual(bad.status_code,403)

        os.environ["SEND_ENABLED"]="true"
        with patch("broadcaster.web.telegram.enqueue",side_effect=AssertionError("No Telegram API expected")) as sending:
            res=self.testclient.post(
                f"/campaigns/{source}/retry",data={"csrf":csrf},
                follow_redirects=False,
            )
            self.assertEqual(res.status_code,303)
            retry=res.headers["location"].split("?")[0].split("/")[-1]
            self.assertNotEqual(retry,source)
            second=self.testclient.post(
                f"/campaigns/{source}/retry",data={"csrf":csrf},
                follow_redirects=False,
            )
            self.assertEqual(second.status_code,303)
            self.assertIn(f"/campaigns/{retry}",second.headers["location"])
            sending.assert_not_called()

        with core.database() as db:
            old=db.execute("SELECT state FROM campaigns WHERE id=?",(source,)).fetchone()
            new=db.execute("SELECT state,total,message FROM campaigns WHERE id=?",(retry,)).fetchone()
            attempts=db.execute("SELECT contact_id,state FROM deliveries WHERE campaign_id=?",(retry,)).fetchall()
            self.assertEqual(old["state"],"interrupted")
            self.assertEqual(new["state"],"draft")
            self.assertEqual(new["message"],"Not sent automatically")
            self.assertEqual(new["total"],1)
            self.assertEqual([(d["contact_id"],d["state"]) for d in attempts],[(ids[1],"pending")])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0],2)

        page=self.testclient.get(f"/campaigns/{retry}")
        self.assertEqual(page.status_code,200)
        self.assertIn("Send 1 private message",page.text)
        self.assertIn("Not sent automatically",page.text)
        self.assertIn("View retry",self.testclient.get("/campaigns").text)

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
            contact_id=core.insert_contact(db,details,consent=True, owner_id=self.owner_id)
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
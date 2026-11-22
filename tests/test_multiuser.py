"""Offline multi-user access isolation and upgrade tests. Never contact Telegram."""
from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from broadcaster import core, telegram
from broadcaster.web import app


class MultiUserWebsiteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        keys=("DATABASE_PATH","APP_ENCRYPTION_KEY","TELEGRAM_SESSION_DIR",
              "SEND_ENABLED","SEND_MAX_RECIPIENTS","COOKIE_SECURE")
        self.previous={k:os.environ.get(k) for k in keys}
        os.environ.update({
            "DATABASE_PATH":str(Path(self.temp.name)/"multiuser.sqlite3"),
            "APP_ENCRYPTION_KEY":Fernet.generate_key().decode(),
            "TELEGRAM_SESSION_DIR":str(Path(self.temp.name)/"sessions"),
            "SEND_ENABLED":"false",
            "COOKIE_SECURE":"false",
        })
        os.environ.pop("SEND_MAX_RECIPIENTS",None)
        core.init_db()
        core.create_admin("admin","owner-password")
        self.owner=TestClient(app,raise_server_exceptions=True)
        self.member=TestClient(app,raise_server_exceptions=True)
        self.owner.__enter__()
        self.member.__enter__()
        self.owner_csrf=self.login(self.owner,"admin","owner-password")
        with core.database() as db:
            self.owner_id=db.execute("SELECT id FROM admins WHERE username='admin'").fetchone()["id"]
        new=self.owner.post(
            "/users",
            data={"csrf":self.owner_csrf,"username":"colleague",
                  "password":"user-password","confirm_password":"user-password"},
            follow_redirects=False,
        )
        self.assertEqual(new.status_code,303)
        self.user_csrf=self.login(self.member,"colleague","user-password")
        with core.database() as db:
            self.user_id=db.execute("SELECT id FROM admins WHERE username='colleague'").fetchone()["id"]

    def tearDown(self):
        self.member.__exit__(None,None,None)
        self.owner.__exit__(None,None,None)
        for key,value in self.previous.items():
            if value is None:
                os.environ.pop(key,None)
            else:
                os.environ[key]=value
        self.temp.cleanup()

    def login(self,client,username,password):
        response=client.post(
            "/login",data={"username":username,"password":password},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code,303)
        with core.database() as db:
            session=core.session(db,client.cookies.get("tb_session"))
        self.assertIsNotNone(session)
        return session["csrf"]

    def insert_sender(self,owner_id,label):
        sender_id=uuid.uuid4().hex
        with core.database() as db:
            db.execute(
                "INSERT INTO accounts(id,label,api_id,api_hash_enc,phone_enc,"
                "session_name,state,created_at,updated_at,owner_id) "
                "VALUES (?,?,?,?,?,?,'connected',?,?,?)",
                (sender_id,label,123456,core.encrypt("hash"),
                 core.encrypt("+420123456789"),sender_id,core.now(),core.now(),owner_id),
            )
            db.commit()
        return sender_id

    def insert_contact(self,owner_id,name,username):
        with core.database() as db:
            contact=core.insert_contact(
                db,core.normalize_contact(name,username,"","",""),owner_id,
                consent=True,
            )
            db.commit()
        return contact

    def test_owner_cannot_read_other_workspaces_and_users_cannot_read_owner(self):
        owner_account=self.insert_sender(self.owner_id,"OWNER_SECRET_SENDER")
        member_account=self.insert_sender(self.user_id,"MEMBER_SECRET_SENDER")
        owner_contact=self.insert_contact(self.owner_id,"OWNER_PRIVATE_CONTACT","@sharedcontact")
        member_contact=self.insert_contact(self.user_id,"MEMBER_PRIVATE_CONTACT","@sharedcontact")
        with core.database() as db:
            owner_campaign=core.create_campaign(
                db,owner_account,"OWNER_PRIVATE_MESSAGE",[owner_contact],None,self.owner_id
            )
            member_campaign=core.create_campaign(
                db,member_account,"MEMBER_PRIVATE_MESSAGE",[member_contact],None,self.user_id
            )
            db.commit()

        for client, forbidden, own, forbidden_account,own_account,forbidden_cid, own_cid, forbidden_contact in (
            (self.member,"OWNER_PRIVATE", "MEMBER_PRIVATE",owner_account,member_account,owner_campaign,member_campaign,owner_contact),
            (self.owner,"MEMBER_PRIVATE", "OWNER_PRIVATE",member_account,owner_account,member_campaign,owner_campaign,member_contact),
        ):
            for path in ("/","/contacts","/accounts","/compose","/campaigns","/contacts/export"):
                page=client.get(path)
                self.assertEqual(page.status_code,200,path)
                self.assertNotIn(forbidden,page.text,path)
            for path in (f"/accounts/{forbidden_account}/setup",
                         f"/contacts/{forbidden_contact}/edit",
                         f"/campaigns/{forbidden_cid}",
                         f"/api/campaigns/{forbidden_cid}"):
                page=client.get(path)
                self.assertEqual(page.status_code,404,path)
            self.assertEqual(client.get(f"/accounts/{own_account}/setup").status_code,200)
            self.assertEqual(client.get(f"/campaigns/{own_cid}").status_code,200)

        self.assertEqual(self.owner.get("/users").status_code,200)
        self.assertEqual(self.member.get("/users").status_code,403)
        self.assertNotIn("User management", self.member.get("/").text)

    def test_cross_user_write_operations_are_rejected(self):
        owner_account=self.insert_sender(self.owner_id,"Owner account")
        owner_contact=self.insert_contact(self.owner_id,"owner secret","@ownerprivate")
        member_account=self.insert_sender(self.user_id,"Member account")
        with core.database() as db:
            owner_campaign=core.create_campaign(
                db,owner_account,"Original owner's message",[owner_contact],None,self.owner_id
            )
            db.execute("UPDATE campaigns SET state='interrupted' WHERE id=?",(owner_campaign,))
            db.commit()

        edit=self.member.post(
            f"/contacts/{owner_contact}/edit",
            data={"csrf":self.user_csrf,"name":"STOLEN","username":"@stolenxx",
                  "enabled":"yes","consent":"yes"},
            follow_redirects=False,
        )
        self.assertEqual(edit.status_code,404)
        self.assertEqual(self.member.post(
            f"/contacts/{owner_contact}/remove",
            data={"csrf":self.user_csrf},follow_redirects=False,
        ).status_code,404)

        fake_account=self.member.post(
            "/compose/preview",
            data={"csrf":self.user_csrf,"account_id":owner_account,
                  "message":"Cannot send via other account","contacts":str(owner_contact)},
            follow_redirects=False,
        )
        self.assertEqual(fake_account.status_code,303)
        fake_contact=self.member.post(
            "/compose/preview",
            data={"csrf":self.user_csrf,"account_id":member_account,
                  "message":"Cannot target other contacts","contacts":str(owner_contact)},
            follow_redirects=False,
        )
        self.assertEqual(fake_contact.status_code,303)

        os.environ["SEND_ENABLED"]="true"
        with patch("broadcaster.web.telegram.enqueue", side_effect=AssertionError("No sends")) as enqueue:
            self.assertEqual(self.member.post(
                f"/campaigns/{owner_campaign}/send",
                data={"csrf":self.user_csrf},follow_redirects=False,
            ).status_code,404)
            self.assertEqual(self.member.post(
                f"/campaigns/{owner_campaign}/retry",
                data={"csrf":self.user_csrf},follow_redirects=False,
            ).status_code,303)
            enqueue.assert_not_called()

        with patch("broadcaster.web.telegram.request_code",
                   side_effect=AssertionError("No Telegram API")) as request_code:
            self.assertEqual(self.member.post(
                f"/accounts/{owner_account}/request-code",
                data={"csrf":self.user_csrf},follow_redirects=False,
            ).status_code,404)
            request_code.assert_not_called()
        for suffix,data in (
            ("verify-code",{"code":"12345"}),
            ("verify-password",{"password":"test"}),
            ("verify",{}),
        ):
            with patch("broadcaster.web.telegram.verify_code",
                       side_effect=AssertionError("No Telegram API")), \
                 patch("broadcaster.web.telegram.verify_password",
                       side_effect=AssertionError("No Telegram API")), \
                 patch("broadcaster.web.telegram.verify_connected",
                       side_effect=AssertionError("No Telegram API")):
                self.assertEqual(self.member.post(
                    f"/accounts/{owner_account}/{suffix}",
                    data={"csrf":self.user_csrf,**data},follow_redirects=False,
                ).status_code,404)

        with core.database() as db:
            original=db.execute("SELECT state FROM campaigns WHERE id=?",(owner_campaign,)).fetchone()
            contact=db.execute("SELECT name,archived FROM contacts WHERE id=?",(owner_contact,)).fetchone()
            self.assertEqual(original["state"],"interrupted")
            self.assertEqual((contact["name"],contact["archived"]),("owner secret",0))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0],1)

    def test_owner_user_creation_password_change_reset_and_disable(self):
        # Newly created users may access only their own workspace; no owner
        # privileges or access to other users' private data.
        self.assertEqual(self.member.get("/settings/password").status_code,200)
        self.assertEqual(self.member.post(
            "/users",data={"csrf":self.user_csrf,"username":"intruder",
                           "password":"test","confirm_password":"test"},
            follow_redirects=False,
        ).status_code,403)

        wrong=self.member.post(
            "/settings/password",
            data={"csrf":self.user_csrf,"current_password":"wrong",
                  "new_password":"replacement","confirm_password":"replacement"},
            follow_redirects=False,
        )
        self.assertEqual(wrong.status_code,303)
        valid=self.member.post(
            "/settings/password",
            data={"csrf":self.user_csrf,"current_password":"user-password",
                  "new_password":"x","confirm_password":"x"},
            follow_redirects=False,
        )
        self.assertEqual(valid.status_code,303)
        self.assertEqual(self.member.get("/",follow_redirects=False).status_code,303)
        self.user_csrf=self.login(self.member,"colleague","x")

        # Owner must reauthenticate to reset another person's credentials.
        refused=self.owner.post(
            f"/users/{self.user_id}/password",
            data={"csrf":self.owner_csrf,"owner_password":"wrong",
                  "new_password":"newpw","confirm_password":"newpw"},
            follow_redirects=False,
        )
        self.assertEqual(refused.status_code,303)
        allowed=self.owner.post(
            f"/users/{self.user_id}/password",
            data={"csrf":self.owner_csrf,"owner_password":"owner-password",
                  "new_password":"newpw","confirm_password":"newpw"},
            follow_redirects=False,
        )
        self.assertEqual(allowed.status_code,303)
        self.assertEqual(self.member.get("/",follow_redirects=False).status_code,303)
        self.user_csrf=self.login(self.member,"colleague","newpw")

        disabled=self.owner.post(
            f"/users/{self.user_id}/status",
            data={"csrf":self.owner_csrf,"action":"disable"},
            follow_redirects=False,
        )
        self.assertEqual(disabled.status_code,303)
        self.assertEqual(self.member.get("/",follow_redirects=False).status_code,303)
        login_attempt=self.member.post(
            "/login",data={"username":"colleague","password":"newpw"},
            follow_redirects=False,
        )
        self.assertEqual(login_attempt.status_code,303)
        self.assertNotEqual(login_attempt.headers["location"],"/")
        self.assertEqual(self.owner.post(
            f"/users/{self.owner_id}/status",
            data={"csrf":self.owner_csrf,"action":"disable"},
            follow_redirects=False,
        ).status_code,303)
        with core.database() as db:
            self.assertEqual(db.execute("SELECT active FROM admins WHERE id=?",(self.owner_id,)).fetchone()["active"],1)
            self.assertEqual(db.execute("SELECT active FROM admins WHERE id=?",(self.user_id,)).fetchone()["active"],0)
        enabled=self.owner.post(
            f"/users/{self.user_id}/status",
            data={"csrf":self.owner_csrf,"action":"enable"},
            follow_redirects=False,
        )
        self.assertEqual(enabled.status_code,303)
        self.user_csrf=self.login(self.member,"colleague","newpw")

    def test_worker_fails_closed_on_mismatched_sender_ownership(self):
        owner_account=self.insert_sender(self.owner_id,"Owner's sender")
        owner_contact=self.insert_contact(self.owner_id,"Alice","@testisolated")
        with core.database() as db:
            cid=core.create_campaign(
                db,owner_account,"never sent",[owner_contact],None,self.owner_id
            )
            db.execute("UPDATE campaigns SET owner_id=?, state='queued' WHERE id=?",
                       (self.user_id,cid))
            db.commit()
        os.environ["SEND_ENABLED"]="true"
        with patch("broadcaster.telegram.new_client",
                   side_effect=AssertionError("Do not initialize Telethon")) as client:
            asyncio.run(telegram.dispatch(cid))
            client.assert_not_called()
        with core.database() as db:
            self.assertEqual(
                db.execute("SELECT state FROM campaigns WHERE id=?",(cid,)).fetchone()["state"],
                "failed",
            )


class LegacyDatabaseMigrationTests(unittest.TestCase):
    def test_new_ownerless_records_are_not_reassigned_on_restart(self):
        temp=tempfile.TemporaryDirectory()
        original=os.environ.get("DATABASE_PATH")
        os.environ["DATABASE_PATH"]=str(Path(temp.name)/"ownerless.sqlite3")
        try:
            core.init_db()
            core.create_admin("original","password")
            with core.database() as db:
                db.execute(
                    "INSERT INTO contacts(name,username,created_at,updated_at)"
                    " VALUES ('Unowned','@neverassignme',100,100)"
                )
                db.commit()
            with self.assertRaisesRegex(RuntimeError,"ownerless records"):
                core.init_db()
            with core.database() as db:
                row=db.execute("SELECT owner_id FROM contacts WHERE name='Unowned'").fetchone()
                self.assertIsNone(row["owner_id"])
        finally:
            if original is None:
                os.environ.pop("DATABASE_PATH",None)
            else:
                os.environ["DATABASE_PATH"]=original
            temp.cleanup()

    def test_old_database_is_migrated_without_losing_existing_accounts(self):
        temp=tempfile.TemporaryDirectory()
        old_db=os.environ.get("DATABASE_PATH")
        path=Path(temp.name)/"legacy.sqlite3"
        os.environ["DATABASE_PATH"]=str(path)
        try:
            # Represent the actual pre-multiuser schema, before owner_id/role.
            with core.database() as db:
                db.executescript(core.SCHEMA)
                salt=bytes(range(16))
                pwd=core.password_digest("password",salt)
                db.execute(
                    "INSERT INTO admins(id,username,password_salt,password_digest,created_at)"
                    " VALUES (1,'original',?,?,100)",(salt.hex(),pwd),
                )
                db.execute(
                    "INSERT INTO admins(id,username,password_salt,password_digest,created_at)"
                    " VALUES (2,'other',?,?,200)",(salt.hex(),pwd),
                )
                sender=uuid.uuid4().hex
                db.execute(
                    "INSERT INTO accounts(id,label,api_id,api_hash_enc,phone_enc,"
                    "session_name,state,created_at,updated_at) "
                    "VALUES (?,'Legacy sender',123,'encrypted1','encrypted2',?,'connected',100,100)",
                    (sender,sender),
                )
                db.execute(
                    "INSERT INTO contacts(id,name,username,enabled,consent,created_at,updated_at)"
                    " VALUES (5,'Legacy contact','@legacycontact',1,1,100,100)"
                )
                db.execute(
                    "INSERT INTO campaigns(id,account_id,message,state,created_at,total)"
                    " VALUES ('legacy-campaign',?,'Original message','draft',100,1)",(sender,)
                )
                db.execute(
                    "INSERT INTO deliveries(campaign_id,contact_id,name,destination,updated_at)"
                    " VALUES ('legacy-campaign',5,'Legacy contact','@legacycontact',100)"
                )
                db.commit()
            core.init_db()
            core.init_db()  # Repeatable, does not reassign records.
            with core.database() as db:
                roles=db.execute("SELECT id,role,active FROM admins ORDER BY id").fetchall()
                self.assertEqual([(r["role"],r["active"]) for r in roles],
                                 [("owner",1),("user",1)])
                for table in ("accounts","contacts","campaigns"):
                    r=db.execute(f"SELECT owner_id FROM {table}").fetchone()
                    self.assertEqual(r["owner_id"],1,table)
                self.assertEqual(
                    db.execute("SELECT message FROM campaigns WHERE id='legacy-campaign'").fetchone()[0],
                    "Original message",
                )
                self.assertIsNone(core.campaign_summary(db,"legacy-campaign",2)[0])
                self.assertEqual(len(core.campaign_summary(db,"legacy-campaign",1)[1]),1)
                self.assertIsNone(db.execute("SELECT owner_id FROM campaigns WHERE owner_id IS NULL").fetchone())
        finally:
            if old_db is None:
                os.environ.pop("DATABASE_PATH",None)
            else:
                os.environ["DATABASE_PATH"]=old_db
            temp.cleanup()


if __name__=="__main__":
    unittest.main()
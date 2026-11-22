"""Offline tests never connect to Telegram or send messages."""
import os
import tempfile
import unittest
from pathlib import Path
from cryptography.fernet import Fernet
from broadcaster import core

class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old={k:os.environ.get(k) for k in ("DATABASE_PATH","APP_ENCRYPTION_KEY")}
        os.environ["DATABASE_PATH"]=str(Path(self.temp.name)/"test.sqlite3")
        os.environ["APP_ENCRYPTION_KEY"]=Fernet.generate_key().decode()
        core.init_db()
    def tearDown(self):
        for k,v in self.old.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        self.temp.cleanup()

    def test_encryption_roundtrip(self):
        encrypted=core.encrypt("secret-telegram-hash")
        self.assertNotIn("secret-telegram-hash",encrypted)
        self.assertEqual(core.decrypt(encrypted),"secret-telegram-hash")

    def test_password_hash_not_plaintext(self):
        core.create_admin("joe-admin","a long strong password!123")
        with core.database() as db:
            row=db.execute("SELECT * FROM admins").fetchone()
            self.assertTrue(core.validate_password(row,"a long strong password!123"))
            self.assertFalse(core.validate_password(row,"another_password"))
            self.assertNotIn("password",row["password_digest"])
            session,csrf=core.new_session(db,row["id"])
            db.commit()
            self.assertIsNotNone(core.session(db,session))
            self.assertTrue(core.check_csrf(csrf,csrf))
            self.assertFalse(core.check_csrf(csrf,"invalid"))
            core.revoke_session(db,session)
            db.commit()
            self.assertIsNone(core.session(db,session))

    def test_contact_normalization_and_opt_in_default(self):
        entry=core.normalize_contact(" Alice ","Alice_12345","","","intro")
        self.assertEqual(entry["username"],"@alice_12345")
        with core.database() as db:
            cid=core.insert_contact(db,entry)
            db.commit()
            c=db.execute("SELECT * FROM contacts WHERE id=?",(cid,)).fetchone()
            self.assertEqual(c["consent"],0)
            with self.assertRaises(ValueError):
                core.insert_contact(db,entry)

    def test_reject_invalid_identifier(self):
        for name,username,phone,uid in [
            ("Joe","joe","",""),
            ("Joe","","not-a-phone",""),
            ("Joe","","","14"),
            ("Joe","","",""),
        ]:
            with self.assertRaises(ValueError):
                core.normalize_contact(name,username,phone,uid,"")

    def test_campaign_requires_eligible_contact_and_connected_sender(self):
        import uuid
        uid=uuid.uuid4().hex
        with core.database() as db:
            db.execute(
                """INSERT INTO accounts(id,label,api_id,api_hash_enc,phone_enc,username,session_name,state,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,'connected',?,?)""",
                (uid,"Test",123456,core.encrypt("hash"),core.encrypt("+420123456789"),"@sender",uid,core.now(),core.now()),
            )
            contact=core.normalize_contact("Alice","@aliceexample","","","")
            cid=core.insert_contact(db,contact,consent=False)
            db.commit()
            with self.assertRaises(ValueError):
                core.create_campaign(db,uid,"Hi!",[cid],3)
            db.execute("UPDATE contacts SET consent=1 WHERE id=?",(cid,))
            camp=core.create_campaign(db,uid,"Hi!",[cid],3)
            db.commit()
            snap,recipients=core.campaign_summary(db,camp)
            self.assertEqual(snap["state"],"draft")
            self.assertEqual(len(recipients),1)
            self.assertEqual(recipients[0]["destination"],"@aliceexample")
            with self.assertRaises(ValueError):
                core.create_campaign(db,uid,"Hi!",[cid,cid],3)
            with self.assertRaises(ValueError):
                core.create_campaign(db,uid,"Hi!",[cid,cid,cid,cid],3)

    def test_uncertain_campaign_never_auto_retried(self):
        import uuid
        uid=uuid.uuid4().hex
        with core.database() as db:
            db.execute(
                "INSERT INTO accounts(id,label,api_id,api_hash_enc,phone_enc,session_name,state,created_at,updated_at) VALUES (?,?,?,?,?,?,'connected',?,?)",
                (uid,"Test",123,core.encrypt("hash"),core.encrypt("+420123456789"),uid,core.now(),core.now()),
            )
            cid=core.insert_contact(db,core.normalize_contact("Bob","@bobexample","","",""),consent=True)
            campaign=core.create_campaign(db,uid,"hello",[cid],3)
            db.execute("UPDATE campaigns SET state='sending' WHERE id=?",(campaign,))
            db.execute("UPDATE deliveries SET state='sending' WHERE campaign_id=?",(campaign,))
            db.commit()
        core.init_db()
        with core.database() as db:
            self.assertEqual(db.execute("SELECT state FROM campaigns WHERE id=?",(campaign,)).fetchone()["state"],"interrupted")
            self.assertEqual(db.execute("SELECT state FROM deliveries WHERE campaign_id=?",(campaign,)).fetchone()["state"],"unknown")

if __name__=="__main__":unittest.main()

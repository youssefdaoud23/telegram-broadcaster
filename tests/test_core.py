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

    def test_administrator_password_has_no_length_policy(self):
        passwords = ("x", "1234", "z" * 300)
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            core.create_admin("empty-pass", "")
        for index, password in enumerate(passwords):
            core.create_admin(f"custom{index}", password)
            with core.database() as db:
                admin = db.execute("SELECT * FROM admins WHERE username=?", (f"custom{index}",)).fetchone()
                self.assertTrue(core.validate_password(admin, password))
                self.assertFalse(core.validate_password(admin, password + "wrong"))

    def test_duplicate_administrator_is_friendly_and_reset_is_safe(self):
        core.create_admin("admin", "old-password")
        core.create_admin("another", "another-password")
        with self.assertRaisesRegex(ValueError, "already exists"):
            core.create_admin("ADMIN", "new-password")
        with core.database() as db:
            admin = db.execute("SELECT * FROM admins WHERE username='admin'").fetchone()
            token, _ = core.new_session(db, admin["id"])
            core.failed_login(db, "admin")
            db.commit()
        core.reset_admin_password("ADMIN", "x")
        with core.database() as db:
            changed = db.execute("SELECT * FROM admins WHERE username='admin'").fetchone()
            untouched = db.execute("SELECT * FROM admins WHERE username='another'").fetchone()
            self.assertTrue(core.validate_password(changed, "x"))
            self.assertFalse(core.validate_password(changed, "old-password"))
            self.assertTrue(core.validate_password(untouched, "another-password"))
            self.assertIsNone(core.session(db, token))
            self.assertIsNone(db.execute("SELECT username FROM login_attempts WHERE username='admin'").fetchone())
        with self.assertRaisesRegex(ValueError, "not found"):
            core.reset_admin_password("missing", "test")
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            core.reset_admin_password("admin", "")

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

    def test_optional_limit_configuration(self):
        old = os.environ.pop("SEND_MAX_RECIPIENTS", None)
        try:
            self.assertIsNone(core.recipient_limit())
            for value in ("", "0", "UNLIMITED", "none"):
                os.environ["SEND_MAX_RECIPIENTS"] = value
                self.assertIsNone(core.recipient_limit())
            for value, expected in (("1", 1), ("20", 20), ("1200", 1200)):
                os.environ["SEND_MAX_RECIPIENTS"] = value
                self.assertEqual(core.recipient_limit(), expected)
            for bad in ("-1", "what", "2.5"):
                os.environ["SEND_MAX_RECIPIENTS"] = bad
                with self.assertRaisesRegex(ValueError, "SEND_MAX_RECIPIENTS"):
                    core.recipient_limit()
        finally:
            if old is None:
                os.environ.pop("SEND_MAX_RECIPIENTS", None)
            else:
                os.environ["SEND_MAX_RECIPIENTS"] = old

    def test_campaign_can_exceed_original_100_limit_when_unset(self):
        import uuid
        account_id=uuid.uuid4().hex
        with core.database() as db:
            db.execute(
                "INSERT INTO accounts(id,label,api_id,api_hash_enc,phone_enc,session_name,state,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,'connected',?,?)",
                (account_id, "Offline", 123, core.encrypt("hash"), core.encrypt("+420123456789"),
                 account_id, core.now(), core.now()),
            )
            ids=[]
            for index in range(113):
                ids.append(core.insert_contact(db,core.normalize_contact(
                    f"Sample{index}",f"@capacitytest{index}","","",""
                ), consent=True))
            with self.assertRaisesRegex(ValueError, "no more than 3"):
                core.create_campaign(db, account_id, "test", ids, 3)
            campaign=core.create_campaign(db, account_id, "test", ids, None)
            db.commit()
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM deliveries WHERE campaign_id=?", (campaign,)
            ).fetchone()[0], 113)
            db.execute("UPDATE campaigns SET state='interrupted' WHERE id=?", (campaign,))
            db.commit()
        with core.database() as db:
            retried,created=core.create_retry_draft(db,campaign,None)
            db.commit()
            self.assertTrue(created)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM deliveries WHERE campaign_id=?", (retried,)
            ).fetchone()[0],113)
            self.assertEqual(db.execute(
                "SELECT state FROM campaigns WHERE id=?", (retried,)
            ).fetchone()[0],"draft")

    def test_retry_draft_only_copies_pending_opted_in_contacts(self):
        import uuid
        sender = uuid.uuid4().hex
        with core.database() as db:
            db.execute(
                "INSERT INTO accounts(id,label,api_id,api_hash_enc,phone_enc,session_name,state,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,'connected',?,?)",
                (sender,"Offline sender",123,core.encrypt("hash"),core.encrypt("+420123456789"),
                 sender,core.now(),core.now()),
            )
            ids=[]
            for n in range(5):
                ids.append(core.insert_contact(
                    db,core.normalize_contact(
                        f"Test{n}",f"@pendingtest{n}","", "",""
                    ),consent=True,
                ))
            original=core.create_campaign(db,sender,"Original text",ids,5)
            states=["sent","unknown","failed","pending","pending"]
            for cid,state in zip(ids,states):
                db.execute(
                    "UPDATE deliveries SET state=? WHERE campaign_id=? AND contact_id=?",
                    (state,original,cid),
                )
            db.execute("UPDATE contacts SET enabled=0 WHERE id=?",(ids[4],))
            db.execute("UPDATE campaigns SET state='interrupted' WHERE id=?",(original,))
            db.commit()

        with core.database() as db:
            self.assertEqual(core.retryable_count(db,original),1)
            retry, created=core.create_retry_draft(db,original,3)
            db.commit()
            self.assertTrue(created)
            self.assertNotEqual(retry,original)
            old=db.execute("SELECT state FROM campaigns WHERE id=?",(original,)).fetchone()
            new=db.execute("SELECT state,account_id,message,total FROM campaigns WHERE id=?",(retry,)).fetchone()
            delivery=db.execute("SELECT contact_id,state FROM deliveries WHERE campaign_id=?",(retry,)).fetchall()
            self.assertEqual(old["state"],"interrupted")
            self.assertEqual(new["state"],"draft")
            self.assertEqual(new["total"],1)
            self.assertEqual(new["message"],"Original text")
            self.assertEqual(new["account_id"],sender)
            self.assertEqual([(d["contact_id"],d["state"]) for d in delivery],[(ids[3],"pending")])
        with core.database() as db:
            duplicate, created=core.create_retry_draft(db,original,3)
            db.commit()
            self.assertFalse(created)
            self.assertEqual(duplicate,retry)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM campaign_retries").fetchone()[0],1)

    def test_retry_draft_rejects_uncertain_and_enforces_limit(self):
        import uuid
        sender=uuid.uuid4().hex
        with core.database() as db:
            db.execute(
                "INSERT INTO accounts(id,label,api_id,api_hash_enc,phone_enc,session_name,state,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,'connected',?,?)",
                (sender,"Offline sender",123,core.encrypt("hash"),core.encrypt("+420123456789"),
                 sender,core.now(),core.now()),
            )
            ids=[]
            for n in range(2):
                ids.append(core.insert_contact(db,core.normalize_contact(
                    f"Contact{n}",f"@retrycontact{n}","","",""
                ),consent=True))
            uncertain=core.create_campaign(db,sender,"Uncertain",[ids[0]],3)
            db.execute("UPDATE campaigns SET state='interrupted' WHERE id=?",(uncertain,))
            db.execute("UPDATE deliveries SET state='unknown' WHERE campaign_id=?",(uncertain,))
            too_many=core.create_campaign(db,sender,"Pending",[ids[0],ids[1]],3)
            db.execute("UPDATE campaigns SET state='interrupted' WHERE id=?",(too_many,))
            db.commit()

        with core.database() as db:
            with self.assertRaisesRegex(ValueError,"No safely retryable"):
                core.create_retry_draft(db,uncertain,3)
        with core.database() as db:
            with self.assertRaisesRegex(ValueError,"per-broadcast limit is 1"):
                core.create_retry_draft(db,too_many,1)
        with core.database() as db:
            new_id,created=core.create_retry_draft(db,too_many,2)
            db.commit()
            self.assertTrue(created)
            self.assertEqual(db.execute("SELECT total FROM campaigns WHERE id=?",(new_id,)).fetchone()[0],2)
            with self.assertRaisesRegex(ValueError,"Only interrupted"):
                core.create_retry_draft(db,new_id,2)

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
        core.init_db()  # Ordinary maintenance/CLI initialization must not interrupt work.
        with core.database() as db:
            self.assertEqual(db.execute("SELECT state FROM campaigns WHERE id=?",(campaign,)).fetchone()["state"],"sending")
        core.init_db(recover_inflight=True)  # Only actual app startup recovers orphaned work.
        with core.database() as db:
            self.assertEqual(db.execute("SELECT state FROM campaigns WHERE id=?",(campaign,)).fetchone()["state"],"interrupted")
            self.assertEqual(db.execute("SELECT state FROM deliveries WHERE campaign_id=?",(campaign,)).fetchone()["state"],"unknown")

if __name__=="__main__":unittest.main()
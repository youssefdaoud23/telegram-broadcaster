"""Portable self-hosted installation checks, offline and non-destructive."""
from __future__ import annotations

import base64
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet


ROOT=Path(__file__).resolve().parents[1]
CONFIG_SCRIPT=ROOT/"scripts"/"configure.py"
TEMPLATE=ROOT/".env.example"


class PortableSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.directory=Path(self.tmp.name)
        scripts=self.directory/"scripts"
        scripts.mkdir()
        (scripts/"configure.py").write_text(CONFIG_SCRIPT.read_text(encoding="utf8"), encoding="utf8")
        (self.directory/".env.example").write_text(TEMPLATE.read_text(encoding="utf8"), encoding="utf8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_setup(self,*args):
        return subprocess.run(
            [sys.executable,str(self.directory/"scripts"/"configure.py"),*args],
            cwd=self.directory,text=True,capture_output=True,timeout=8,
        )

    def config(self):
        return dict(
            line.split("=",1) for line in
            (self.directory/".env").read_text(encoding="utf8").splitlines()
            if line and not line.startswith("#") and "=" in line
        )

    def test_fresh_install_generates_private_key_and_local_only_defaults(self):
        result=self.run_setup()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.count("Created .env"),1)
        data=self.config()
        self.assertTrue(Fernet(data["APP_ENCRYPTION_KEY"].encode()))
        self.assertEqual(len(base64.urlsafe_b64decode(data["APP_ENCRYPTION_KEY"])),32)
        self.assertEqual(data["BROADCASTER_BIND_IP"],"127.0.0.1")
        self.assertEqual(data["BROADCASTER_PORT"],"8789")
        self.assertEqual(data["SEND_ENABLED"],"false")
        self.assertEqual(data["COOKIE_SECURE"],"false")
        self.assertEqual(data["PUBLIC_BASE_URL"],"http://localhost:8789")
        self.assertEqual(data["DATABASE_PATH"],"/data/broadcaster.sqlite3")
        self.assertTrue((self.directory/"data").is_dir())
        if os.name=="posix":
            self.assertEqual(stat.S_IMODE((self.directory/".env").stat().st_mode),0o600)
        self.assertNotIn(data["APP_ENCRYPTION_KEY"],result.stdout)

    def test_custom_port_and_https_domain(self):
        result=self.run_setup("--port","9898","--public-url","https://broadcast.example.net")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.config()["BROADCASTER_PORT"],"9898")
        self.assertEqual(self.config()["PUBLIC_BASE_URL"],"https://broadcast.example.net")
        self.assertEqual(self.config()["COOKIE_SECURE"],"true")

    def test_wide_host_binding_requires_explicit_https_origin(self):
        result=self.run_setup("--bind-address","0.0.0.0")
        self.assertNotEqual(result.returncode,0)
        self.assertIn("requires --public-url",result.stderr)
        self.assertFalse((self.directory/".env").exists())
        good=self.run_setup("--bind-address","0.0.0.0","--public-url","https://site.example.org")
        self.assertEqual(good.returncode,0,good.stderr)

    def test_existing_private_key_never_overwritten(self):
        self.assertEqual(self.run_setup().returncode,0)
        old=(self.directory/".env").read_bytes()
        again=self.run_setup("--port","9001")
        self.assertEqual(again.returncode,1)
        self.assertIn("Refusing to overwrite",again.stderr)
        self.assertEqual((self.directory/".env").read_bytes(),old)

    def test_existing_data_without_encryption_key_fails_closed(self):
        target=self.directory/"data"
        target.mkdir()
        (target/"broadcaster.sqlite3").write_bytes(b"existing important data")
        attempt=self.run_setup()
        self.assertEqual(attempt.returncode,1)
        self.assertIn("Recover the original",attempt.stderr)
        self.assertFalse((self.directory/".env").exists())
        self.assertEqual((target/"broadcaster.sqlite3").read_bytes(),b"existing important data")

    def test_invalid_hostname_port_public_url_and_symlink_are_rejected(self):
        for argv in (
            ("--port","0"),
            ("--port","65536"),
            ("--bind-address","host.example.com"),
            ("--public-url","http://example.com"),
            ("--public-url","https://example.com/path"),
            ("--public-url","https://alice:secret@example.com"),
        ):
            with self.subTest(argv=argv):
                result=self.run_setup(*argv)
                self.assertNotEqual(result.returncode,0)
                self.assertFalse((self.directory/".env").exists())
        original=self.directory/"real-data"
        original.mkdir()
        (self.directory/"data").symlink_to(original,target_is_directory=True)
        refused=self.run_setup()
        self.assertEqual(refused.returncode,1)
        self.assertIn("symlinked",refused.stderr)

    def test_documentation_and_compose_have_portability_hooks(self):
        compose=(ROOT/"compose.yaml").read_text(encoding="utf8")
        volume=(ROOT/"compose.volume.yaml").read_text(encoding="utf8")
        dockerfile=(ROOT/"Dockerfile").read_text(encoding="utf8")
        readme=(ROOT/"README.md").read_text(encoding="utf8")
        self.assertIn("${BROADCASTER_BIND_IP:-0.0.0.0}",compose)
        self.assertIn("${BROADCASTER_PORT:-8789}",compose)
        self.assertIn("healthcheck:",compose)
        self.assertIn("cap_drop: [ALL]",compose)
        self.assertIn("broadcaster_data:/data",volume)
        for filename in (
            "README.md","docs/INSTALL.md","docs/TROUBLESHOOTING.md",
            ".github/workflows/ci.yml","scripts/configure.py",
        ):
            self.assertIn(
                "--user root --cap-add CHOWN broadcaster",
                (ROOT/filename).read_text(encoding="utf8"),
                filename,
            )
        self.assertIn("install -d -m 0700 -o 10001 -g 10001 /data",dockerfile)
        for title in ("INSTALL","OPERATIONS","CONFIGURATION","SECURITY","TROUBLESHOOTING"):
            self.assertTrue((ROOT/"docs"/(title+".md")).exists())
            self.assertIn("docs/"+title+".md",readme)


if __name__=="__main__":
    unittest.main()

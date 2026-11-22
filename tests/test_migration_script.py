"""Static guards for the offline-tested migration entrypoint.

This test NEVER executes the move or touches the production container.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


class MigrationScriptTests(unittest.TestCase):
    def test_migration_script_is_syntactically_valid_and_scoped(self):
        source=Path(__file__).resolve().parents[1]/"scripts"/"migrate_to_opt.sh"
        text=source.read_text("utf8")
        bash=shutil.which("bash")
        if bash:
            result=subprocess.run([bash,"-n",str(source)],
                                  capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
        for expected in (
            "OLD=/root/n8n/telegram-broadcaster",
            "NEW=/opt/telegram-broadcaster",
            "trap rollback EXIT",
            'read_counts "$OLD"',
            'docker compose -f "$OLD/compose.yaml" stop broadcaster',
            'mv -- "$OLD" "$NEW"',
            'docker compose -f "$NEW/compose.yaml" up -d --no-build --force-recreate broadcaster',
            "PRAGMA integrity_check",
            "db.backup(copy)",
            '[[ "$before_counts" == "$after_counts" ]]',
            "http://127.0.0.1:8789/health",
            "sha256sum",
            "data_inode",
        ):
            self.assertIn(expected,text,expected)
        self.assertNotIn("down -v",text)
        self.assertNotIn("rm -rf",text)
        self.assertNotIn("docker system prune",text)
        self.assertNotIn("telegram-personal-bridge",text)
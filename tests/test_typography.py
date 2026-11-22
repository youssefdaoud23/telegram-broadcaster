"""Typography guard for the source, templates and project documentation."""
import unittest
from pathlib import Path


class TypographyTests(unittest.TestCase):
    def test_no_unicode_em_dash(self):
        root=Path(__file__).resolve().parents[1]
        directories=[root/"broadcaster",root/"tests"]
        files=[]
        for folder in directories:
            files.extend(
                p for p in folder.rglob("*")
                if p.is_file() and "__pycache__" not in p.parts
                and p.suffix in {".py",".html",".css",".js",".md"}
            )
        files.extend(root.glob("*.md"))
        files.extend([root/"compose.yaml",root/".env.example",root/"Dockerfile"])
        bad=[str(p.relative_to(root)) for p in files
             if p.exists() and chr(0x2014) in p.read_text("utf-8")]
        self.assertFalse(bad, "Use ASCII hyphens, not Unicode em dashes: "+", ".join(bad))
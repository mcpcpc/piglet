import tempfile
import unittest
from pathlib import Path
from unittest import mock

from piglet.runtime import export
from piglet.sessions.store import SessionFile


def make_session(directory):
    session = SessionFile.create(Path("/proj"), directory)
    session.append_message({"role": "user", "content": "hi </script> there", "timestamp": 1})
    session.append("session_info", {"name": "Demo"})
    return session


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_jsonl_round_trip(self):
        session = make_session(self.root / "s")
        text = export.export_jsonl(session)
        self.assertEqual(text, session.path.read_text())
        self.assertTrue(export.export_filename(session, "html").endswith(".html"))
        self.assertTrue(
            export.export_filename(SessionFile.create(Path("/p"), None), "jsonl").startswith(
                "piglet-session-"
            )
        )

    def test_inline_assets(self):
        assets = self.root / "assets"
        assets.mkdir()
        (assets / "v.js").write_text("console.log('</script>')")
        (assets / "v.css").write_text("body{color:red}")
        page = (
            '<html><head><script type="module" crossorigin src="/assets/v.js"></script>'
            '<link rel="stylesheet" crossorigin href="/assets/v.css"></head><body>'
            "<!--PIGLET_SESSION_DATA--></body></html>"
        )
        inlined = export.inline_assets(page, self.root)
        self.assertIn("<style>body{color:red}</style>", inlined)
        self.assertIn("console.log('<\\/script>')", inlined)
        self.assertNotIn("src=", inlined)
        self.assertIsNone(
            export.inline_assets(
                '<script type="module" src="/assets/missing.js"></script>', self.root
            )
        )

    def test_html_uses_template_and_escapes_data(self):
        session = make_session(None)
        template = "<html><!--PIGLET_SESSION_DATA--></html>"
        with mock.patch.object(export, "_template", return_value=template):
            page = export.export_html(session)
        self.assertIn('<script id="piglet-session" type="application/json">', page)
        self.assertIn("hi <\\/script> there", page)

    def test_html_fallback_without_template(self):
        session = make_session(None)
        with mock.patch.object(export, "_template", return_value=None):
            page = export.export_html(session)
        self.assertIn("<h1>Demo</h1>", page)
        self.assertIn("hi &lt;/script&gt; there", page)

import asyncio
import base64
import io
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from piglet.abort import AbortSignal
from piglet.tools import truncate
from piglet.tools.base import ToolContext, ToolError, definition, mutated_path
from piglet.tools.bash import BashTool
from piglet.tools.edit import EditTool, apply_edits, normalize_for_fuzzy_match
from piglet.tools.paths import display_path, resolve_path
from piglet.tools.process import OutputBuffer, find_shell
from piglet.tools.read import ReadTool, read_text_result, resize_image
from piglet.tools.registry import builtin_tools, enabled_tool_names
from piglet.tools.search import FindTool, GrepTool, LsTool, glob_matches
from piglet.tools.write import WriteTool


def run(coro):
    return asyncio.run(coro)


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def ctx(self, **kw):
        return ToolContext(self.cwd, **kw)


class TruncateTest(unittest.TestCase):
    def test_head_by_lines_and_bytes(self):
        text = "\n".join(str(i) for i in range(10))
        result = truncate.truncate_head(text, max_lines=3)
        self.assertEqual(result.content, "0\n1\n2")
        self.assertEqual(result.truncated_by, "lines")
        result = truncate.truncate_head("aaaa\nbbbb", max_bytes=6)
        self.assertEqual((result.content, result.truncated_by), ("aaaa", "bytes"))
        self.assertTrue(truncate.truncate_head("x" * 10, max_bytes=5).first_line_exceeds_limit)
        self.assertFalse(truncate.truncate_head("ok\n").truncated)

    def test_tail(self):
        result = truncate.truncate_tail("1\n2\n3\n4", max_lines=2)
        self.assertEqual(result.content, "3\n4")
        result = truncate.truncate_tail("x" * 10, max_bytes=4)
        self.assertEqual(result.content, "xxxx")

    def test_format_size_and_line(self):
        self.assertEqual(truncate.format_size(10), "10B")
        self.assertEqual(truncate.format_size(2048), "2.0KB")
        self.assertEqual(truncate.format_size(3 * 1024 * 1024), "3.0MB")
        self.assertTrue(truncate.truncate_line("x" * 600).endswith("[truncated]"))


class PathTest(TmpCase):
    def test_resolve(self):
        self.assertEqual(resolve_path("@src/a.py", self.cwd), self.cwd / "src/a.py")
        self.assertEqual(resolve_path("/abs", self.cwd), Path("/abs"))
        self.assertEqual(display_path(self.cwd / "a", self.cwd), "a")
        self.assertEqual(display_path(Path("/elsewhere"), self.cwd), "/elsewhere")


class ReadTest(TmpCase):
    def test_reads_with_offset_and_limit(self):
        (self.cwd / "f.txt").write_text("\n".join(f"line{i}" for i in range(1, 11)) + "\n")
        result = run(
            ReadTool().execute("1", {"path": "f.txt", "offset": 3, "limit": 2}, self.ctx())
        )
        self.assertEqual(
            result.content[0]["text"],
            "line3\nline4\n\n[6 more lines in file. Use offset=5 to continue.]",
        )

    def test_missing_and_offset_errors(self):
        with self.assertRaises(ToolError):
            run(ReadTool().execute("1", {"path": "nope"}, self.ctx()))
        with self.assertRaises(ToolError):
            read_text_result("a\n", "f", 5, None)

    def test_truncation_notice(self):
        text = "\n".join("x" for _ in range(2500))
        result = read_text_result(text, "f", None, None)
        self.assertIn(
            "[Showing lines 1-2000 of 2500. Use offset=2001 to continue.]",
            result.content[0]["text"],
        )
        long_line = read_text_result("y" * 60000, "f", None, None)
        self.assertIn("exceeds 50.0KB limit", long_line.content[0]["text"])

    def test_image_resize_and_block(self):
        image = Image.new("RGB", (3000, 1000), "red")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        (self.cwd / "big.png").write_bytes(buffer.getvalue())
        result = run(ReadTool().execute("1", {"path": "big.png"}, self.ctx()))
        data = base64.b64decode(result.content[1]["data"])
        self.assertEqual(Image.open(io.BytesIO(data)).size, (2000, 667))
        blocked = run(ReadTool(block_images=True).execute("1", {"path": "big.png"}, self.ctx()))
        self.assertIn("blocked", blocked.content[0]["text"])
        self.assertEqual(resize_image(b"not an image", "image/png"), (b"not an image", "image/png"))


class WriteEditTest(TmpCase):
    def test_write_creates_parents(self):
        tool = WriteTool()
        result = run(tool.execute("1", {"path": "a/b/c.txt", "content": "hi"}, self.ctx()))
        self.assertEqual((self.cwd / "a/b/c.txt").read_text(), "hi")
        self.assertTrue(result.details["created"])
        self.assertEqual(mutated_path(tool, {"path": "x"}, self.cwd), self.cwd / "x")
        self.assertIsNone(mutated_path(ReadTool(), {"path": "x"}, self.cwd))

    def test_edit_multi_and_preserves_crlf_and_bom(self):
        path = self.cwd / "f.txt"
        path.write_bytes("﻿alpha\r\nbeta\r\ngamma\r\n".encode())
        args = {
            "path": "f.txt",
            "edits": [
                {"oldText": "alpha", "newText": "ALPHA"},
                {"oldText": "gamma", "newText": "GAMMA"},
            ],
        }
        result = run(EditTool().execute("1", args, self.ctx()))
        self.assertEqual(path.read_bytes().decode(), "﻿ALPHA\r\nbeta\r\nGAMMA\r\n")
        self.assertIn("-alpha", result.details["diff"])
        self.assertEqual(result.details["firstChangedLine"], 1)
        self.assertEqual(result.content[0]["text"], "Successfully replaced 2 block(s) in f.txt.")

    def test_edit_legacy_form_and_missing_file(self):
        (self.cwd / "g.txt").write_text("one two")
        run(
            EditTool().execute("1", {"path": "g.txt", "oldText": "two", "newText": "2"}, self.ctx())
        )
        self.assertEqual((self.cwd / "g.txt").read_text(), "one 2")
        with self.assertRaises(ToolError):
            run(
                EditTool().execute(
                    "1",
                    {"path": "missing", "edits": [{"oldText": "a", "newText": "b"}]},
                    self.ctx(),
                )
            )
        with self.assertRaises(ToolError):
            run(EditTool().execute("1", {"path": "g.txt"}, self.ctx()))

    def test_apply_edits_errors(self):
        with self.assertRaisesRegex(ToolError, "Found 2 occurrences"):
            apply_edits("a a", [{"oldText": "a", "newText": "b"}], "f")
        with self.assertRaisesRegex(ToolError, "Could not find"):
            apply_edits("abc", [{"oldText": "zzz", "newText": "b"}], "f")
        with self.assertRaisesRegex(ToolError, "overlap"):
            apply_edits(
                "abcdef",
                [{"oldText": "abcd", "newText": "x"}, {"oldText": "cdef", "newText": "y"}],
                "f",
            )
        with self.assertRaisesRegex(ToolError, "No changes"):
            apply_edits("abc", [{"oldText": "abc", "newText": "abc"}], "f")
        with self.assertRaisesRegex(ToolError, "must not be empty"):
            apply_edits("abc", [{"oldText": "", "newText": "x"}], "f")

    def test_fuzzy_match(self):
        content = "say “hello”   \nnext line\n"
        result = apply_edits(
            content, [{"oldText": 'say "hello"\nnext line', "newText": "replaced"}], "f"
        )
        self.assertEqual(result, "replaced\n")
        self.assertEqual(normalize_for_fuzzy_match("a—b c  "), "a-b c")


@unittest.skipIf(sys.platform == "win32", "POSIX shell tests")
class BashTest(TmpCase):
    def tool(self):
        return BashTool(find_shell())

    def test_runs_command_and_streams(self):
        updates = []
        ctx = self.ctx(on_update=updates.append)
        result = run(self.tool().execute("1", {"command": "echo hello; echo err >&2"}, ctx))
        self.assertEqual(result.content[0]["text"], "hello\nerr\n")
        self.assertFalse(result.is_error)
        self.assertTrue(updates)

    def test_exit_code_is_error(self):
        result = run(self.tool().execute("1", {"command": "exit 3"}, self.ctx()))
        self.assertTrue(result.is_error)
        self.assertIn("Command exited with code 3", result.content[0]["text"])
        self.assertIn("(no output)", result.content[0]["text"])

    def test_timeout_and_abort(self):
        with self.assertRaisesRegex(ToolError, "timed out"):
            run(self.tool().execute("1", {"command": "sleep 5", "timeout": 0.2}, self.ctx()))

        async def abort_soon():
            signal = AbortSignal()
            asyncio.get_running_loop().call_later(0.2, signal.abort)
            return await self.tool().execute("1", {"command": "sleep 5"}, self.ctx(signal=signal))

        with self.assertRaisesRegex(ToolError, "aborted"):
            run(abort_soon())

    def test_invalid_timeout_and_cwd(self):
        with self.assertRaises(ToolError):
            run(self.tool().execute("1", {"command": "true", "timeout": -1}, self.ctx()))
        with self.assertRaises(ToolError):
            run(self.tool().execute("1", {"command": "true", "timeout": 10**9}, self.ctx()))
        with self.assertRaisesRegex(ToolError, "Working directory"):
            run(self.tool().execute("1", {"command": "true"}, ToolContext(self.cwd / "gone")))

    def test_large_output_truncated_to_tail(self):
        result = run(self.tool().execute("1", {"command": "seq 1 3000"}, self.ctx()))
        text = result.content[0]["text"]
        self.assertTrue(text.startswith("1001\n"))
        self.assertIn("Full output:", text)
        self.assertTrue(Path(result.details["fullOutputPath"]).exists())
        self.assertIn("powershell", BashTool(["powershell.exe", "-Command"]).description.lower())

    def test_output_buffer_spills(self):
        buffer = OutputBuffer(self.cwd)
        buffer.add("x\n" * 3000)
        buffer.add("tail")
        self.assertTrue(Path(buffer.spill_path).read_text().endswith("tail"))


class SearchTest(TmpCase):
    def setUp(self):
        super().setUp()
        (self.cwd / ".gitignore").write_text("build/\n*.log\n")
        (self.cwd / "src").mkdir()
        (self.cwd / "src" / "a.py").write_text("x = 1\n# TODO: fix\ny = 2\n")
        (self.cwd / "src" / "b.ts").write_text("todo later\n")
        (self.cwd / "build").mkdir()
        (self.cwd / "build" / "c.py").write_text("TODO hidden\n")
        (self.cwd / "debug.log").write_text("TODO log\n")

    def test_grep_respects_gitignore_and_options(self):
        result = run(GrepTool().execute("1", {"pattern": "TODO"}, self.ctx()))
        self.assertEqual(result.content[0]["text"], "src/a.py:2:# TODO: fix")
        result = run(
            GrepTool().execute(
                "1", {"pattern": "todo", "ignoreCase": True, "glob": "*.ts"}, self.ctx()
            )
        )
        self.assertEqual(result.content[0]["text"], "src/b.ts:1:todo later")
        result = run(GrepTool().execute("1", {"pattern": "TODO", "context": 1}, self.ctx()))
        self.assertEqual(result.content[0]["text"].splitlines()[0], "src/a.py-1-x = 1")
        result = run(GrepTool().execute("1", {"pattern": "(", "literal": True}, self.ctx()))
        self.assertEqual(result.content[0]["text"], "No matches found")
        with self.assertRaises(ToolError):
            run(GrepTool().execute("1", {"pattern": "("}, self.ctx()))
        with self.assertRaises(ToolError):
            run(GrepTool().execute("1", {"pattern": "x", "path": "nope"}, self.ctx()))
        single = run(
            GrepTool().execute("1", {"pattern": "y", "path": "src/a.py", "limit": 1}, self.ctx())
        )
        self.assertIn("Showing first 1", single.content[0]["text"])

    def test_find_and_glob(self):
        result = run(FindTool().execute("1", {"pattern": "*.py"}, self.ctx()))
        self.assertEqual(result.content[0]["text"], "src/a.py")
        result = run(FindTool().execute("1", {"pattern": "src/**/*.ts"}, self.ctx()))
        self.assertEqual(result.content[0]["text"], "src/b.ts")
        result = run(FindTool().execute("1", {"pattern": "*.none"}, self.ctx()))
        self.assertEqual(result.content[0]["text"], "No files found matching pattern")
        self.assertTrue(glob_matches("**/*.py", Path("a/b/c.py")))
        self.assertFalse(glob_matches("src/*.py", Path("src/x/c.py")))
        with self.assertRaises(ToolError):
            run(FindTool().execute("1", {"pattern": "*", "path": "nope"}, self.ctx()))

    def test_ls(self):
        result = run(LsTool().execute("1", {}, self.ctx()))
        self.assertEqual(
            result.content[0]["text"].splitlines(), [".gitignore", "build/", "debug.log", "src/"]
        )
        result = run(LsTool().execute("1", {"limit": 1}, self.ctx()))
        self.assertIn("Showing 1 of 4", result.content[0]["text"])
        (self.cwd / "empty").mkdir()
        self.assertEqual(
            run(LsTool().execute("1", {"path": "empty"}, self.ctx())).content[0]["text"],
            "(empty directory)",
        )
        with self.assertRaises(ToolError):
            run(LsTool().execute("1", {"path": "src/a.py"}, self.ctx()))


class RegistryTest(unittest.TestCase):
    def test_builtin_tools_and_enabled(self):
        tools = builtin_tools({})
        self.assertEqual(set(tools), {"read", "bash", "edit", "write", "grep", "find", "ls"})
        self.assertEqual(definition(tools["read"])["name"], "read")
        self.assertEqual(enabled_tool_names({}), ["read", "bash", "edit", "write"])
        self.assertEqual(
            enabled_tool_names({"defaultTools": ["+grep"]}),
            ["read", "bash", "edit", "write", "grep"],
        )

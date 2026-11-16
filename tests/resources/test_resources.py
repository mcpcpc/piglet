import tempfile
import unittest
from pathlib import Path

from piglet.resources.context_files import load_context_files, load_override
from piglet.resources.frontmatter import split_frontmatter
from piglet.resources.skills import (
    discover_skills,
    expand_skill_command,
    format_skills_for_prompt,
    load_skill,
    skill_roots,
)
from piglet.resources.system_prompt import PromptTool, SystemPromptOptions, build_system_prompt
from piglet.resources.templates import (
    expand_template,
    load_templates,
    parse_command_args,
    substitute_args,
)


class TemplateTest(unittest.TestCase):
    def test_parse_command_args(self):
        self.assertEqual(parse_command_args("a \"b c\" 'd e' f"), ["a", "b c", "d e", "f"])
        self.assertEqual(parse_command_args('say "it\\"s"'), ["say", 'it"s'])
        self.assertEqual(parse_command_args("  "), [])
        self.assertEqual(parse_command_args('x "unterminated'), ["x", '"unterminated'])

    def test_substitute_args(self):
        args = ["one", "two", "three"]
        self.assertEqual(substitute_args("$1-$2-$9", args), "one-two-")
        self.assertEqual(substitute_args("$@ | $ARGUMENTS", args), "one two three | one two three")
        self.assertEqual(
            substitute_args("${4:-fallback} ${1:-x} ${@:-none}", args), "fallback one one two three"
        )
        self.assertEqual(
            substitute_args("${@:2} / ${@:1:2} / ${@:0}", args),
            "two three / one two / one two three",
        )
        self.assertEqual(substitute_args("${@:-none}", []), "none")

    def test_load_and_expand(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "a", Path(tmp) / "b"
            first.mkdir()
            second.mkdir()
            (first / "review.md").write_text(
                "---\ndescription: Review code\nargument-hint: <file>\n---\nReview $1"
            )
            (second / "review.md").write_text("shadowed")
            (second / "plain.md").write_text("\nFirst line describes\nbody $@")
            templates = load_templates([first, second, Path(tmp) / "missing"])
            self.assertEqual([t.name for t in templates], ["plain", "review"])
            review = next(t for t in templates if t.name == "review")
            self.assertEqual((review.description, review.argument_hint), ("Review code", "<file>"))
            self.assertEqual(templates[0].description, "First line describes")
            self.assertEqual(expand_template("/review main.py", templates), "Review main.py")
            self.assertEqual(expand_template("/unknown x", templates), "/unknown x")
            self.assertEqual(expand_template("not a command", templates), "not a command")


class SkillTest(unittest.TestCase):
    def test_discovery_validation_and_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / "skills" / "deploy"
            good.mkdir(parents=True)
            (good / "SKILL.md").write_text(
                "---\nname: deploy\ndescription: Ship <it> & check\n---\nSteps"
            )
            dup = root / "other" / "deploy"
            dup.mkdir(parents=True)
            (dup / "SKILL.md").write_text("---\nname: deploy\ndescription: dup\n---\n")
            bad = root / "skills" / "Bad Name"
            bad.mkdir()
            (bad / "SKILL.md").write_text("---\ndescription: x\n---\n")
            missing = root / "skills" / "nodesc"
            missing.mkdir()
            (missing / "SKILL.md").write_text("no frontmatter")
            hidden = root / "skills" / "hidden"
            hidden.mkdir()
            (hidden / "SKILL.md").write_text(
                "---\ndescription: h\ndisable-model-invocation: true\n---\n"
            )
            skills, warnings = discover_skills([root / "skills", root / "other"])
            self.assertEqual(sorted(s.name for s in skills), ["Bad Name", "deploy", "hidden"])
            self.assertTrue(any("already defined" in w for w in warnings))
            self.assertTrue(any("invalid skill name" in w for w in warnings))
            self.assertTrue(any("description is required" in w for w in warnings))
            prompt = format_skills_for_prompt(skills)
            self.assertIn("<description>Ship &lt;it&gt; &amp; check</description>", prompt)
            self.assertNotIn("<name>hidden</name>", prompt)
            self.assertIn("Use bash to load", format_skills_for_prompt(skills, "bash"))
            self.assertEqual(format_skills_for_prompt([]), "")
            expanded = expand_skill_command("/skill:deploy now please", skills)
            self.assertIn('<skill name="deploy"', expanded)
            self.assertTrue(expanded.endswith("now please"))
            self.assertEqual(expand_skill_command("/skill:nope", skills), "/skill:nope")
            self.assertEqual(load_skill(root / "absent" / "SKILL.md")[0], None)

    def test_skill_roots_stop_at_git_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            (repo / ".git").mkdir(parents=True)
            (repo / ".agents" / "skills").mkdir(parents=True)
            (root / ".agents" / "skills").mkdir(parents=True)
            cwd = repo / "pkg"
            cwd.mkdir()
            roots = skill_roots(root / "agent", cwd, None, root / "home")
            self.assertIn(repo / ".agents" / "skills", roots)
            self.assertNotIn(root / ".agents" / "skills", roots)


class ContextTest(unittest.TestCase):
    def test_context_files_and_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = root / "agent"
            agent.mkdir()
            (agent / "AGENTS.md").write_text("global")
            project = root / "p"
            (project / "sub").mkdir(parents=True)
            (project / "CLAUDE.md").write_text("claude")
            (project / "AGENTS.override.md").write_text("override wins")
            (project / "sub" / "AGENTS.md").write_text("nested")
            files = load_context_files(agent, project / "sub")
            self.assertEqual(next(f.content for f in files), "global")
            self.assertEqual([f.content for f in files][-2:], ["override wins", "nested"])
            (agent / "SYSTEM.md").write_text("agent system")
            self.assertEqual(load_override("SYSTEM.md", agent, None), "agent system")
            (project / ".piglet").mkdir()
            (project / ".piglet" / "SYSTEM.md").write_text("project system")
            self.assertEqual(
                load_override("SYSTEM.md", agent, project / ".piglet"), "project system"
            )
            self.assertIsNone(load_override("APPEND_SYSTEM.md", agent, None))

    def test_frontmatter(self):
        self.assertEqual(split_frontmatter("---\na: 1\n---\nbody"), ({"a": 1}, "body"))
        self.assertEqual(split_frontmatter("---\n: bad: [\n---\nbody")[1], "body")
        self.assertEqual(split_frontmatter("---\nno end")[0], {})
        self.assertEqual(split_frontmatter("\ufeffplain"), ({}, "plain"))


class SystemPromptTest(unittest.TestCase):
    def test_sections(self):
        prompt = build_system_prompt(
            SystemPromptOptions(
                cwd="C:\\work",
                tools=[PromptTool("bash", "Execute bash commands", ())],
                append="extra rules",
            )
        )
        self.assertTrue(prompt.startswith("You are a coding assistant running inside piglet"))
        self.assertIn("- Use bash for file operations like ls, rg, find", prompt)
        self.assertIn("<addendum>\nextra rules\n</addendum>", prompt)
        self.assertTrue(prompt.endswith("<cwd>\nC:/work\n</cwd>"))
        custom = build_system_prompt(SystemPromptOptions(cwd="/p", custom_prompt="Custom!"))
        self.assertTrue(custom.startswith("Custom!"))
        self.assertNotIn("<tools>", custom)

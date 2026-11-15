import unittest

from piglet.config.values import EnvRef, Literal, ValueResolver, env_var_names, parse_template


class ParseTemplateTest(unittest.TestCase):
    def test_literal_and_env_parts(self):
        self.assertEqual(
            parse_template("Bearer ${TOKEN}-$X!"),
            [Literal("Bearer "), EnvRef("TOKEN"), Literal("-"), EnvRef("X"), Literal("!")],
        )

    def test_escapes(self):
        self.assertEqual(parse_template("a$$b$!c"), [Literal("a$b!c")])

    def test_invalid_braces_and_trailing_dollar_stay_literal(self):
        self.assertEqual(parse_template("${1x}$"), [Literal("${1x}$")])
        self.assertEqual(parse_template("${open"), [Literal("${open")])

    def test_env_var_names(self):
        self.assertEqual(env_var_names("$A-${B}-$A"), ["A", "B"])
        self.assertEqual(env_var_names("!echo $A"), [])


class ValueResolverTest(unittest.TestCase):
    def test_resolves_env_template(self):
        resolver = ValueResolver(env={"KEY": "secret"})
        self.assertEqual(resolver.resolve("sk-$KEY"), "sk-secret")

    def test_missing_env_returns_none(self):
        self.assertIsNone(ValueResolver(env={}).resolve("$MISSING"))

    def test_commands_are_cached_when_enabled(self):
        calls = []

        def runner(command):
            calls.append(command)
            return "out"

        cached = ValueResolver(env={}, runner=runner, cache_commands=True)
        self.assertEqual(cached.resolve("!get key"), "out")
        self.assertEqual(cached.resolve("!get key"), "out")
        self.assertEqual(calls, ["get key"])

        uncached = ValueResolver(env={}, runner=runner, cache_commands=False)
        uncached.resolve("!get key")
        uncached.resolve("!get key")
        self.assertEqual(len(calls), 3)

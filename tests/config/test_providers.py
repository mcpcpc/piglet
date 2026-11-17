import unittest
from types import SimpleNamespace

from piglet.ai.registry import apply_models_config, load_catalog
from piglet.config.providers import PROVIDERS, provider_auth_list

NOW = 1_800_000_000_000
CHATGPT = SimpleNamespace(
    account="ChatGPT",
    subscription=True,
    saved_accounts=lambda: [{"id": "c1", "label": "user@example.invalid"}],
)


def rows(models=None, stored=None, env=None, logins=None):
    resolved = apply_models_config(load_catalog(), models)
    listed = provider_auth_list(resolved, stored or {}, env or {}, logins or {}, NOW)
    return {row["id"]: row for row in listed}, [row["id"] for row in listed]


class ListRuleTest(unittest.TestCase):
    def test_default_list(self):
        by_id, order = rows()
        self.assertEqual(len(order), 33)
        for absent in (
            "mistral",
            "google-vertex",
            "azure",
            "github-copilot",
            "openai-codex",
            "cloudflare-ai-gateway",
        ):
            self.assertNotIn(absent, by_id)
        self.assertEqual(order[0], "openrouter")
        rest = [by_id[p]["name"].casefold() for p in order[1:]]
        self.assertEqual(rest, sorted(rest))
        self.assertTrue(all(row["sources"] == [] for row in by_id.values()))

    def test_every_listed_catalog_provider_has_a_name(self):
        _, order = rows()
        self.assertEqual([p for p in order if p not in PROVIDERS], [])

    def test_no_sign_in_providers_appear_with_a_credential(self):
        by_id, _ = rows(
            stored={"github-copilot": {"type": "oauth", "access": "a", "expires": 1}},
            env={"COPILOT_GITHUB_TOKEN": ""},
        )
        copilot = by_id["github-copilot"]
        self.assertEqual(copilot["name"], "GitHub Copilot")
        self.assertIsNone(copilot["keyLabel"])
        self.assertEqual(copilot["sources"][0]["problem"], "expired")
        by_id, _ = rows(env={"COPILOT_GITHUB_TOKEN": "t"})
        self.assertEqual(by_id["github-copilot"]["sources"][0]["envVar"], "COPILOT_GITHUB_TOKEN")

    def test_custom_models_json_provider_uses_its_name(self):
        models = {
            "providers": {
                "lab": {
                    "name": "Lab Gateway",
                    "baseUrl": "http://lab.invalid/v1",
                    "api": "openai-completions",
                    "models": [{"id": "m"}],
                }
            }
        }
        by_id, _ = rows(models)
        self.assertEqual(by_id["lab"]["name"], "Lab Gateway")
        self.assertEqual(by_id["lab"]["keyLabel"], "Lab Gateway API key")
        self.assertEqual(by_id["lab"]["sources"], [])

    def test_login_metadata(self):
        by_id, _ = rows(logins={"openai": CHATGPT})
        self.assertEqual(
            by_id["openai"]["login"],
            {
                "account": "ChatGPT",
                "subscription": True,
                "accounts": [{"id": "c1", "label": "user@example.invalid"}],
            },
        )
        self.assertIsNone(by_id["groq"]["login"])
        self.assertEqual(by_id["anthropic"]["keywords"], ["Claude"])


class SourcesTest(unittest.TestCase):
    def test_precedence_order_and_matched_env_var(self):
        by_id, _ = rows(
            models={"providers": {"groq": {"apiKey": "x"}}},
            stored={"groq": {"type": "api_key", "key": "k"}},
            env={"GROQ_API_KEY": "e"},
        )
        kinds = [s["kind"] for s in by_id["groq"]["sources"]]
        self.assertEqual(kinds, ["stored", "models.json", "env"])
        by_id, _ = rows(env={"ANTHROPIC_API_KEY": "e"})
        self.assertEqual(
            by_id["anthropic"]["sources"],
            [{"kind": "env", "envVar": "ANTHROPIC_API_KEY", "problem": None}],
        )
        self.assertEqual(len(by_id["anthropic"]["envVars"]), 3)

    def test_problems(self):
        stored = {
            "groq": {"type": "api_key", "key": "$UNSET_VAR"},
            "cerebras": {"type": "api_key", "key": "   "},
            "deepseek": {"type": "api_key"},
            "fireworks": {"type": "api_key", "key": "!never-run-this"},
            "openai": {"type": "oauth", "access": "a", "expires": 1},
            "github-copilot": {"type": "oauth", "access": "a", "expires": NOW + 1},
            "together": {"type": "mystery"},
        }
        by_id, _ = rows(
            models={"providers": {"zai": {"apiKey": "${ZAI_SECRET}"}}},
            stored=stored,
            logins={"openai": CHATGPT},
        )
        problem = {p: by_id[p]["sources"][0]["problem"] for p in (*stored, "zai")}
        self.assertEqual(
            problem,
            {
                "groq": "unresolved",
                "cerebras": "blank",
                "deepseek": "invalid",
                "fireworks": None,
                "openai": None,
                "github-copilot": None,
                "together": "invalid",
                "zai": "unresolved",
            },
        )
        self.assertEqual(by_id["groq"]["sources"][0]["missingVar"], "UNSET_VAR")
        self.assertEqual(by_id["zai"]["sources"][0]["missingVar"], "ZAI_SECRET")
        self.assertEqual(by_id["openai"]["sources"][0]["type"], "oauth")

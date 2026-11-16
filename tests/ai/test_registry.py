import unittest
from typing import ClassVar

from piglet.ai import registry as r
from piglet.ai.types import calculate_cost, empty_usage


def model(provider, model_id, **extra):
    return {
        "provider": provider,
        "id": model_id,
        "name": model_id,
        "api": "openai-completions",
        **extra,
    }


class CatalogTest(unittest.TestCase):
    def test_catalog_loads(self):
        catalog = r.load_catalog()
        self.assertGreater(len(catalog), 100)
        self.assertTrue(any(m["provider"] == "anthropic" for m in catalog))


class ModelsConfigTest(unittest.TestCase):
    def test_provider_override_and_new_provider(self):
        catalog = [model("openai", "gpt", baseUrl="https://api.openai.com/v1")]
        config = {
            "providers": {
                "openai": {
                    "baseUrl": "https://proxy/v1",
                    "modelOverrides": {"gpt": {"maxTokens": 7}},
                },
                "ollama": {
                    "baseUrl": "http://localhost:11434/v1",
                    "api": "openai-completions",
                    "apiKey": "ollama",
                    "models": [{"id": "qwen"}],
                },
                "broken": {"models": [{"id": "x"}]},
            }
        }
        resolved = r.apply_models_config(catalog, config)
        gpt = next(m for m in resolved.models if m["id"] == "gpt")
        self.assertEqual(gpt["baseUrl"], "https://proxy/v1")
        self.assertEqual(gpt["maxTokens"], 7)
        qwen = next(m for m in resolved.models if m["id"] == "qwen")
        self.assertEqual(qwen["contextWindow"], 128000)
        self.assertEqual(resolved.providers["ollama"].api_key, "ollama")
        self.assertEqual(len(resolved.errors), 1)
        self.assertEqual(catalog[0]["baseUrl"], "https://api.openai.com/v1")

    def test_models_entry_replaces_existing(self):
        catalog = [model("openai", "gpt", reasoning=True)]
        resolved = r.apply_models_config(
            catalog, {"providers": {"openai": {"models": [{"id": "gpt"}]}}}
        )
        self.assertEqual(len(resolved.models), 1)
        self.assertFalse(resolved.models[0]["reasoning"])

    def test_absent_config(self):
        self.assertEqual(len(r.apply_models_config([model("a", "b")], None).models), 1)


class ThinkingTest(unittest.TestCase):
    def test_non_reasoning_supports_off_only(self):
        self.assertEqual(r.supported_thinking_levels(model("a", "b")), ["off"])

    def test_map_controls_xhigh_and_null(self):
        m = model("a", "b", reasoning=True, thinkingLevelMap={"off": None, "xhigh": "xhigh"})
        self.assertEqual(
            r.supported_thinking_levels(m), ["minimal", "low", "medium", "high", "xhigh"]
        )
        self.assertEqual(r.clamp_thinking_level(m, "off"), "minimal")
        self.assertEqual(r.clamp_thinking_level(m, "max"), "xhigh")
        self.assertEqual(r.clamp_thinking_level(m, "bogus"), "minimal")


class ResolveTest(unittest.TestCase):
    models: ClassVar[list] = [
        model("anthropic", "claude-sonnet-4-5"),
        model("anthropic", "claude-sonnet-4-5-20250929"),
        model("openrouter", "claude-sonnet-4-5"),
        model("openai", "gpt-5"),
    ]

    def test_exact_provider_id(self):
        found = r.resolve_reference("openrouter/claude-sonnet-4-5", self.models, lambda p: True)
        self.assertEqual(found["provider"], "openrouter")

    def test_exact_id_prefers_usable_provider(self):
        found = r.resolve_reference("claude-sonnet-4-5", self.models, lambda p: p == "openrouter")
        self.assertEqual(found["provider"], "openrouter")

    def test_fuzzy_prefers_alias(self):
        found = r.resolve_reference("sonnet", self.models, lambda p: p == "anthropic")
        self.assertEqual(found["id"], "claude-sonnet-4-5")
        self.assertIsNone(r.resolve_reference("sonnet", self.models, lambda p: True, fuzzy=False))
        self.assertIsNone(r.resolve_reference("", self.models, lambda p: True))

    def test_scope_with_glob_and_thinking(self):
        scoped = r.resolve_scope(
            ["anthropic/*:high", "gpt-5", "gpt-5"], self.models, lambda p: True
        )
        self.assertEqual(
            [r.model_key(s.model) for s in scoped][:2],
            ["anthropic/claude-sonnet-4-5", "anthropic/claude-sonnet-4-5-20250929"],
        )
        self.assertEqual(scoped[0].thinking_level, "high")
        self.assertEqual(len(scoped), 3)

    def test_find_initial_model(self):
        models = [model("openai", "gpt-6.1-sol"), model("openai", "other"), model("groq", "x")]
        usable = lambda p: p == "openai"
        self.assertEqual(r.find_initial_model(models, usable, {}, [])["id"], "gpt-6.1-sol")
        chosen = r.find_initial_model(
            models, usable, {"defaultProvider": "openai", "defaultModel": "other"}, []
        )
        self.assertEqual(chosen["id"], "other")
        scoped = [r.ScopedModel(models[2])]
        self.assertEqual(r.find_initial_model(models, usable, {}, scoped)["id"], "x")
        self.assertIsNone(r.find_initial_model(models, lambda p: False, {}, []))


class CostTest(unittest.TestCase):
    def test_calculate_cost_with_tiers(self):
        m = {
            "cost": {
                "input": 1,
                "output": 2,
                "cacheRead": 0.1,
                "cacheWrite": 1.25,
                "tiers": [
                    {
                        "inputTokensAbove": 100,
                        "input": 10,
                        "output": 20,
                        "cacheRead": 1,
                        "cacheWrite": 12.5,
                    }
                ],
            }
        }
        usage = empty_usage()
        usage.update(input=1_000_000, output=1_000_000)
        cost = calculate_cost(m, usage)
        self.assertAlmostEqual(cost["total"], 30.0)

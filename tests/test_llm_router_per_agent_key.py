"""Tests for per-agent OpenAI-compatible endpoint + api_key_ref resolution.

Covers the gateway/proxy routing path (e.g. a LiteLLM "atlas-router"): an
agent declares `provider: atlas`, a per-agent `endpoint`, and an `api_key_ref`
that resolves through the SecretsManager (encrypted store / GCP SM / env) at
invoke time — no global env key required.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock

import pytest

from stacks.base import LLMConfig
from src.platform.llm_router import LLMResponse, LLMRouter


class _FakeSecrets:
    """Minimal SecretsManager stand-in: name -> value."""

    def __init__(self, values: dict[str, str]):
        self._values = values
        self.calls: list[tuple[str, str, str]] = []

    def get(self, name, default="", *, caller="", reason=""):
        self.calls.append((name, caller, reason))
        return self._values.get(name, default)


@pytest.fixture
def fake_openai(monkeypatch):
    """Replace `openai.OpenAI` so `from openai import OpenAI` yields a fake.

    Each construction records its kwargs; lets tests assert the resolved
    base_url + api_key without any network or the real SDK installed.
    """
    instances: list[object] = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.base_url = kwargs.get("base_url")
            instances.append(self)

    mod = types.ModuleType("openai")
    mod.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", mod)
    return instances


# ---------------------------------------------------------------------------
# _resolve_api_key
# ---------------------------------------------------------------------------

class TestResolveApiKey:
    def test_secret_ref_via_secrets_manager(self):
        router = LLMRouter(api_keys={})
        router.bind_secrets(_FakeSecrets({"litellm-allycode-key": "sk-resolved"}))
        assert router._resolve_api_key("secret:litellm-allycode-key") == "sk-resolved"

    def test_secret_ref_records_audit_metadata(self):
        secrets = _FakeSecrets({"k": "v"})
        router = LLMRouter(api_keys={})
        router.bind_secrets(secrets)
        router._resolve_api_key("secret:k")
        assert secrets.calls == [("k", "llm_router", "llm.api_key_ref")]

    def test_secret_ref_without_manager_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("LITELLM_ALLYCODE_KEY", "sk-from-env")
        router = LLMRouter(api_keys={})  # no secrets bound
        assert router._resolve_api_key("secret:litellm-allycode-key") == "sk-from-env"

    def test_env_ref(self, monkeypatch):
        monkeypatch.setenv("MY_GW_KEY", "sk-env")
        router = LLMRouter(api_keys={})
        assert router._resolve_api_key("env:MY_GW_KEY") == "sk-env"

    def test_literal_passthrough(self):
        router = LLMRouter(api_keys={})
        assert router._resolve_api_key("sk-literal") == "sk-literal"

    def test_none_and_empty(self):
        router = LLMRouter(api_keys={})
        assert router._resolve_api_key(None) is None
        assert router._resolve_api_key("") is None

    def test_missing_secret_returns_none(self):
        router = LLMRouter(api_keys={})
        router.bind_secrets(_FakeSecrets({}))
        assert router._resolve_api_key("secret:absent") is None


# ---------------------------------------------------------------------------
# _get_openai_compatible_client
# ---------------------------------------------------------------------------

class TestPerAgentClientCache:
    def test_builds_client_with_endpoint_and_key(self, fake_openai):
        router = LLMRouter(api_keys={})
        client = router._get_openai_compatible_client(
            "atlas", "https://atlas-router.example.com/v1", "sk-1",
        )
        assert client is not None
        assert client.kwargs["base_url"] == "https://atlas-router.example.com/v1"
        assert client.kwargs["api_key"] == "sk-1"

    def test_same_pair_is_cached(self, fake_openai):
        router = LLMRouter(api_keys={})
        c1 = router._get_openai_compatible_client("atlas", "https://x/v1", "sk-1")
        c2 = router._get_openai_compatible_client("atlas", "https://x/v1", "sk-1")
        assert c1 is c2
        assert len(fake_openai) == 1

    def test_different_key_builds_new_client(self, fake_openai):
        router = LLMRouter(api_keys={})
        c1 = router._get_openai_compatible_client("atlas", "https://x/v1", "sk-1")
        c2 = router._get_openai_compatible_client("atlas", "https://x/v1", "sk-2")
        assert c1 is not c2
        assert len(fake_openai) == 2

    def test_no_override_returns_boot_client(self, fake_openai):
        router = LLMRouter(api_keys={})
        router._clients["atlas"] = object()  # pretend a boot-time client exists
        got = router._get_openai_compatible_client("atlas", None, None)
        assert got is router._clients["atlas"]
        assert len(fake_openai) == 0  # nothing new built


# ---------------------------------------------------------------------------
# chat() end-to-end threading
# ---------------------------------------------------------------------------

class TestChatThreadsPerAgentEndpoint:
    @pytest.mark.asyncio
    async def test_atlas_first_class_fields(self, fake_openai):
        router = LLMRouter(api_keys={})
        router.bind_secrets(_FakeSecrets({"litellm-allycode-key": "sk-resolved"}))
        router._call_openai = AsyncMock(
            return_value=LLMResponse(text="hi", model="gemini-3.1-pro", provider="atlas")
        )
        cfg = LLMConfig(
            chat_model="gemini-3.1-pro",
            provider="atlas",
            endpoint="https://atlas-router.example.com/v1",
            api_key_ref="secret:litellm-allycode-key",
        )

        resp = await router.chat(cfg, [{"role": "user", "content": "hello"}])

        assert resp.text == "hi"
        # The per-agent client handed to _call_openai carries the resolved key.
        used_client = router._call_openai.call_args.args[0]
        assert used_client.kwargs["base_url"] == "https://atlas-router.example.com/v1"
        assert used_client.kwargs["api_key"] == "sk-resolved"

    @pytest.mark.asyncio
    async def test_metadata_fallback_still_works(self, fake_openai):
        """base_url/api_key_ref in llm_config.metadata remain honored (back-compat)."""
        router = LLMRouter(api_keys={})
        router.bind_secrets(_FakeSecrets({"k": "sk-meta"}))
        router._call_openai = AsyncMock(
            return_value=LLMResponse(text="ok", model="qwen", provider="atlas")
        )
        cfg = LLMConfig(
            chat_model="qwen",
            provider="atlas",
            metadata={"base_url": "https://gw/v1", "api_key_ref": "secret:k"},
        )

        await router.chat(cfg, [{"role": "user", "content": "hi"}])

        used_client = router._call_openai.call_args.args[0]
        assert used_client.kwargs["base_url"] == "https://gw/v1"
        assert used_client.kwargs["api_key"] == "sk-meta"

    @pytest.mark.asyncio
    async def test_atlas_without_override_or_boot_key_is_simulated(self):
        router = LLMRouter(api_keys={})  # no atlas boot client, no override
        cfg = LLMConfig(chat_model="gemini-3-flash", provider="atlas")
        resp = await router.chat(cfg, [{"role": "user", "content": "hi"}])
        assert "[Simulated atlas/gemini-3-flash]" in resp.text

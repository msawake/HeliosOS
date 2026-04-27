"""
Multi-LLM Router.

Routes agent LLM calls to the correct provider and model, supporting
separate chat vs reasoning models per agent. Abstracts away provider
differences so stacks just call `router.chat()` or `router.reason()`.

Production hardening:
- Each provider call is wrapped with a 3-attempt exponential backoff
  retry on transient errors (rate limits, 5xx, timeouts).
- If the primary provider fails after retries AND the agent's
  LLMConfig.metadata["fallback_provider"] is set, a single failover
  attempt is made to the fallback provider. Failovers emit an audit
  event when an AuditLog instance is bound.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from dataclasses import dataclass
from typing import Any

from stacks.base import LLMConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------

MAX_RETRIES = int(os.environ.get("FORGEOS_LLM_MAX_RETRIES", "3"))
BACKOFF_BASE_SECONDS = float(os.environ.get("FORGEOS_LLM_BACKOFF_BASE", "1.0"))
BACKOFF_MAX_SECONDS = float(os.environ.get("FORGEOS_LLM_BACKOFF_MAX", "30.0"))


def _is_retryable(exc: BaseException) -> bool:
    """Classify an exception as transient/retryable.

    Check status code first (if present) — a `APIStatusError(400)` is fatal
    even though its name matches a retryable class.
    """
    name = type(exc).__name__

    # Check status code first — overrides name-based classification
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int):
        if status >= 500 or status == 429 or status == 408:
            return True
        return False  # 4xx (except 408/429) is not retryable

    # Name-based classification for SDK errors without status codes
    retryable_names = {
        "RateLimitError", "APIConnectionError", "APITimeoutError",
        "InternalServerError", "ServiceUnavailableError",
        "TimeoutError", "ReadTimeout", "ConnectTimeout", "ConnectionError",
    }
    if name in retryable_names:
        return True

    # httpx / generic timeouts in the message
    msg = str(exc).lower()
    if "timeout" in msg or "timed out" in msg or "rate limit" in msg:
        return True
    return False


async def _with_retry(call_fn, *, provider: str, model: str, attempts: int = MAX_RETRIES):
    """Call `call_fn` up to `attempts` times with exponential backoff on transient errors."""
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await call_fn()
        except Exception as e:
            last_exc = e
            if not _is_retryable(e) or attempt == attempts:
                raise
            delay = min(
                BACKOFF_MAX_SECONDS,
                BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
            ) + random.uniform(0, 0.5)
            logger.warning(
                "LLM call transient error on %s/%s (attempt %d/%d): %s. Retrying in %.1fs",
                provider, model, attempt, attempts, e, delay,
            )
            await asyncio.sleep(delay)
    if last_exc:
        raise last_exc  # pragma: no cover


def _to_openai_tools(tools: list[dict]) -> list[dict]:
    """Convert Anthropic-format tool definitions to OpenAI format.

    Anthropic: {"name": "x", "description": "y", "input_schema": {...}}
    OpenAI:    {"type": "function", "function": {"name": "x", "description": "y", "parameters": {...}}}
    """
    converted = []
    for t in tools:
        if t.get("type") == "function":
            converted.append(t)  # Already OpenAI format
        else:
            name = t.get("name", "")
            if not name:
                logger.warning("Skipping tool with missing name: %s", t)
                continue
            schema = t.get("input_schema")
            if schema is None:
                logger.warning("Tool '%s' missing input_schema — using empty object schema", name)
                schema = {"type": "object", "properties": {}}
            converted.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": t.get("description", ""),
                    "parameters": schema,
                },
            })
    return converted


@dataclass
class ToolCall:
    """A tool call requested by the LLM."""
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    tokens_used: int = 0
    finish_reason: str = "stop"
    tool_calls: list[ToolCall] | None = None
    raw: dict[str, Any] | None = None
    error: str | None = None  # set when both primary + fallback providers fail

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class LLMRouter:
    """
    Routes LLM calls based on agent LLMConfig.

    Currently returns simulated responses. When real SDKs are plugged in,
    each provider branch calls the actual API client.
    """

    def __init__(self, api_keys: dict[str, str] | None = None, audit_log=None):
        self._api_keys = api_keys or {}
        self._clients: dict[str, Any] = {}
        self._audit = audit_log  # optional AuditLog for failover events
        self._init_clients()

    def bind_audit(self, audit_log) -> None:
        """Attach an AuditLog instance after construction (bootstrap convenience)."""
        self._audit = audit_log

    def _init_clients(self) -> None:
        for provider, key in self._api_keys.items():
            if provider == "anthropic" and key:
                try:
                    from anthropic import Anthropic
                    self._clients["anthropic"] = Anthropic(api_key=key)
                    logger.info("Initialized Anthropic client")
                except ImportError:
                    logger.warning("anthropic package not installed")
            elif provider == "openai" and key:
                try:
                    from openai import OpenAI
                    self._clients["openai"] = OpenAI(api_key=key)
                    logger.info("Initialized OpenAI client")
                except ImportError:
                    logger.warning("openai package not installed")
            elif provider == "atlas" and key:
                try:
                    from openai import OpenAI
                    atlas_url = os.environ.get("ATLAS_GATEWAY_URL", "https://atlas-gateway-609114458603.europe-west1.run.app/v1")
                    self._clients["atlas"] = OpenAI(api_key=key, base_url=atlas_url, timeout=120.0)
                    logger.info("Initialized Atlas Gateway client (%s)", atlas_url)
                except ImportError:
                    logger.warning("openai package not installed (needed for Atlas Gateway)")
            elif provider == "vertex" and key:
                self._clients["vertex"] = {
                    "project_id": key,  # key=project_id for vertex
                    "region": os.environ.get("GCP_REGION", "us-central1"),
                }
                logger.info("Initialized Vertex AI client (project=%s, region=%s)",
                            key, self._clients["vertex"]["region"])
            elif provider == "google" and key:
                try:
                    from openai import OpenAI
                    self._clients["google"] = OpenAI(
                        api_key=key,
                        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                    )
                    logger.info("Initialized Google/Gemini client (OpenAI-compatible)")
                except ImportError:
                    logger.warning("openai package not installed (needed for Gemini)")


    async def chat(self, llm_config: LLMConfig, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        """Send a chat completion using the agent's chat model.

        Retries transient errors up to MAX_RETRIES with exponential backoff.
        If the primary provider fails after retries and the agent config
        specifies `metadata.fallback_provider`, a single failover attempt is
        made against the fallback provider.
        """
        fallback = (getattr(llm_config, "metadata", None) or {}).get("fallback_provider")
        return await self._call_with_failover(
            provider=llm_config.provider,
            model=llm_config.chat_model,
            messages=messages,
            tools=tools,
            fallback_provider=fallback,
        )

    async def reason(self, llm_config: LLMConfig, messages: list[dict]) -> LLMResponse:
        """Send a reasoning/thinking call using the agent's reasoning model."""
        model = llm_config.reasoning_model or llm_config.chat_model
        fallback = (getattr(llm_config, "metadata", None) or {}).get("fallback_provider")
        return await self._call_with_failover(
            provider=llm_config.provider,
            model=model,
            messages=messages,
            fallback_provider=fallback,
        )

    async def chat_stream(
        self,
        llm_config: LLMConfig,
        messages: list[dict],
        tools: list[dict] | None = None,
    ):
        """Stream a chat completion as an async iterator of typed events.

        Event shapes:
            {"type": "text_delta", "content": str}
            {"type": "tool_use", "id": str, "name": str, "input": dict}
            {"type": "done", "tokens_used": int, "text": str, "tool_calls": list}
            {"type": "error", "error": str}

        Falls back to a single "text_delta" + "done" pair in simulated mode.
        Retries and failover are NOT applied to streaming calls — the caller
        should handle reconnect/retry at the event-stream layer.
        """
        provider = llm_config.provider
        model = llm_config.chat_model
        client = self._clients.get(provider)

        if provider == "anthropic" and client:
            async for ev in self._stream_anthropic(client, model, messages, tools):
                yield ev
            return
        if provider == "openai" and client:
            async for ev in self._stream_openai(client, model, messages, tools):
                yield ev
            return
        if provider == "google" and client:
            async for ev in self._stream_openai(client, model, messages, tools):
                yield ev
            return

        # Simulated fallback
        yield {
            "type": "text_delta",
            "content": f"[Simulated {provider}/{model}] Processed {len(messages)} message(s).",
        }
        yield {"type": "done", "tokens_used": 0, "text": "", "tool_calls": []}

    async def _stream_anthropic(
        self, client: Any, model: str, messages: list[dict], tools: list[dict] | None,
    ):
        """Stream from Anthropic using `client.messages.stream`.

        Yields text_delta / tool_use / done / error events.
        """
        try:
            system_parts = []
            non_system = []
            for m in messages:
                if m.get("role") == "system":
                    system_parts.append(m.get("content", ""))
                else:
                    non_system.append(m)

            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": 16384,
                "messages": non_system,
            }
            if system_parts:
                kwargs["system"] = "\n\n".join(system_parts)
            if tools:
                kwargs["tools"] = tools

            # The Anthropic SDK's `stream()` returns a context manager. Run it
            # in a worker thread so we don't block the asyncio loop.
            import concurrent.futures
            q: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_event_loop()

            def _run():
                try:
                    with client.messages.stream(**kwargs) as stream:
                        text_acc = ""
                        for text_delta in stream.text_stream:
                            text_acc += text_delta
                            asyncio.run_coroutine_threadsafe(
                                q.put({"type": "text_delta", "content": text_delta}),
                                loop,
                            )
                        final = stream.get_final_message()
                        tokens = 0
                        if final and getattr(final, "usage", None):
                            tokens = final.usage.input_tokens + final.usage.output_tokens
                        tool_calls = []
                        for block in getattr(final, "content", []) or []:
                            if getattr(block, "type", "") == "tool_use":
                                tool_calls.append({
                                    "id": block.id,
                                    "name": block.name,
                                    "input": block.input,
                                })
                        asyncio.run_coroutine_threadsafe(
                            q.put({
                                "type": "done",
                                "tokens_used": tokens,
                                "text": "",  # Already streamed via text_delta events
                                "tool_calls": tool_calls,
                            }),
                            loop,
                        )
                except Exception as e:
                    logger.exception("Anthropic stream error")
                    asyncio.run_coroutine_threadsafe(
                        q.put({"type": "error", "error": str(e)}),
                        loop,
                    )
                finally:
                    asyncio.run_coroutine_threadsafe(
                        q.put({"type": "stream_end"}), loop,
                    )

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(_run)
                while True:
                    ev = await q.get()
                    if ev is None or ev.get("type") == "stream_end":
                        return
                    yield ev
                    if ev.get("type") in ("done", "error"):
                        return

        except Exception as e:
            logger.exception("Anthropic stream setup failed")
            yield {"type": "error", "error": str(e)}

    async def _stream_openai(
        self, client: Any, model: str, messages: list[dict], tools: list[dict] | None,
    ):
        """Stream from OpenAI using `stream=True`."""
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "stream": True,
            }
            if tools:
                kwargs["tools"] = _to_openai_tools(tools)

            import concurrent.futures
            q: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_event_loop()

            def _run():
                try:
                    stream = client.chat.completions.create(**kwargs)
                    text_acc = ""
                    tc_accum: dict[int, dict] = {}
                    for chunk in stream:
                        try:
                            choice = chunk.choices[0]
                        except (IndexError, AttributeError):
                            continue
                        delta = getattr(choice, "delta", None)
                        if delta is None:
                            continue
                        content = getattr(delta, "content", None)
                        if content:
                            text_acc += content
                            asyncio.run_coroutine_threadsafe(
                                q.put({"type": "text_delta", "content": content}),
                                loop,
                            )
                        for tc_delta in getattr(delta, "tool_calls", None) or []:
                            idx = getattr(tc_delta, "index", 0)
                            if idx not in tc_accum:
                                tc_accum[idx] = {"id": "", "name": "", "arguments": ""}
                            if getattr(tc_delta, "id", None):
                                tc_accum[idx]["id"] = tc_delta.id
                            fn = getattr(tc_delta, "function", None)
                            if fn:
                                if getattr(fn, "name", None):
                                    tc_accum[idx]["name"] = fn.name
                                if getattr(fn, "arguments", None):
                                    tc_accum[idx]["arguments"] += fn.arguments
                    tool_calls = []
                    for idx in sorted(tc_accum):
                        tc = tc_accum[idx]
                        try:
                            inp = json.loads(tc["arguments"]) if tc["arguments"] else {}
                        except json.JSONDecodeError:
                            inp = {}
                        tool_calls.append({"id": tc["id"], "name": tc["name"], "input": inp})
                    asyncio.run_coroutine_threadsafe(
                        q.put({
                            "type": "done",
                            "tokens_used": 0,
                            "text": "",
                            "tool_calls": tool_calls,
                        }),
                        loop,
                    )
                except Exception as e:
                    logger.exception("OpenAI stream error")
                    asyncio.run_coroutine_threadsafe(
                        q.put({"type": "error", "error": str(e)}),
                        loop,
                    )
                finally:
                    asyncio.run_coroutine_threadsafe(
                        q.put({"type": "stream_end"}), loop,
                    )

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(_run)
                while True:
                    ev = await q.get()
                    if ev is None or ev.get("type") == "stream_end":
                        return
                    yield ev
                    if ev.get("type") in ("done", "error"):
                        return

        except Exception as e:
            logger.exception("OpenAI stream setup failed")
            yield {"type": "error", "error": str(e)}

    async def _call_with_failover(
        self,
        provider: str,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        fallback_provider: str | None = None,
    ) -> LLMResponse:
        """Call the primary provider with retries; optionally failover once."""
        try:
            return await self._call(
                provider=provider, model=model, messages=messages, tools=tools,
            )
        except Exception as primary_error:
            logger.error("Primary LLM call failed after retries: %s/%s: %s",
                         provider, model, primary_error)

            if not fallback_provider or fallback_provider == provider:
                # No failover available — return an error response
                return LLMResponse(
                    text=f"[Error] {primary_error}",
                    model=model,
                    provider=provider,
                    error=str(primary_error),
                )

            # Audit failover attempt
            if self._audit is not None:
                try:
                    self._audit.record(
                        "platform.llm_failover",
                        resource_type="llm",
                        resource_id=f"{provider}:{model}",
                        outcome="pending",
                        details={
                            "from_provider": provider,
                            "to_provider": fallback_provider,
                            "model": model,
                            "error": str(primary_error),
                        },
                    )
                except Exception:
                    pass

            logger.warning("Failing over %s -> %s", provider, fallback_provider)
            try:
                return await self._call(
                    provider=fallback_provider,
                    model=model,
                    messages=messages,
                    tools=tools,
                )
            except Exception as fallback_error:
                logger.error("Fallback LLM call also failed: %s: %s",
                             fallback_provider, fallback_error)
                return LLMResponse(
                    text=f"[Error] primary={primary_error} fallback={fallback_error}",
                    model=model,
                    provider=provider,
                    error=f"primary={primary_error} fallback={fallback_error}",
                )

    async def _call(
        self,
        provider: str,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """Direct provider call with retries. Raises on exhausted retries."""
        client = self._clients.get(provider)

        if provider == "anthropic" and client:
            return await _with_retry(
                lambda: self._call_anthropic(client, model, messages, tools),
                provider=provider, model=model,
            )
        if provider == "openai" and client:
            return await _with_retry(
                lambda: self._call_openai(client, model, messages, tools),
                provider=provider, model=model,
            )
        if provider == "google" and client:
            return await _with_retry(
                lambda: self._call_google(client, model, messages, tools),
                provider=provider, model=model,
            )
        if provider == "atlas" and client:
            return await _with_retry(
                lambda: self._call_openai(client, model, messages, tools),
                provider=provider, model=model,
            )
        if provider == "vertex" and client:
            return await _with_retry(
                lambda: self._call_vertex(client, model, messages, tools),
                provider=provider, model=model,
            )

        logger.debug(
            "Simulated LLM call: provider=%s model=%s messages=%d",
            provider, model, len(messages),
        )
        return LLMResponse(
            text=f"[Simulated {provider}/{model}] Processed {len(messages)} message(s).",
            model=model,
            provider=provider,
            tokens_used=0,
        )

    async def _call_anthropic(
        self, client: Any, model: str, messages: list[dict], tools: list[dict] | None
    ) -> LLMResponse:
        """Call Anthropic. Raises on any error so the retry wrapper can handle it."""
        # Extract system messages — Anthropic requires them as a top-level parameter
        system_parts = []
        non_system = []
        for m in messages:
            if m.get("role") == "system":
                system_parts.append(m.get("content", ""))
            else:
                non_system.append(m)

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 16384,
            "messages": non_system,
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)
        if tools:
            kwargs["tools"] = tools
        response = client.messages.create(**kwargs)
        text = ""
        tool_calls = []
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))
        return LLMResponse(
            text=text,
            model=model,
            provider="anthropic",
            tokens_used=response.usage.input_tokens + response.usage.output_tokens,
            finish_reason=response.stop_reason or "stop",
            tool_calls=tool_calls or None,
            raw={"id": response.id, "content": [{"type": b.type} for b in response.content]},
        )

    async def _call_openai(
        self, client: Any, model: str, messages: list[dict], tools: list[dict] | None
    ) -> LLMResponse:
        """Call OpenAI. Raises on any error so the retry wrapper can handle it."""
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            kwargs["tools"] = _to_openai_tools(tools)
        response = await asyncio.to_thread(client.chat.completions.create, **kwargs)
        choice = response.choices[0]
        tool_calls = None
        if choice.message.tool_calls:
            import json as _json
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    input=_json.loads(tc.function.arguments) if tc.function.arguments else {},
                )
                for tc in choice.message.tool_calls
            ]
        return LLMResponse(
            text=choice.message.content or "",
            model=model,
            provider="openai",
            tokens_used=response.usage.total_tokens if response.usage else 0,
            finish_reason=choice.finish_reason or "stop",
            tool_calls=tool_calls,
        )

    async def _call_google(
        self, client: Any, model: str, messages: list[dict], tools: list[dict] | None
    ) -> LLMResponse:
        """Call Google Gemini via OpenAI-compatible endpoint."""
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            kwargs["tools"] = _to_openai_tools(tools)
        response = await asyncio.to_thread(client.chat.completions.create, **kwargs)
        choice = response.choices[0]
        tool_calls = None
        if choice.message.tool_calls:
            import json as _json
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    input=_json.loads(tc.function.arguments) if tc.function.arguments else {},
                )
                for tc in choice.message.tool_calls
            ]
        return LLMResponse(
            text=choice.message.content or "",
            model=model,
            provider="google",
            tokens_used=response.usage.total_tokens if response.usage else 0,
            finish_reason=choice.finish_reason or "stop",
            tool_calls=tool_calls,
        )

    async def _call_vertex(
        self, config: dict, model: str, messages: list[dict],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """Call Vertex AI Gemini with tool calling support."""
        import subprocess
        import httpx

        project_id = config["project_id"]
        region = config["region"]

        token_result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True, text=True, timeout=10,
        )
        if token_result.returncode != 0:
            raise RuntimeError("Failed to get gcloud access token")
        access_token = token_result.stdout.strip()

        contents = []
        system_text = ""
        for m in messages:
            role = m.get("role", "user")

            if role == "system":
                content = m.get("content", "")
                if isinstance(content, str):
                    system_text += content + "\n"
                continue

            if "parts" in m and role in ("model", "user"):
                contents.append(m)
                continue

            content = m.get("content", "")
            vertex_role = "model" if role == "assistant" else "user"

            if isinstance(content, str) and content:
                contents.append({"role": vertex_role, "parts": [{"text": content}]})
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append({"text": block["text"]})
                        elif block.get("type") == "tool_use":
                            parts.append({"functionCall": {"name": block["name"], "args": block.get("input", {})}})
                if parts:
                    contents.append({"role": vertex_role, "parts": parts})

        url = (
            f"https://{region}-aiplatform.googleapis.com/v1/"
            f"projects/{project_id}/locations/{region}/"
            f"publishers/google/models/{model}:generateContent"
        )
        payload: dict[str, Any] = {"contents": contents}
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text.strip()}]}

        if tools:
            function_declarations = []
            for t in tools:
                name = t.get("name", "")
                if not name:
                    continue
                schema = t.get("input_schema", {"type": "object", "properties": {}})
                clean_schema = {
                    "type": schema.get("type", "object"),
                    "properties": schema.get("properties", {}),
                }
                if schema.get("required"):
                    clean_schema["required"] = schema["required"]
                function_declarations.append({
                    "name": name,
                    "description": t.get("description", ""),
                    "parameters": clean_schema,
                })
            if function_declarations:
                payload["tools"] = [{"functionDeclarations": function_declarations}]

        async with httpx.AsyncClient(timeout=90.0) as http:
            resp = await http.post(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        candidates = data.get("candidates", [])
        text = ""
        tool_calls_out = []

        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            for part in parts:
                if "text" in part:
                    text += part["text"]
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    tool_calls_out.append(ToolCall(
                        id=f"vertex_{fc['name']}_{len(tool_calls_out)}",
                        name=fc["name"],
                        input=fc.get("args", {}),
                    ))

        usage = data.get("usageMetadata", {})
        input_tokens = usage.get("promptTokenCount", 0)
        output_tokens = usage.get("candidatesTokenCount", 0)

        return LLMResponse(
            text=text,
            model=model,
            provider="vertex",
            tokens_used=input_tokens + output_tokens,
            finish_reason=candidates[0].get("finishReason", "STOP") if candidates else "STOP",
            tool_calls=tool_calls_out or None,
            raw={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        )

    def available_providers(self) -> list[str]:
        return list(self._clients.keys()) or ["simulated"]

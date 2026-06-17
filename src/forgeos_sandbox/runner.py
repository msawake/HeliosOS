"""
Sandbox agent runtime — runs inside an isolated Docker container.

Executes an agentic loop (LLM -> tool_use -> proxy to API -> tool_result -> LLM).
All tool calls are proxied through the Helios OS API where the Kernel validates
permissions before executing.

Environment variables:
  AGENT_ID, AGENT_TOKEN, FORGEOS_API_URL, AGENT_MODEL, AGENT_PROVIDER,
  AGENT_SYSTEM_PROMPT, AGENT_TOOLS (JSON), AGENT_PROMPT, AGENT_MAX_TURNS
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s | sandbox | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("forgeos_sandbox")

# Explicit output budget for the OpenAI/vLLM path. Without it the OpenAI SDK
# defaults to a model-specific cap (~4096), which isn't enough for a reasoning
# model like Qwen 3.6 to fit its chain-of-thought AND the content + tool_call —
# the response truncates and the loop sees content=None, tool_calls=[] (an empty
# turn). Qwen 3.6's context is 131k, so 65k output headroom is safe; vLLM only
# allocates what's generated. Mirrors src/platform/llm_router._OPENAI_MAX_TOKENS.
_OPENAI_MAX_TOKENS = int(os.environ.get("FORGEOS_OPENAI_MAX_TOKENS", "65536"))


class SandboxRunner:
    """Agentic loop running inside a sandboxed container."""

    def __init__(self):
        self.agent_id = os.environ.get("AGENT_ID", "")
        self.token = os.environ.get("AGENT_TOKEN", "")
        self.api_url = os.environ.get("FORGEOS_API_URL", "http://localhost:5000")
        self.model = os.environ.get("AGENT_MODEL", "gpt-4o-mini")
        self.provider = os.environ.get("AGENT_PROVIDER", "openai")
        self.system_prompt = os.environ.get("AGENT_SYSTEM_PROMPT", "You are a helpful agent.")
        self.prompt = os.environ.get("AGENT_PROMPT", "")
        self.max_turns = int(os.environ.get("AGENT_MAX_TURNS", "15"))
        try:
            self.allowed_tools: list[str] = json.loads(os.environ.get("AGENT_TOOLS", "[]"))
        except json.JSONDecodeError:
            self.allowed_tools = []
        self._fn_names: dict[str, str] = {}  # gemini functionCall id -> name

        self._http = httpx.Client(
            base_url=self.api_url,
            headers={"X-Agent-Token": self.token, "Content-Type": "application/json"},
            timeout=120,
        )
        if not self.agent_id or not self.token:
            logger.error("Missing AGENT_ID or AGENT_TOKEN")
            sys.exit(1)
        logger.info("Sandbox starting: agent=%s model=%s tools=%d", self.agent_id, self.model, len(self.allowed_tools))

    def run(self, prompt: str | None = None) -> dict:
        prompt = prompt if prompt is not None else self.prompt
        if not prompt:
            return {"status": "failed", "error": "No AGENT_PROMPT"}
        self._fn_names = {}  # reset per-invocation gemini functionCall id map

        tool_schemas = self._build_tool_schemas()
        messages = [{"role": "user", "content": prompt}]
        all_tool_calls: list[dict] = []
        final_text = ""
        start = time.time()

        for turn in range(self.max_turns):
            logger.info("Turn %d/%d", turn + 1, self.max_turns)
            response = self._call_llm(messages, tool_schemas)
            if not response:
                return {"status": "failed", "error": "LLM call failed"}

            text = response.get("text", "")
            tool_calls = response.get("tool_calls", [])
            if not tool_calls:
                final_text = text
                break

            # Build assistant message. Carry a reasoning model's chain-of-thought
            # forward as a <think> block so it doesn't re-derive its plan from
            # scratch each turn (the symptom: Qwen re-exploring the same files
            # and never converging). Matches src/platform/llm_router's loop.
            reasoning = response.get("reasoning")
            assistant_content = []
            carried = (f"<think>{reasoning}</think>\n" if reasoning else "") + (text or "")
            if carried:
                assistant_content.append({"type": "text", "text": carried})
            for tc in tool_calls:
                assistant_content.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["input"]})
            messages.append({"role": "assistant", "content": assistant_content})

            # Execute tools via API proxy
            tool_results = []
            for tc in tool_calls:
                all_tool_calls.append({"name": tc["name"], "input": tc["input"]})
                logger.info("  Tool: %s", tc["name"])
                result = self._proxy_tool(tc["name"], tc["input"])
                tool_results.append({"type": "tool_result", "tool_use_id": tc["id"], "content": json.dumps(result) if isinstance(result, dict) else str(result)})
            messages.append({"role": "user", "content": tool_results})
        else:
            final_text = text or "[Max turns reached]"

        elapsed = time.time() - start
        result = {"status": "completed", "agent_id": self.agent_id, "output": final_text, "tool_calls": all_tool_calls, "turns": min(turn + 1, self.max_turns), "elapsed_seconds": round(elapsed, 2)}
        self._report_result(result)
        logger.info("Done in %.1fs (%d turns, %d tools)", elapsed, result["turns"], len(all_tool_calls))
        logger.info("Output: %s", (final_text or "").strip()[:1000])
        return result

    def _call_llm(self, messages, tools):
        try:
            if self.provider == "anthropic":
                return self._call_anthropic(messages, tools)
            if self.provider in ("google", "vertex"):
                return self._call_google(messages, tools)
            return self._call_openai(messages, tools)
        except Exception as e:
            logger.error("LLM error: %s", e)
            return None

    def _call_google(self, messages, tools):
        """Google AI Studio (Gemini) via REST — sandbox agents on gemini-* models.
        Converts the runner's anthropic-style message blocks to Gemini `contents`."""
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            raise RuntimeError("no GEMINI_API_KEY/GOOGLE_API_KEY in sandbox env")
        contents = []
        for msg in messages:
            role, content = msg["role"], msg["content"]
            g_role = "model" if role == "assistant" else "user"
            if isinstance(content, str):
                contents.append({"role": g_role, "parts": [{"text": content}]})
                continue
            parts = []
            for b in content:
                t = b.get("type")
                if t == "text" and b.get("text"):
                    parts.append({"text": b["text"]})
                elif t == "tool_use":
                    parts.append({"functionCall": {"name": b["name"], "args": b.get("input", {})}})
                elif t == "tool_result":
                    nm = self._fn_names.get(b.get("tool_use_id"), "tool")
                    parts.append({"text": f"Result of {nm}: {b.get('content', '')}"})
            if parts:
                contents.append({"role": g_role, "parts": parts})
        payload: dict = {"contents": contents}
        if self.system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": self.system_prompt}]}
        if tools:
            payload["tools"] = [{"functionDeclarations": [
                {"name": t["name"], "description": t.get("description", ""),
                 "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}
                for t in tools]}]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={api_key}"
        resp = httpx.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        cand = (data.get("candidates") or [{}])[0]
        out_parts = cand.get("content", {}).get("parts", []) or []
        text = "".join(p["text"] for p in out_parts if "text" in p)
        tcs = []
        for p in out_parts:
            if "functionCall" in p:
                fc = p["functionCall"]
                fid = f"g_{fc.get('name', 'fn')}_{len(tcs)}"
                tcs.append({"id": fid, "name": fc.get("name", ""), "input": fc.get("args", {})})
                self._fn_names[fid] = fc.get("name", "")
        usage = data.get("usageMetadata", {})
        return {"text": text, "tool_calls": tcs,
                "tokens": usage.get("promptTokenCount", 0) + usage.get("candidatesTokenCount", 0)}

    def _call_anthropic(self, messages, tools):
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(model=self.model, max_tokens=4096, system=self.system_prompt, messages=messages, tools=tools or anthropic.NOT_GIVEN)
        text_parts = [b.text for b in resp.content if hasattr(b, "text")]
        tc = [{"id": b.id, "name": b.name, "input": b.input} for b in resp.content if b.type == "tool_use"]
        return {"text": " ".join(text_parts), "tool_calls": tc, "tokens": resp.usage.input_tokens + resp.usage.output_tokens}

    def _call_openai(self, messages, tools):
        import openai
        client = openai.OpenAI()
        oai_msgs = [{"role": "system", "content": self.system_prompt}]
        for msg in messages:
            if msg["role"] == "user" and isinstance(msg["content"], list):
                if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in msg["content"]):
                    for c in msg["content"]:
                        if c.get("type") == "tool_result":
                            oai_msgs.append({"role": "tool", "tool_call_id": c["tool_use_id"], "content": c["content"]})
                    continue
            if msg["role"] == "assistant" and isinstance(msg["content"], list):
                tp = [c["text"] for c in msg["content"] if c.get("type") == "text"]
                tcs = [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["input"])}} for c in msg["content"] if c.get("type") == "tool_use"]
                m = {"role": "assistant", "content": " ".join(tp) or None}
                if tcs:
                    m["tool_calls"] = tcs
                oai_msgs.append(m)
                continue
            oai_msgs.append(msg)
        oai_tools = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t.get("input_schema", {})}} for t in tools] if tools else None
        resp = client.chat.completions.create(model=self.model, messages=oai_msgs, tools=oai_tools, max_tokens=_OPENAI_MAX_TOKENS)
        choice = resp.choices[0]
        msg = choice.message
        tc = []
        if msg.tool_calls:
            tc = [{"id": t.id, "name": t.function.name, "input": json.loads(t.function.arguments)} for t in msg.tool_calls]
        # Reasoning models (Qwen 3.x via vLLM --reasoning-parser, DeepSeek-R1)
        # surface their chain-of-thought in a separate field — `reasoning`
        # (vLLM) or `reasoning_content` (R1-style) — leaving `content` empty.
        # Capture it so (a) when the model emits ONLY reasoning + no content +
        # no tool calls we surface it as text instead of returning an empty turn,
        # and (b) the loop can echo it back so the model keeps its plan across
        # turns. Mirrors src/platform/llm_router._call_openai.
        reasoning = getattr(msg, "reasoning", None) or getattr(msg, "reasoning_content", None)
        text = msg.content or ""
        if not text and not tc and reasoning:
            text = reasoning
        return {"text": text, "reasoning": reasoning, "tool_calls": tc, "tokens": resp.usage.total_tokens if resp.usage else 0}

    def _proxy_tool(self, tool_name, tool_input):
        try:
            resp = self._http.post("/api/sandbox/tool", json={"tool_name": tool_name, "tool_input": tool_input})
            return resp.json() if resp.status_code == 200 else {"error": f"API {resp.status_code}: {resp.text[:200]}"}
        except Exception as e:
            return {"error": f"Proxy failed: {e}"}

    def _build_tool_schemas(self):
        if not self.allowed_tools:
            return []
        try:
            resp = self._http.get("/api/platform/tools")
            if resp.status_code == 200:
                return [t for t in resp.json() if t.get("name") in self.allowed_tools]
        except Exception:
            pass
        return [{"name": t, "description": f"Tool: {t}", "input_schema": {"type": "object", "properties": {}}} for t in self.allowed_tools]

    def _report_result(self, result):
        try:
            self._http.post("/api/sandbox/result", json={"agent_id": self.agent_id, **result})
        except Exception:
            pass


def main():
    runner = SandboxRunner()
    result = runner.run()
    sys.exit(0 if result.get("status") == "completed" else 1)

if __name__ == "__main__":
    main()

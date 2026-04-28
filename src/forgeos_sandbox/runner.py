"""
<<<<<<< HEAD
Sandbox agent runtime — runs inside an isolated container.
=======
Sandbox agent runtime — runs inside an isolated Docker container.
>>>>>>> origin/main

Executes an agentic loop (LLM -> tool_use -> proxy to API -> tool_result -> LLM).
All tool calls are proxied through the ForgeOS API where the Kernel validates
permissions before executing.

<<<<<<< HEAD
Can be configured via environment variables (single-agent legacy mode) or via
a config dict (multi-agent environment mode).
=======
Environment variables:
  AGENT_ID, AGENT_TOKEN, FORGEOS_API_URL, AGENT_MODEL, AGENT_PROVIDER,
  AGENT_SYSTEM_PROMPT, AGENT_TOOLS (JSON), AGENT_PROMPT, AGENT_MAX_TURNS
>>>>>>> origin/main
"""

from __future__ import annotations

<<<<<<< HEAD
import asyncio
=======
>>>>>>> origin/main
import json
import logging
import os
import sys
import time

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s | sandbox | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("forgeos_sandbox")


class SandboxRunner:
    """Agentic loop running inside a sandboxed container."""

<<<<<<< HEAD
    def __init__(self, config: dict | None = None):
        if config:
            self.agent_id = config["agent_id"]
            self.token = config["agent_token"]
            self.api_url = config.get("api_url", "http://localhost:5000")
            self.model = config.get("model", "gpt-4o-mini")
            self.provider = config.get("provider", "openai")
            self.system_prompt = config.get("system_prompt", "You are a helpful agent.")
            self.prompt = config.get("prompt", "")
            self.max_turns = int(config.get("max_turns", 15))
            self.allowed_tools: list[str] = config.get("tools", [])
            self.loop_mode = config.get("loop_mode", False)
            self.loop_interval = int(config.get("loop_interval", 120))
            self._log = logging.getLogger(f"forgeos_sandbox.agent.{self.agent_id}")
        else:
            self.agent_id = os.environ.get("AGENT_ID", "")
            self.token = os.environ.get("AGENT_TOKEN", "")
            self.api_url = os.environ.get("FORGEOS_API_URL", "http://localhost:5000")
            self.model = os.environ.get("AGENT_MODEL", "gpt-4o-mini")
            self.provider = os.environ.get("AGENT_PROVIDER", "openai")
            self.system_prompt = os.environ.get("AGENT_SYSTEM_PROMPT", "You are a helpful agent.")
            self.prompt = os.environ.get("AGENT_PROMPT", "")
            self.max_turns = int(os.environ.get("AGENT_MAX_TURNS", "15"))
            self.loop_mode = os.environ.get("AGENT_LOOP_MODE", "false").lower() == "true"
            self.loop_interval = int(os.environ.get("AGENT_LOOP_INTERVAL", "120"))
            try:
                self.allowed_tools = json.loads(os.environ.get("AGENT_TOOLS", "[]"))
            except json.JSONDecodeError:
                self.allowed_tools = []
            self._log = logger
=======
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
>>>>>>> origin/main

        self._http = httpx.Client(
            base_url=self.api_url,
            headers={"X-Agent-Token": self.token, "Content-Type": "application/json"},
            timeout=120,
        )
<<<<<<< HEAD
        self._async_http: httpx.AsyncClient | None = None
        self._stopped = False

        if not self.agent_id or not self.token:
            self._log.error("Missing agent_id or agent_token")
            if not config:
                sys.exit(1)
            return
        self._log.info("Sandbox starting: agent=%s model=%s tools=%d", self.agent_id, self.model, len(self.allowed_tools))

    def stop(self):
        self._stopped = True

    def run(self) -> dict:
        if not self.prompt:
            return {"status": "failed", "error": "No prompt"}
=======
        if not self.agent_id or not self.token:
            logger.error("Missing AGENT_ID or AGENT_TOKEN")
            sys.exit(1)
        logger.info("Sandbox starting: agent=%s model=%s tools=%d", self.agent_id, self.model, len(self.allowed_tools))

    def run(self) -> dict:
        if not self.prompt:
            return {"status": "failed", "error": "No AGENT_PROMPT"}
>>>>>>> origin/main

        tool_schemas = self._build_tool_schemas()
        messages = [{"role": "user", "content": self.prompt}]
        all_tool_calls: list[dict] = []
        final_text = ""
        start = time.time()

        for turn in range(self.max_turns):
<<<<<<< HEAD
            if self._stopped:
                return {"status": "stopped", "agent_id": self.agent_id}
            self._log.info("Turn %d/%d", turn + 1, self.max_turns)
=======
            logger.info("Turn %d/%d", turn + 1, self.max_turns)
>>>>>>> origin/main
            response = self._call_llm(messages, tool_schemas)
            if not response:
                return {"status": "failed", "error": "LLM call failed"}

            text = response.get("text", "")
            tool_calls = response.get("tool_calls", [])
            if not tool_calls:
                final_text = text
                break

<<<<<<< HEAD
=======
            # Build assistant message
>>>>>>> origin/main
            assistant_content = []
            if text:
                assistant_content.append({"type": "text", "text": text})
            for tc in tool_calls:
                assistant_content.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["input"]})
            messages.append({"role": "assistant", "content": assistant_content})

<<<<<<< HEAD
            tool_results = []
            for tc in tool_calls:
                all_tool_calls.append({"name": tc["name"], "input": tc["input"]})
                self._log.info("  Tool: %s", tc["name"])
=======
            # Execute tools via API proxy
            tool_results = []
            for tc in tool_calls:
                all_tool_calls.append({"name": tc["name"], "input": tc["input"]})
                logger.info("  Tool: %s", tc["name"])
>>>>>>> origin/main
                result = self._proxy_tool(tc["name"], tc["input"])
                tool_results.append({"type": "tool_result", "tool_use_id": tc["id"], "content": json.dumps(result) if isinstance(result, dict) else str(result)})
            messages.append({"role": "user", "content": tool_results})
        else:
            final_text = text or "[Max turns reached]"

        elapsed = time.time() - start
        result = {"status": "completed", "agent_id": self.agent_id, "output": final_text, "tool_calls": all_tool_calls, "turns": min(turn + 1, self.max_turns), "elapsed_seconds": round(elapsed, 2)}
        self._report_result(result)
<<<<<<< HEAD
        self._log.info("Done in %.1fs (%d turns, %d tools)", elapsed, result["turns"], len(all_tool_calls))
        return result

    async def run_async(self) -> dict:
        """Run the agentic loop as an async task."""
        if self.loop_mode:
            self._log.info("Always-on loop (interval=%ds)", self.loop_interval)
            while not self._stopped:
                try:
                    await asyncio.to_thread(self.run)
                except Exception as e:
                    self._log.error("Loop iteration failed: %s", e)
                await asyncio.sleep(self.loop_interval)
            return {"status": "stopped", "agent_id": self.agent_id}
        return await asyncio.to_thread(self.run)

=======
        logger.info("Done in %.1fs (%d turns, %d tools)", elapsed, result["turns"], len(all_tool_calls))
        return result

>>>>>>> origin/main
    def _call_llm(self, messages, tools):
        try:
            if self.provider == "anthropic":
                return self._call_anthropic(messages, tools)
<<<<<<< HEAD
            if self.provider == "google":
                return self._call_google(messages, tools)
            return self._call_openai(messages, tools)
        except Exception as e:
            self._log.error("LLM error: %s", e)
=======
            return self._call_openai(messages, tools)
        except Exception as e:
            logger.error("LLM error: %s", e)
>>>>>>> origin/main
            return None

    def _call_anthropic(self, messages, tools):
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(model=self.model, max_tokens=4096, system=self.system_prompt, messages=messages, tools=tools or anthropic.NOT_GIVEN)
        text_parts = [b.text for b in resp.content if hasattr(b, "text")]
        tc = [{"id": b.id, "name": b.name, "input": b.input} for b in resp.content if b.type == "tool_use"]
        return {"text": " ".join(text_parts), "tool_calls": tc, "tokens": resp.usage.input_tokens + resp.usage.output_tokens}

<<<<<<< HEAD
    def _call_openai(self, messages, tools, client=None):
        import openai
        if client is None:
            client = openai.OpenAI()
=======
    def _call_openai(self, messages, tools):
        import openai
        client = openai.OpenAI()
>>>>>>> origin/main
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
        resp = client.chat.completions.create(model=self.model, messages=oai_msgs, tools=oai_tools)
        choice = resp.choices[0]
        tc = []
        if choice.message.tool_calls:
            tc = [{"id": t.id, "name": t.function.name, "input": json.loads(t.function.arguments)} for t in choice.message.tool_calls]
        return {"text": choice.message.content or "", "tool_calls": tc, "tokens": resp.usage.total_tokens if resp.usage else 0}

<<<<<<< HEAD
    def _call_google(self, messages, tools):
        import openai
        client = openai.OpenAI(
            api_key=os.environ.get("GOOGLE_API_KEY", ""),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        return self._call_openai(messages, tools, client=client)

=======
>>>>>>> origin/main
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
<<<<<<< HEAD

    if not runner.loop_mode:
        result = runner.run()
        sys.exit(0 if result.get("status") == "completed" else 1)

    logger.info("Always-on loop (interval=%ds)", runner.loop_interval)
    while True:
        try:
            runner.run()
        except Exception as e:
            logger.error("Loop iteration failed: %s", e)
        time.sleep(runner.loop_interval)
=======
    result = runner.run()
    sys.exit(0 if result.get("status") == "completed" else 1)
>>>>>>> origin/main

if __name__ == "__main__":
    main()

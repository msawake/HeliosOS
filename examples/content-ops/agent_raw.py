"""
Content Pipeline — NO GOVERNANCE (raw version).

Same dual-LLM pipeline, zero ForgeOS runtime checks.
Compare with agent.py to see what governance adds.

⚠ RISKS WITHOUT GOVERNANCE:
  - No client isolation → pharma data leaks to fintech
  - No budget control → one client burns all budget
  - No HITL → medical claims published without review
  - No audit trail → no proof of compliance review
  - No tool restrictions → AI images generated for regulated clients
  - No A2A check → cross-client agent contamination
"""

from __future__ import annotations

import asyncio
import os
import sys
import httpx

sys.path.insert(0, os.path.dirname(__file__))
from clients import CLIENTS, CONTENT_TYPES

ATLAS_URL = os.environ.get("ATLAS_GATEWAY_URL", "")
ATLAS_KEY = os.environ.get("ATLAS_GATEWAY_KEY", "")


async def call_llm(model: str, prompt: str, system: str = "") -> str:
    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(
            f"{ATLAS_URL}/chat/completions",
            headers={"Authorization": f"Bearer {ATLAS_KEY}"},
            json={
                "model": model,
                "messages": [
                    *([{"role": "system", "content": system}] if system else []),
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 2048,
            },
        )
        return resp.json()["choices"][0]["message"]["content"]


async def produce_content(client_id: str, topic: str):
    client = CLIENTS[client_id]

    # No check_data()    → any client data accessible
    # No budget()        → no spending limit
    # No reserve()       → no cost tracking
    # No check_tool()    → all tools allowed (even AI images for pharma)

    # ── PRODUCE ──
    draft = await call_llm(
        "gemini-2.5-flash",
        topic,
        system=f"You are a content producer for {client['name']}. "
               f"Brand voice: {client['brand_voice']}",
    )

    # No audit()         → no record of what was generated
    # No check_a2a()     → producer calls any editor, any client

    # ── REVIEW ──
    review = await call_llm(
        "claude-sonnet",
        f"Review this draft:\n\n{draft}",
        system=f"You are an editor for {client['name']}. "
               f"Check compliance: {', '.join(client['compliance'])}",
    )

    # No ask_human()     → regulated content auto-published
    # No commit()        → budget untracked
    # No checkpoint()    → crash = redo everything
    # No audit()         → no proof of review

    print(f"\n{'='*60}")
    print(f"CLIENT: {client['name']}")
    print(f"DRAFT: {draft[:200]}...")
    print(f"REVIEW: {review[:200]}...")
    print(f"{'='*60}")


async def main():
    for client_id in CLIENTS:
        await produce_content(client_id, CLIENTS[client_id]["sample_topics"][0])


if __name__ == "__main__":
    asyncio.run(main())

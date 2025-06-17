# ─────────────────────────────────────────────────────────────────────────────
# services/intent_api/reply_agent.py
"""
Generate a natural Teams reply using full memory, send it, and persist turn.
"""
from __future__ import annotations

import json, logging, os
from datetime import datetime
from typing import Any, Dict, List

import requests
from openai import OpenAI

# ─────────────── Project helpers ────────────────
from common.graph_auth import get_access_token
from common.memory_helpers import (
    save_message,
    fetch_chat_history,
    fetch_global_history,
    semantic_search,
)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
logging.getLogger(__name__).setLevel(logging.INFO)

# ─────────────── Graph helpers ────────────────
def _ms_graph(url: str, token: str, *, method: str = "GET",
              payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    r = requests.request(
        method,
        url,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=payload,
        timeout=10,
    )
    r.raise_for_status()
    return r.json() if r.text else {}


def _send_teams_reply(chat_id: str, message: str, token: str) -> int:
    url  = f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages"
    body = {"body": {"contentType": "text", "content": message}}
    return requests.post(
        url,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=body,
        timeout=10,
    ).status_code


# ─────────────── Public API ────────────────
def process_reply(chat_id: str, last_user_text: str) -> None:
    """
    Compose a helpful answer and post it to Teams.
    Raises on MS Graph or OpenAI errors so caller can log.
    """
    access_token, _ = get_access_token()

    # Fetch chat metadata for persistence
    chat_type = _ms_graph(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}?$select=chatType",
        access_token,
    ).get("chatType", "unknown")

    # Memory tiers
    chat_mem   = fetch_chat_history(chat_id, limit=40)
    global_mem = fetch_global_history(limit=8)
    semantic_mem = semantic_search(last_user_text, chat_id,
                                   k_chat=8, k_global=4)

    def _append(dst: List[Dict[str, str]], rows):
        for row in rows:
            dst.append({
                "role": "user" if row["sender"] == "user" else "assistant",
                "content": row["content"],
            })

    messages: List[Dict[str, str]] = [{
        "role": "system",
        "content": (
            "You are John, a concise yet friendly executive assistant. "
            "Answer clearly, reference context when useful, and keep replies under 120 words. "
            "NO markdown code blocks, just plain text."
        ),
    }]
    _append(messages, chat_mem)
    if semantic_mem:
        messages.append({"role": "system", "content": "🔎 Relevant context:"})
        _append(messages, semantic_mem)
    if global_mem:
        messages.append({"role": "system", "content": "🌐 Other chats context:"})
        _append(messages, global_mem)
    messages.append({"role": "user", "content": last_user_text})

    reply = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
    ).choices[0].message.content.strip()

    # Send + persist
    status = _send_teams_reply(chat_id, reply, access_token)
    save_message(chat_id, "assistant", reply, chat_type)

    logging.info("✓ reply sent (status %s)", status)

# ─────────────────────────────────────────────────────────────────────────────
# services/intent_api/reply_agent.py
"""
Compose a Teams reply, post it, and persist the assistant turn.

•  Standard path  → GPT drafts a ≤120-word answer anchored in memory.
•  If `missing_info` is supplied (e.g.,   "recipients" / "subject" / "body")
   the reply is generated locally: a short, polite question asking
   the user for that specific detail—no GPT call needed.
"""
from __future__ import annotations
import logging, os, requests
from typing import Any, Dict, List

from openai import OpenAI
from common.graph_auth import get_access_token
from common.memory_helpers import (
    save_message,
    fetch_chat_history,
    fetch_global_history,
    semantic_search,
)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
logging.getLogger(__name__).setLevel(logging.INFO)


# ─────────── Graph helpers ───────────
def _graph(url: str, token: str, *,
           method: str = "GET",
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


def _teams_post(chat_id: str, text: str, token: str) -> int:
    body = {"body": {"contentType": "text", "content": text}}
    return requests.post(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=body,
        timeout=10,
    ).status_code


# ─────────── Public entry-point ───────────
def process_reply(
    chat_id: str,
    last_user_text: str,
    missing_info: str | None = None,  # e.g. "recipients" / "subject" / "body"
) -> None:
    """
    Send a reply to Teams. If `missing_info` is given, immediately ask
    for it; otherwise run GPT to craft a normal answer.
    """
    access_token, _ = get_access_token()

    # --- Ask for missing field directly (no GPT) -----------------------
    if missing_info:
        polite = (
            f"I need the e-mail {missing_info} to proceed. "
            "Could you provide that, please?"
        )
        _teams_post(chat_id, polite, access_token)
        save_message(chat_id, "assistant", polite, "unknown")
        logging.info("✓ ask-for-%s sent", missing_info)
        return

    # --- Full GPT flow -------------------------------------------------
    chat_type = _graph(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}?$select=chatType",
        access_token,
    ).get("chatType", "unknown")

    chat_mem     = fetch_chat_history(chat_id, limit=40)
    global_mem   = fetch_global_history(limit=8)
    semantic_mem = semantic_search(last_user_text, chat_id, k_chat=8, k_global=4)

    def _append(dst: List[Dict[str, str]], rows):
        for r in rows:
            dst.append({
                "role": "user" if r["sender"] == "user" else "assistant",
                "content": r["content"],
            })

    msgs: List[Dict[str, str]] = [{
        "role": "system",
        "content": (
            "You are John, a concise yet friendly executive assistant. "
            "Answer clearly, reference context when helpful, and keep replies ≤120 words. "
            "NO markdown code blocks; plain text only."
        ),
    }]
    _append(msgs, chat_mem)
    if semantic_mem:
        msgs.append({"role": "system", "content": "🔎 Relevant context:"})
        _append(msgs, semantic_mem)
    if global_mem:
        msgs.append({"role": "system", "content": "🌐 Other chats context:"})
        _append(msgs, global_mem)
    msgs.append({"role": "user", "content": last_user_text})

    reply = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=msgs,
    ).choices[0].message.content.strip()

    status = _teams_post(chat_id, reply, access_token)
    save_message(chat_id, "assistant", reply, chat_type)
    logging.info("✓ reply sent (status %s)", status)

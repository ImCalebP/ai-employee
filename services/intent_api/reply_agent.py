# ─────────────────────────────────────────────────────────────────────────────
# services/intent_api/reply_agent.py
"""
Generate a Teams reply and persist it.

If called with `missing_info` ("recipients" | "subject" | "body")
the reply skips GPT and immediately asks the user for that detail.
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


def _graph(url: str, token: str, *,
           method: str = "GET",
           payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    r = requests.request(
        method, url,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=payload, timeout=10,
    )
    r.raise_for_status()
    return r.json() if r.text else {}


def _teams_post(chat_id: str, text: str, token: str) -> int:
    body = {"body": {"contentType": "text", "content": text}}
    return requests.post(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=body, timeout=10,
    ).status_code


def process_reply(
    chat_id: str,
    last_user_text: str,
    missing_info: str | None = None,
) -> None:
    """
    Send a reply. If missing_info is given, ask for that field directly;
    otherwise build a normal GPT response.
    """
    access_token, _ = get_access_token()

    # --- immediate ask branch -----------------------------------------
    if missing_info:
        ask = {
            "recipients": "Could you share the e-mail address(es) to send this to?",
            "subject":    "What subject line would you like?",
            "body":       "What should the body of the e-mail say?",
        }[missing_info]
        _teams_post(chat_id, ask, access_token)
        save_message(chat_id, "assistant", ask, "unknown")
        logging.info("✓ prompt for %s sent", missing_info)
        return

    # --- normal GPT reply ---------------------------------------------
    chat_type = _graph(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}?$select=chatType",
        access_token,
    ).get("chatType", "unknown")

    chat_mem     = fetch_chat_history(chat_id, 40)
    global_mem   = fetch_global_history(8)
    semantic_mem = semantic_search(last_user_text, chat_id, 8, 4)

    def _add(dst: List[Dict[str, str]], rows):
        for r in rows:
            dst.append({"role": "user" if r["sender"] == "user" else "assistant",
                        "content": r["content"]})

    msgs: List[Dict[str, str]] = [{
        "role": "system",
        "content": (
            "You are John, a concise yet friendly executive assistant.\n"
            "Reply in ≤120 words. Use context when helpful. Plain text only."
        ),
    }]
    _add(msgs, chat_mem)
    if semantic_mem:
        msgs.append({"role": "system", "content": "🔎 Relevant context:"})
        _add(msgs, semantic_mem)
    if global_mem:
        msgs.append({"role": "system", "content": "🌐 Other chats context:"})
        _add(msgs, global_mem)
    msgs.append({"role": "user", "content": last_user_text})

    reply = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=msgs,
    ).choices[0].message.content.strip()

    status = _teams_post(chat_id, reply, access_token)
    save_message(chat_id, "assistant", reply, chat_type)
    logging.info("✓ reply sent (%s)", status)

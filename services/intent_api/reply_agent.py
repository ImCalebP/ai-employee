# ─────────────────────────────────────────────────────────────────────────────
# services/intent_api/reply_agent.py
"""
Responds in Teams. If `missing_info` is provided, asks the user directly;
otherwise drafts a normal GPT reply.
"""
from __future__ import annotations
import logging, os, requests, re
from typing import Any, Dict, List

from openai import OpenAI
from common.graph_auth import get_access_token
from common.memory_helpers import (
    save_message, fetch_chat_history, fetch_global_history, semantic_search
)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
logging.getLogger(__name__).setLevel(logging.INFO)


# ───────── Graph helpers ─────────
def _graph(url: str, token: str, *, method: str = "GET",
           payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    r = requests.request(method, url,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=payload, timeout=10)
    r.raise_for_status()
    return r.json() if r.text else {}


def _teams_post(chat_id: str, text: str, token: str) -> None:
    body = {"body": {"contentType": "text", "content": text}}
    requests.post(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=body, timeout=10).raise_for_status()


# ───────── main entry point ─────────
def process_reply(
    chat_id: str,
    last_user_text: str,
    missing_info: str | None = None,
    custom_prompt: str | None = None,
) -> None:
    token, _ = get_access_token()

    # -------- quick “ask-for-info” branch --------
    if missing_info:
        # normalise any compound like "recipients|subject|body"
        clean = missing_info.lower()
        match = re.search(r"(recipients|subject|body)", clean)
        key = match.group(1) if match else "recipients"

        default = {
            "recipients": "Could you share the e-mail address(es)?",
            "subject":    "What subject line would you like?",
            "body":       "What should the body of the e-mail say?",
        }[key]

        ask = custom_prompt or default
        _teams_post(chat_id, ask, token)
        save_message(chat_id, "assistant", ask, "unknown")
        logging.info("✓ prompt for %s sent", key)
        return

    # -------- full GPT reply --------
    chat_type = _graph(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}?$select=chatType",
        token).get("chatType", "unknown")

    chat_mem   = fetch_chat_history(chat_id, 40)
    global_mem = fetch_global_history(8)
    sem_mem    = semantic_search(last_user_text, chat_id, 8, 4)

    def _append(dst, rows):
        for r in rows:
            dst.append({
                "role": "user" if r["sender"] == "user" else "assistant",
                "content": r["content"],
            })

    msgs: List[Dict[str, str]] = [{
        "role": "system",
        "content": (
            "You are John, an executive assistant. Reply in ≤120 words, "
            "plain text—no code blocks."
        ),
    }]
    _append(msgs, chat_mem)
    if sem_mem:
        msgs += [{"role":"system","content":"🔍 Relevant context:"}]
        _append(msgs, sem_mem)
    if global_mem:
        msgs += [{"role":"system","content":"🌐 Other chats:"}]
        _append(msgs, global_mem)
    msgs.append({"role": "user", "content": last_user_text})

    reply = client.chat.completions.create(
        model="gpt-4o",
        messages=msgs,
    ).choices[0].message.content.strip()

    _teams_post(chat_id, reply, token)
    save_message(chat_id, "assistant", reply, chat_type)
    logging.info("✓ reply sent")

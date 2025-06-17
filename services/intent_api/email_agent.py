# ─────────────────────────────────────────────────────────────────────────────
# services/intent_api/email_agent.py
"""
Compose and send an Outlook e-mail, given a Teams chat_id.

Steps
1. Pull full memory (chat + semantic + global)
2. Ask GPT to output {"to":[…],"subject":"…","body":"…"}
3. Send via Microsoft Graph
4. Persist the assistant turn
"""
from __future__ import annotations
import json, logging, os
from typing import Any, Dict, List

import requests
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

# ─────────────── Graph helpers ────────────────
def _send_graph(
    url: str, token: str, *, method: str = "GET", payload: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    r = requests.request(
        method,
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=10,
    )
    r.raise_for_status()
    return r.json() if r.text else {}


def _send_outlook_mail(details: Dict[str, Any], token: str) -> None:
    """
    Graph v1.0 sendMail endpoint.
    `details` must hold keys: to, subject, body (HTML or plain text)
    """
    url = "https://graph.microsoft.com/v1.0/me/sendMail"
    payload = {
        "message": {
            "subject": details["subject"],
            "body": {"contentType": "Text", "content": details["body"]},
            "toRecipients": [{"emailAddress": {"address": addr}} for addr in details["to"]],
        }
    }
    _send_graph(url, token, method="POST", payload=payload)


# ─────────────── Public API ────────────────
def process_email_request(chat_id: str) -> None:
    """
    Derive e-mail details from chat context, send it, and log the turn.
    Called by intent.py when intent == "send_email".
    """
    access_token, _ = get_access_token()

    # --- Memory ---------------------------------------------------------
    chat_mem = fetch_chat_history(chat_id, limit=40)
    user_turns = [row for row in chat_mem if row["sender"] == "user"]
    last_user_text = user_turns[-1]["content"] if user_turns else ""

    global_mem = fetch_global_history(limit=8)
    semantic_mem = semantic_search(last_user_text, chat_id, k_chat=8, k_global=4)

    # --- GPT prompt -----------------------------------------------------
    def _append(dst: List[Dict[str, str]], rows):
        for r in rows:
            dst.append(
                {
                    "role": "user" if r["sender"] == "user" else "assistant",
                    "content": r["content"],
                }
            )

    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You draft Outlook e-mails. "
                "Search ALL context to fill: recipients' e-mail(s), subject, body.\n\n"
                "Return ONE JSON only, exactly:\n"
                '{"to":["a@b.com"],"subject":"...","body":"..."}\n'
                "If any field is still missing after reading everything, "
                'return {"error":"missing X"}'
            ),
        }
    ]
    _append(messages, chat_mem)
    if semantic_mem:
        messages.append({"role": "system", "content": "🔎 Relevant context:"})
        _append(messages, semantic_mem)
    if global_mem:
        messages.append({"role": "system", "content": "🌐 Other chats context:"})
        _append(messages, global_mem)
    messages.append(
        {
            "role": "system",
            "content": "Output strictly one JSON object as specified—no comments.",
        }
    )

    draft: Dict[str, Any] = json.loads(
        client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=messages,
        ).choices[0].message.content
    )

    # --- Validate -------------------------------------------------------
    if "error" in draft:
        raise ValueError(draft["error"])

    required = {"to", "subject", "body"}
    if not required.issubset(draft.keys()):
        raise ValueError(f"Missing keys in e-mail draft: {draft}")

    # --- Send via Outlook ----------------------------------------------
    _send_outlook_mail(draft, access_token)
    logging.info("✓ Outlook mail sent: %s → %s", draft["subject"], draft["to"])

    # --- Persist assistant turn ----------------------------------------
    reply_txt = f"✉️ E-mail sent: “{draft['subject']}” ➜ {', '.join(draft['to'])}"
    chat_type = next((row["chat_type"] for row in chat_mem if row.get("chat_type")), None)
    save_message(chat_id, "assistant", reply_txt, chat_type or "unknown")

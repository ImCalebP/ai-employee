# ─────────────────────────────────────────────────────────────────────────────
# services/intent_api/email_agent.py
"""
Draft and send an Outlook e-mail based on chat context.

• If **any** critical field is missing, raise
  ValueError("missing <field>")  – intent.py will catch this and
  hand control to reply_agent so the user is prompted for details.
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

# ───────── MS Graph helpers ─────────
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


def _send_outlook(details: Dict[str, Any], token: str) -> None:
    payload = {
        "message": {
            "subject": details["subject"],
            "body": {
                "contentType": "Text",
                "content": details["body"],
            },
            "toRecipients": [
                {"emailAddress": {"address": addr}} for addr in details["to"]
            ],
        }
    }
    _graph("https://graph.microsoft.com/v1.0/me/sendMail",
           token, method="POST", payload=payload)


# ───────── Public entry-point ─────────
def process_email_request(chat_id: str) -> None:
    """
    Build e-mail from context and send it.
    Raises ValueError("missing …") if info is incomplete.
    """
    access_token, _ = get_access_token()

    # — Memory -----------------------------------------------------------
    chat_mem     = fetch_chat_history(chat_id, limit=40)
    user_turns   = [r for r in chat_mem if r["sender"] == "user"]
    last_user    = user_turns[-1]["content"] if user_turns else ""
    global_mem   = fetch_global_history(limit=8)
    semantic_mem = semantic_search(last_user, chat_id, k_chat=8, k_global=4)

    def _build_prompt() -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = [{
            "role": "system",
            "content": (
                "You draft Outlook e-mails. Search ALL context to find:\n"
                "  • recipients' e-mails\n  • a concise subject\n  • the body text\n\n"
                "Return ONE JSON only, exactly:\n"
                '{"to":["a@b.com"],"subject":"...","body":"..."}\n'
                "If ANY field is still missing after exhaustive search, "
                'return {"error":"missing recipients"} (or subject / body).\n'
                "NO extra keys, comments, or markdown."
            ),
        }]
        def _ap(dst, rows):
            for r in rows:
                dst.append({
                    "role": "user" if r["sender"] == "user" else "assistant",
                    "content": r["content"],
                })
        _ap(out, chat_mem)
        if semantic_mem:
            out.append({"role": "system", "content": "🔎 Relevant context:"})
            _ap(out, semantic_mem)
        if global_mem:
            out.append({"role": "system", "content": "🌐 Other chats context:"})
            _ap(out, global_mem)
        out.append({
            "role": "system",
            "content": "Output strictly one JSON as specified.",
        })
        return out

    draft = json.loads(
        client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=_build_prompt(),
        ).choices[0].message.content
    )

    # — Validation -------------------------------------------------------
    if "error" in draft:
        raise ValueError(draft["error"])

    for key in ("to", "subject", "body"):
        if key not in draft or not draft[key]:
            raise ValueError(f"missing {key}")

    # — Send e-mail ------------------------------------------------------
    _send_outlook(draft, access_token)
    logging.info("✓ Outlook e-mail sent: %s → %s",
                 draft["subject"], ", ".join(draft["to"]))

    # — Persist assistant turn ------------------------------------------
    reply_txt = f"✉️ E-mail sent: “{draft['subject']}” ➜ {', '.join(draft['to'])}"
    chat_type = next((r.get("chat_type") for r in chat_mem if r.get("chat_type")), None)
    save_message(chat_id, "assistant", reply_txt, chat_type or "unknown")

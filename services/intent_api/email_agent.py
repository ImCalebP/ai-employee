# ─────────────────────────────────────────────────────────────────────────────
# services/intent_api/email_agent.py
"""
Draft (and if possible send) an Outlook e-mail.

Returns:
    {"status":"sent"}                          – e-mail sent & persisted
    {"status":"missing","missing":"subject"}   – need user input; nothing sent
"""
from __future__ import annotations
import json, logging, os, requests
from typing import Any, Dict, List

from openai import OpenAI
from common.graph_auth import get_access_token
from common.memory_helpers import (
    save_message,
    fetch_chat_history,
    fetch_global_history,
    semantic_search,
)
from services.intent_api.reply_agent import process_reply  # circular-safe

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
def process_email_request(chat_id: str) -> Dict[str, str]:
    """
    Try to build & send an e-mail.
    If any field is missing, ask via reply_agent and return status=missing.
    """
    access_token, _ = get_access_token()

    # Memory tiers
    chat_mem     = fetch_chat_history(chat_id, 40)
    user_turns   = [r for r in chat_mem if r["sender"] == "user"]
    last_user    = user_turns[-1]["content"] if user_turns else ""
    global_mem   = fetch_global_history(8)
    semantic_mem = semantic_search(last_user, chat_id, 8, 4)

    def _append(dst: List[Dict[str, str]], rows):
        for r in rows:
            dst.append({"role": "user" if r["sender"] == "user" else "assistant",
                        "content": r["content"]})

    prompt: List[Dict[str, str]] = [{
        "role": "system",
        "content": (
            "You draft concise, professional Outlook e-mails.\n"
            "Search ALL provided context for:\n"
            "  • recipient e-mail(s) (array)  – never invent! only if explicit\n"
            "  • a clear subject line\n"
            "  • a polite, well-structured body\n\n"
            "Return ONE JSON exactly:\n"
            '{"to":["a@b.com"],"subject":"...","body":"..."}\n'
            "If ANY field is still missing, return:\n"
            '{"missing":"recipients"}  (or subject / body)\n'
            "No extra keys or comments."
        ),
    }]
    _append(prompt, chat_mem)
    if semantic_mem:
        prompt.append({"role": "system", "content": "🔎 Relevant context:"})
        _append(prompt, semantic_mem)
    if global_mem:
        prompt.append({"role": "system", "content": "🌐 Other chats context:"})
        _append(prompt, global_mem)
    prompt.append({"role": "system",
                   "content": "Output strictly one JSON object as specified."})

    draft = json.loads(
        client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=prompt,
        ).choices[0].message.content
    )

    # --- Missing info branch ------------------------------------------
    if "missing" in draft:
        missing = draft["missing"]
        logging.info("email_agent needs %s", missing)
        # Ask via reply_agent right now
        process_reply(chat_id, last_user, missing_info=missing)
        return {"status": "missing", "missing": missing}

    # --- Validate ------------------------------------------------------
    for k in ("to", "subject", "body"):
        if k not in draft or not draft[k]:
            process_reply(chat_id, last_user, missing_info=k)
            return {"status": "missing", "missing": k}

    # --- Send e-mail ---------------------------------------------------
    _send_outlook(draft, access_token)
    logging.info("✓ Outlook e-mail sent: %s → %s",
                 draft["subject"], ", ".join(draft["to"]))

    # Persist assistant turn
    chat_type = next((r.get("chat_type") for r in chat_mem if r.get("chat_type")), None)
    log_txt   = f"✉️ E-mail sent: “{draft['subject']}” ➜ {', '.join(draft['to'])}"
    save_message(chat_id, "assistant", log_txt, chat_type or "unknown")

    return {"status": "sent"}

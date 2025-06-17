# ─────────────────────────────────────────────────────────────────────────────
# services/intent_api/email_agent.py
"""
Drafts and sends an Outlook e-mail.

Recipient rules
---------------
• If an address is *not* in the `contacts` table → ask the user.
• If an address looks fake (@example.com, placeholder, etc.) → ask the user.
• Otherwise send, and upsert/enrich the contact afterwards.
"""
from __future__ import annotations
import json, logging, re, requests
from typing import Dict, List, Tuple

from openai import OpenAI
from common.graph_auth import get_access_token
from common.memory_helpers import (
    fetch_chat_history, fetch_global_history, semantic_search, save_message
)
from services.intent_api.reply_agent import process_reply
from services.intent_api.contact_agent import (
    list_contacts, get_contact, upsert_contact
)
import os  

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
logging.getLogger(__name__).setLevel(logging.INFO)


# ───────── Graph / Teams helpers ─────────
def _graph(url: str, token: str, *, method: str = "GET",
           payload: Dict | None = None) -> Dict:
    r = requests.request(
        method, url,
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
        json=body, timeout=10
    ).raise_for_status()


def _send_outlook(message: Dict, token: str) -> None:
    payload = {
        "message": {
            "subject": message["subject"],
            "body": {"contentType": "Text", "content": message["body"]},
            "toRecipients": [
                {"emailAddress": {"address": addr}} for addr in message["to"]
            ],
        }
    }
    _graph("https://graph.microsoft.com/v1.0/me/sendMail",
           token, method="POST", payload=payload)


# ───────── internal helpers ─────────
_PLACEHOLDER = re.compile(r"(@example\.com$|placeholder|test@|foo@|bar@)", re.I)

def _explicit_emails(text: str) -> List[str]:
    return re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)


def _split(addrs: List[str]) -> Tuple[List[str], List[str]]:
    """Return (valid, missing) based on contacts + placeholder rules."""
    ok, missing = [], []
    for a in addrs:
        if _PLACEHOLDER.search(a) or not get_contact(email=a):
            missing.append(a)
        else:
            ok.append(a)
    return ok, missing


# ───────── main entry point ─────────
def process_email_request(chat_id: str) -> Dict[str, str]:
    token, _ = get_access_token()

    # Memory slices
    chat_mem   = fetch_chat_history(chat_id, 40)
    last_user  = next((r["content"] for r in reversed(chat_mem) if r["sender"] == "user"), "")
    glob_mem   = fetch_global_history(8)
    sem_mem    = semantic_search(last_user, chat_id, 8, 4)

    # Pre-save any explicit addresses in the last user turn
    for addr in _explicit_emails(last_user):
        upsert_contact(email=addr, conversation_id=chat_id)

    # Provide GPT with the list of known contacts
    known_list = list_contacts()
    contacts_block = "\n".join(f"- {c['email']} ({c.get('name') or 'no-name'})" for c in known_list) \
                     or "no contacts saved yet"

    # GPT prompt
    def _append(dst, rows):
        for r in rows:
            dst.append({
                "role": "user" if r["sender"] == "user" else "assistant",
                "content": r["content"],
            })

    msgs: List[Dict] = [{
        "role": "system",
        "content": (
            "You are EmailBot. Draft an Outlook e-mail only for recipients that "
            "already exist in the contact list below.\n"
            "Contact list:\n"
            f"{contacts_block}\n\n"
            "Respond **only** with JSON:\n"
            '{"to":["alice@example.com"],"subject":"…","body":"…"}\n'
            'If any field is missing respond {"missing":"recipients|subject|body"}.\n'
            "Never invent or guess an e-mail address."
        ),
    }]
    _append(msgs, chat_mem)
    if sem_mem:
        msgs += [{"role": "system", "content": "🔍 Relevant context:"}]
        _append(msgs, sem_mem)
    if glob_mem:
        msgs += [{"role": "system", "content": "🌐 Other chats:"}]
        _append(msgs, glob_mem)
    msgs.append({"role": "system", "content": "Reply with the JSON object only."})

    draft = json.loads(
        client.chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=msgs,
        ).choices[0].message.content
    )

    # Missing keys?
    if "missing" in draft:
        process_reply(chat_id, last_user, missing_info=draft["missing"])
        return {"status": "missing", "missing": draft["missing"]}
    for key in ("to", "subject", "body"):
        if key not in draft or not draft[key]:
            process_reply(chat_id, last_user, missing_info=key)
            return {"status": "missing", "missing": key}

    # Validate recipients
    valid, missing = _split(draft["to"])
    if missing:
        prompt = f"I don't have a valid e-mail for: {', '.join(missing)}. Please provide it."
        process_reply(chat_id, last_user,
                      missing_info="recipients",
                      custom_prompt=prompt)
        return {"status": "missing", "missing": "recipients"}

    # Send
    _send_outlook({**draft, "to": valid}, token)
    confirmation = f"✅ E-mail sent: “{draft['subject']}” ➜ {', '.join(valid)}"
    _teams_post(chat_id, confirmation, token)

    # Log
    chat_type = next((r.get("chat_type") for r in chat_mem if r.get("chat_type")), "unknown")
    save_message(chat_id, "assistant", confirmation, chat_type)
    logging.info("✓ Outlook mail sent: %s", draft["subject"])

    # Enrich contacts afterwards
    for addr in valid:
        upsert_contact(email=addr, conversation_id=chat_id)

    return {"status": "sent"}

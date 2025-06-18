# ─────────────────────────────────────────────────────────────────────────────
# services/intent_api/reply_agent.py
"""
Generate a Teams reply, manage contacts end-to-end, and persist everything.

If called with `missing_info` ("recipients" | "subject" | "body")
the reply skips GPT and immediately asks the user for that detail.

Contact CRUD (create / read / update / delete) is built-in:
    get_contact(), list_contacts(), create_contact(), update_contact(),
    delete_contact(), upsert_contact()

Table definition
----------------
contacts
    id               int8  (PK, auto)
    created_at       timestamptz
    email            text
    name             text
    role             text
    phone            text
    conversation_id  text
"""
from __future__ import annotations

import logging
import os
import requests
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from openai import OpenAI
from common.graph_auth import get_access_token
from common.memory_helpers import (
    save_message,
    fetch_chat_history,
    fetch_global_history,
    semantic_search,
)
from common.supabase import supabase  # configured client

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
logging.getLogger(__name__).setLevel(logging.INFO)

# ════════════════════════════════════════
# 1. Microsoft Graph helpers
# ════════════════════════════════════════
def _graph(
    url: str,
    token: str,
    *,
    method: str = "GET",
    payload: Dict[str, Any] | None = None,
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


def _teams_post(chat_id: str, text: str, token: str) -> int:
    body = {"body": {"contentType": "text", "content": text}}
    return requests.post(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=10,
    ).status_code


# ════════════════════════════════════════
# 2. Contact CRUD – embedded (no separate agent)
# ════════════════════════════════════════
_TBL = "contacts"


def _norm(email: str) -> str:
    return email.strip().lower()


def _row(resp) -> Optional[Dict[str, Any]]:
    return (resp.data or [None])[0]


def get_contact(
    *,
    id: int | None = None,
    email: str | None = None,
    conversation_id: str | None = None,
) -> Optional[Dict[str, Any]]:
    if id is not None:
        resp = supabase.table(_TBL).select("*").eq("id", id).limit(1).execute()
    elif email is not None:
        resp = (
            supabase.table(_TBL)
            .select("*")
            .ilike("email", _norm(email))
            .limit(1)
            .execute()
        )
    elif conversation_id is not None:
        resp = (
            supabase.table(_TBL)
            .select("*")
            .eq("conversation_id", conversation_id)
            .limit(1)
            .execute()
        )
    else:
        raise ValueError("get_contact() expects id, email, or conversation_id")
    return _row(resp)


def list_contacts() -> List[Dict[str, Any]]:
    cols = "email,name,role,phone"
    return supabase.table(_TBL).select(cols).execute().data or []


def create_contact(
    *,
    email: str,
    name: str | None = None,
    role: str | None = None,
    conversation_id: str | None = None,
    phone: str | None = None,
) -> Dict[str, Any]:
    row = {
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
        "email": _norm(email),
        "name": name,
        "role": role,
        "conversation_id": conversation_id,
        "phone": phone,
    }
    resp = supabase.table(_TBL).insert(row).execute()
    if getattr(resp, "error", None):
        raise RuntimeError(resp.error)
    logging.info("✓ contact created %s", email)
    return resp.data[0]


def update_contact(contact_id: int, **fields) -> Dict[str, Any]:
    if not fields:
        raise ValueError("update_contact(): nothing to update")
    resp = supabase.table(_TBL).update(fields).eq("id", contact_id).execute()
    if getattr(resp, "error", None):
        raise RuntimeError(resp.error)
    logging.info("✓ contact %s updated with %s", contact_id, fields)
    return resp.data[0]


def delete_contact(contact_id: int) -> None:
    supabase.table(_TBL).delete().eq("id", contact_id).execute()
    logging.info("✓ contact %s deleted", contact_id)


def upsert_contact(
    *,
    email: str,
    name: str | None = None,
    role: str | None = None,
    conversation_id: str | None = None,
    phone: str | None = None,
) -> Dict[str, Any]:
    existing = get_contact(email=email)
    if not existing:
        return create_contact(
            email=email,
            name=name,
            role=role,
            conversation_id=conversation_id,
            phone=phone,
        )

    patch: Dict[str, Any] = {}
    if name and not existing.get("name"):
        patch["name"] = name
    if role and not existing.get("role"):
        patch["role"] = role
    if phone and not existing.get("phone"):
        patch["phone"] = phone
    if conversation_id and not existing.get("conversation_id"):
        patch["conversation_id"] = conversation_id
    return update_contact(existing["id"], **patch) if patch else existing

    # ── 3 reply ───────────
def extract_json(text: str) -> dict:
    """Extract the last JSON block in a text (if any)."""
    try:
        start = text.rfind("{")
        return json.loads(text[start:]) if start != -1 else {}
    except Exception:
        return {}

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}")
def contains_email(text: str) -> bool:
    return bool(EMAIL_RE.search(text))


def process_reply(
    chat_id: str,
    last_user_text: str,
    missing_info: str | None = None,
    custom_prompt: str | None = None,
) -> None:
    access_token, _ = get_access_token()

    # ── 1. Ask for missing info ────────────────────────────────────────
    if missing_info:
        ask = (
            custom_prompt
            or {
                "recipients": "Peux-tu me donner l’adresse e-mail ?",
                "subject": "Quel sujet aimerais-tu ?",
                "body": "Que doit-on écrire dans le corps ?",
            }[missing_info]
        )
        _teams_post(chat_id, ask, access_token)
        save_message(chat_id, "assistant", ask, "unknown")
        logging.info("✓ prompt for %s sent", missing_info)
        return

    # ── 2. Context and memory ──────────────────────────────────────────
    chat_type = _graph(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}?$select=chatType",
        access_token,
    ).get("chatType", "unknown")

    chat_mem = fetch_chat_history(chat_id, 40)
    global_mem = fetch_global_history(8)
    semantic_mem = semantic_search(last_user_text, chat_id, 8, 4)

    def _add(dst: List[Dict[str, str]], rows):
        for r in rows:
            dst.append(
                {
                    "role": "user" if r["sender"] == "user" else "assistant",
                    "content": r["content"],
                }
            )

    # ── 3. Build GPT prompt ────────────────────────────────────────────
    msgs: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are an AI assistant who chats with users.\n"
                "When someone new is mentioned (e.g. a person or email not in the contact list), include a final JSON block like:\n"
                '{"action": "add_contact", "name": "John Smith", "email": "john@acme.com"}\n'
                "If the user asks to remove someone, use:\n"
                '{"action": "delete_contact", "email": "john@acme.com"}\n'
                "Only include the JSON at the end of the reply.\n"
                "Otherwise, just reply normally in human language."
            ),
        }
    ]

    _add(msgs, chat_mem)
    if semantic_mem:
        msgs.append({"role": "system", "content": "🔎 Relevant context:"})
        _add(msgs, semantic_mem)
    if global_mem:
        msgs.append({"role": "system", "content": "🌐 Other chats context:"})
        _add(msgs, global_mem)
    msgs.append({"role": "user", "content": last_user_text})

    # ── 4. GPT chat call ────────────────────────────────────────────────
    full_reply = (
        client.chat.completions.create(
            model="gpt-4o",
            messages=msgs,
        )
        .choices[0]
        .message.content.strip()
    )

    # ── 5. Extract JSON intent ──────────────────────────────────────────
    parsed = extract_json(full_reply)
    contact_action = parsed.get("action")
    reply = full_reply.split("{")[0].strip()  # clean reply (without json)

    if contact_action == "add_contact" and parsed.get("email"):
        upsert_contact(
            email=parsed["email"],
            name=parsed.get("name"),
            conversation_id=chat_id,
        )
        reply += f"\n✅ Contact {parsed.get('name') or parsed['email']} added."

    elif contact_action == "delete_contact" and parsed.get("email"):
        contact = get_contact(email=parsed["email"])
        if contact:
            delete_contact(contact["id"])
            reply += f"\n🗑️ Contact {parsed['email']} deleted."

    # ── 6. Post reply and save ─────────────────────────────────────────
    status = _teams_post(chat_id, reply, access_token)
    save_message(chat_id, "assistant", reply, chat_type)
    logging.info("✓ reply sent (%s)", status)

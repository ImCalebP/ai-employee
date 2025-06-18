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


# ════════════════════════════════════════
# 3. Main reply logic
# ════════════════════════════════════════
def process_reply(
    chat_id: str,
    last_user_text: str,
    missing_info: str | None = None,
    custom_prompt: str | None = None,
) -> None:
    """
    Handle one user turn:
        • Ask for missing e-mail / subject / body if needed
        • Else, generate an intelligent reply
    """
    access_token, _ = get_access_token()

    # ── 3.1 Ask explicitly for missing email / subject / body ───────────
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

    # ── 3.2 Gather memory context ───────────────────────────────────────
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

    # ── 3.3 Build prompt ────────────────────────────────────────────────
    msgs: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are a professional AI assistant responsible for replying intelligently and conversationally to user messages.\n"
                "You have immediate access to:\n"
                "• Message history (vector & global)\n"
                "• A Supabase `contacts` table that you can create, edit **or delete** entries in.\n"
                "🧠 Guidelines\n"
                "Contact handling:\n"
                "– When a person is mentioned, search the contacts table.\n"
                "– If essential info (e-mail or phone) is missing, politely ask for it.\n"
                "– If the user supplies new info, trust that upsert_contact() will run; simply confirm naturally.\n"
                "– If the user requests **deletion**, confirm and call delete_contact().\n"
                "Never invent data.\n\n"
                "Output rules:\n"
                "✅ Plain-text human reply only.\n"
                "❌ No JSON, markdown, or tool descriptions.\n"
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

    # ── 3.4 Call GPT-4o-mini to craft reply ─────────────────────────────
    reply = (
        client.chat.completions.create(
            model="gpt-4o",
            messages=msgs,
        )
        .choices[0]
        .message.content.strip()
    )

    # ── 3.5 Send back to Teams and save memory ──────────────────────────
    status = _teams_post(chat_id, reply, access_token)
    save_message(chat_id, "assistant", reply, chat_type)
    logging.info("✓ reply sent (%s)", status)

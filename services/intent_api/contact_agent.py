# ─────────────────────────────────────────────────────────────────────────────
# services/intent_api/contact_agent.py
"""
Ultra-light CRUD helper for the `contacts` table.

Columns
-------
id               int8  (PK, auto)
created_at       timestamptz
email            text
name             text           <-- NEW
role             text
conversation_id  text
phone            text
"""
from __future__ import annotations
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List

from common.supabase import supabase  # your configured client

logging.getLogger(__name__).setLevel(logging.INFO)
_TBL = "contacts"


# ───────── internal helpers ─────────
def _norm(email: str) -> str:
    return email.strip().lower()


def _row(resp) -> Optional[Dict[str, Any]]:
    return (resp.data or [None])[0]


# ───────── public API ─────────
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
        raise ValueError("get_contact() needs id, email, or conversation_id")
    return _row(resp)


def list_contacts() -> List[Dict[str, Any]]:
    """Return every stored contact (email, name, role, phone)."""
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

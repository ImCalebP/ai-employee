"""
R/W helper layer over the `message_history` table
─────────────────────────────────────────────────
• Stores every turn with an OpenAI embedding
• Tier-1 recall = last-N by chat_id  (+ small global slice)
• Tier-2 recall = pgvector similarity search (match_messages SQL function)
"""

from __future__ import annotations

import datetime as _dt
import logging as _log
import os
import uuid 
from openai import OpenAI
from common.supabase import supabase

# ─── OpenAI embedding setup ─────────────────────────────────────────────
_client       = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
_EMBED_MODEL  = "text-embedding-3-small"          # 1 k tokens · 1536 dims


# ─── pgvector helper ────────────────────────────────────────────────────
def _vector_literal(vec: list[float]) -> str:
    """
    Format a python list of floats as a pgvector literal **for pgvector ≥ 0.5**:
        '[0.1234567,-0.0012345,…]'
    We round to 7 dp – plenty for cosine similarity and keeps the row smaller.
    """
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


# ─── OpenAI embedding wrapper ───────────────────────────────────────────
def _embed(text: str) -> list[float]:
    """Truncate to 4 k chars (embed endpoint limit safeguard)."""
    resp = _client.embeddings.create(model=_EMBED_MODEL, input=text[:4000])
    return resp.data[0].embedding


# ─── public helpers ─────────────────────────────────────────────────────
def save_message(chat_id: str, sender: str, content: str, chat_type: str):
    row = {
        "id": str(uuid.uuid4()),
        "chat_id": chat_id,
        "sender": sender,
        "content": content,
        "chat_type": chat_type,   # ← new
        "timestamp": datetime.utcnow().isoformat(),
        # … embedding calc …
    }
    supabase.table("message_history").insert(row).execute()


    try:
        resp = supabase.table("message_history").insert(row).execute()
        # `resp` is a PostgrestResponse; on success it has `.data`, on error `.error`
        if getattr(resp, "error", None):
            _log.error("Supabase insert failed %s – payload=%s",
                       resp.error, row)
    except Exception as exc:                       # network or client error
        _log.exception("Supabase insert raised – %s – payload=%s", exc, row)


def fetch_chat_history(chat_id: str, limit: int = 15) -> list[dict]:
    resp = (
        supabase.table("message_history")
        .select("sender,content")
        .eq("chat_id", chat_id)
        .order("timestamp")          # ↑ ascending is default, so just drop asc=
        .limit(limit)
        .execute()
    )
    return resp.data or []


def fetch_global_history(limit: int = 5) -> list[dict]:
    resp = (
        supabase.table("message_history")
        .select("sender,content")
        .order("timestamp", desc=True)   # ← want newest first
        .limit(limit)
        .execute()
    )
    return list(reversed(resp.data or []))



def semantic_search(query: str, k: int = 5) -> list[dict]:
    """
    Tier-2 recall – call the `match_messages` RPC which performs
    pgvector cosine search on the embeddings column.
    """
    resp = supabase.rpc(
        "match_messages",
        {"query_embedding": _embed(query), "match_count": k},
    ).execute()
    return resp.data or []

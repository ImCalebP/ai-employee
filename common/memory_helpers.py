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

from openai import OpenAI
from common.supabase import supabase

# ─── OpenAI embedding setup ─────────────────────────────────────────────
_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
_EMBED_MODEL = "text-embedding-3-small"          # 1 k tokens · 1536 dims


# ─── pgvector helper ────────────────────────────────────────────────────
def _vector_literal(vec: list[float]) -> str:
    """
    Return a pgvector literal **for pgvector 0.5+**:
        '[1,2,3,…]'  (square brackets, comma-separated)
    We round to 7 dp – plenty for cosine similarity and keeps payload small.
    """
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


# ─── OpenAI embedding wrapper ───────────────────────────────────────────
def _embed(text: str) -> list[float]:
    """Truncate to 4 k chars (embedding endpoint limit safeguard)."""
    resp = _client.embeddings.create(model=_EMBED_MODEL, input=text[:4000])
    return resp.data[0].embedding


# ─── public helpers ─────────────────────────────────────────────────────
def save_message(chat_id: str, sender: str, content: str) -> None:
    """Insert one row with its vector.  Logs any non-2xx response."""
    row = {
        "chat_id":   chat_id,
        "sender":    sender,                       # "user" | "assistant"
        "content":   content,
        "timestamp": _dt.datetime.utcnow().isoformat(),
        "embedding": _vector_literal(_embed(content)),
    }

    resp = supabase.table("message_history").insert(row).execute()
    if getattr(resp, "status_code", 500) >= 300:
        _log.error("Supabase insert failed (%s) – payload=%s",
                   resp.status_code, row)


def fetch_chat_history(chat_id: str, limit: int = 10) -> list[dict]:
    """Last *limit* rows for this chat (oldest→newest)."""
    resp = (
        supabase.table("message_history")
        .select("sender,content")
        .eq("chat_id", chat_id)
        .order("timestamp", asc=True)
        .limit(limit)
        .execute()
    )
    return resp.data or []


def fetch_global_history(limit: int = 5) -> list[dict]:
    """Tiny global slice to give GPT wider context."""
    resp = (
        supabase.table("message_history")
        .select("sender,content")
        .order("timestamp", desc=True)
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

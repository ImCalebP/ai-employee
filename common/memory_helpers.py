"""
R/W helper layer over the `message_history` table
─────────────────────────────────────────────────
• Stores every turn with an OpenAI embedding
• Tier-1 recall  = last-N by chat_id  (+ small global slice)
• Tier-2 recall  = pgvector similarity search (match_messages SQL function)
"""

from __future__ import annotations

import datetime as _dt
import logging as _log
import os

from openai import OpenAI
from common.supabase import supabase  # → create_client(...) held here

# ─── OpenAI embedding setup ─────────────────────────────────────────────
_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
_EMBED_MODEL = "text-embedding-3-small"   # 1 k tokens, fast + cheap (1536 dims)

# ─── pgvector helper ────────────────────────────────────────────────────
def _vector_literal(vec: list[float]) -> str:
    """
    Convert a Python list → `(1,2,3, …)` literal understood by pgvector.
    We keep 7 dp which is more than enough precision for cosine similarity.
    """
    return "(" + ",".join(f"{x:.7f}" for x in vec) + ")"


# ─── embedding API ──────────────────────────────────────────────────────
def _embed(text: str) -> list[float]:
    """1-shot call – clips the prompt at 4 K characters for safety."""
    resp = _client.embeddings.create(model=_EMBED_MODEL, input=text[:4000])
    return resp.data[0].embedding


# ─── public helpers ─────────────────────────────────────────────────────
def save_message(chat_id: str, sender: str, content: str) -> None:
    """
    Insert a single row.  We **must** send pgvector as a string literal ­–
    NOT raw JSON – otherwise PostgREST tries to coerce it and 500s.
    """
    emb = _embed(content)
    row = {
        "chat_id":   chat_id,
        "sender":    sender,            # "user" | "assistant"
        "content":   content,
        "timestamp": _dt.datetime.utcnow().isoformat(),
        "embedding": _vector_literal(emb),
    }
    resp = supabase.table("message_history").insert(row).execute()
    if getattr(resp, "status_code", 500) >= 300:
        _log.error("Supabase insert failed (%s) – payload=%s",
                   resp.status_code, row)


def fetch_chat_history(chat_id: str, limit: int = 10) -> list[dict]:
    """Last N rows **for this chat**, oldest→newest (for replay into GPT)."""
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
    """A small global slice (helps with general ‘company memory’)."""
    resp = (
        supabase.table("message_history")
        .select("sender,content")
        .order("timestamp", desc=True)
        .limit(limit)
        .execute()
    )
    # Flip to chronological order so GPT sees oldest first.
    return list(reversed(resp.data or []))


def semantic_search(query: str, k: int = 5) -> list[dict]:
    """
    Tier-2 recall – search ALL messages via pgvector.
    Requires the `match_messages` function from the earlier SQL snippet.
    """
    q_emb = _embed(query)
    resp = (
        supabase.rpc(
            "match_messages",
            {"query_embedding": q_emb, "match_count": k},
        ).execute()
    )
    return resp.data or []

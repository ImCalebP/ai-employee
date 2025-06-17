# services/common/memory_helpers.py
"""
Read / write helper for the **message_history** table
────────────────────────────────────────────────────
• Saves every turn together with an OpenAI embedding (pgvector)
• Tier-1 recall  = last-N messages by chat_id  (+ small global slice)
• Tier-2 recall  = pgvector similarity search via `match_messages` RPC
"""

from __future__ import annotations

import logging as _log
import os
import uuid
from datetime import datetime
from typing import List, Dict, Any

from openai import OpenAI

from common.supabase import supabase

# ─────────────────────────  OpenAI Embeddings  ──────────────────────────
_CLIENT = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
_EMBED_MODEL = "text-embedding-3-small"           # 1 k tokens · 1536 dims


def _embed(text: str) -> List[float]:
    """
    Call the embeddings endpoint (truncate input at 4 k chars – hard limit).
    """
    resp = _CLIENT.embeddings.create(model=_EMBED_MODEL, input=text[:4000])
    return resp.data[0].embedding


# ─────────────────────────  pgvector literal  ───────────────────────────
def _vector_literal(vec: List[float]) -> str:
    """
    Format a list of floats as a pgvector literal (pgvector ≥ 0.5 syntax).

    Example →  '[0.1234567,-0.0012345,…]'
    We round to 7 dp – plenty for cosine similarity while keeping rows small.
    """
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


# ─────────────────────────  Public helpers  ─────────────────────────────
def save_message(
    chat_id: str,
    sender: str,
    content: str,
    chat_type: str | None = None,   # "oneOnOne" | "group" | None
) -> None:
    """
    Store a message in **message_history** with its embedding.

    Parameters
    ----------
    chat_id     Teams conversation / chat ID
    sender      "user" | "assistant"
    content     Raw text
    chat_type   "oneOnOne", "group", … (from Graph `chatType`) – optional
    """
    embedding = _embed(content)

    row: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "chat_id": chat_id,
        "sender": sender,
        "content": content,
        "chat_type": chat_type,
        "timestamp": datetime.utcnow().isoformat(),
        "embedding": _vector_literal(embedding),
    }

    try:
        resp = supabase.table("message_history").insert(row).execute()
        if getattr(resp, "error", None):
            _log.error("Supabase insert failed: %s · payload=%s", resp.error, row)
    except Exception as exc:  # network / client error
        _log.exception("Supabase insert raised: %s · payload=%s", exc, row)


def fetch_chat_history(chat_id: str, limit: int = 15) -> List[Dict]:
    """
    Last-N messages for a given chat, oldest → newest (ascending).
    """
    resp = (
        supabase.table("message_history")
        .select("sender,content")
        .eq("chat_id", chat_id)
        .order("timestamp", desc=False)
        .limit(limit)
        .execute()
    )
    return resp.data or []


def fetch_global_history(limit: int = 5) -> List[Dict]:
    """
    Thin global slice – newest first, then reversed to oldest → newest.
    """
    resp = (
        supabase.table("message_history")
        .select("sender,content")
        .order("timestamp", desc=True)
        .limit(limit)
        .execute()
    )
    return list(reversed(resp.data or []))


def semantic_search(query: str, k: int = 5) -> List[Dict]:
    """
    Tier-2 recall – cosine search in pgvector via `match_messages` RPC.
    """
    resp = supabase.rpc(
        "match_messages",
        {"query_embedding": _embed(query), "match_count": k},
    ).execute()
    return resp.data or []

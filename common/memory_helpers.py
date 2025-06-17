# services/common/memory_helpers.py
"""
Read / write helper for the **message_history** table
────────────────────────────────────────────────────
• Persists every turn together with an OpenAI embedding (pgvector)
• Tier-1 recall  = last-N messages per chat (+ a thin global slice)
• Tier-2 recall  = pgvector similarity search via `match_messages`
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
    Call the embeddings endpoint (truncate at 4 k chars – endpoint limit).
    """
    resp = _CLIENT.embeddings.create(model=_EMBED_MODEL, input=text[:4000])
    return resp.data[0].embedding


# ─────────────────────────  pgvector helper  ────────────────────────────
def _vector_literal(vec: List[float]) -> str:
    """
    Format a list of floats as a pgvector literal (pgvector ≥ 0.5 syntax):
    >>> _vector_literal([0.1, -0.2])
    '[0.1000000,-0.2000000]'
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
    Persist one message row with its embedding.

    Parameters
    ----------
    chat_id     Teams chat / conversation ID
    sender      "user" | "assistant"
    content     Message body (plain text)
    chat_type   Optional Graph `chatType` ("oneOnOne", "group", …)
    """
    row: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "chat_id": chat_id,
        "sender": sender,
        "content": content,
        "chat_type": chat_type,
        "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
        "embedding": _vector_literal(_embed(content)),
    }

    try:
        resp = supabase.table("message_history").insert(row).execute()
        if getattr(resp, "error", None):
            _log.error("Supabase insert failed: %s · payload=%s", resp.error, row)
    except Exception as exc:  # network / client error
        _log.exception("Supabase insert raised: %s · payload=%s", exc, row)


def fetch_chat_history(chat_id: str, limit: int = 15) -> List[Dict]:
    """
    Last-`limit` messages for this chat, oldest → newest.
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
    Global slice – newest first, then reversed (so GPT sees chronological order).
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
    Tier-2 recall – cosine search in pgvector via the `match_messages` RPC.
    """
    resp = supabase.rpc(
        "match_messages",
        {"query_embedding": _embed(query), "match_count": k},
    ).execute()
    return resp.data or []

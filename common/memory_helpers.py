"""
Read / write helpers for the **message_history** table
──────────────────────────────────────────────────────
• Persists every turn together with an OpenAI embedding (pgvector)
• Tier-1 recall  = last-N messages per chat  (+ small global slice)
• Tier-2 recall  = semantic search (first inside the chat, then global)
──────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging as _log
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List

from openai import OpenAI
from common.supabase import supabase

# ─────────────────────────  OpenAI Embeddings  ──────────────────────────
# text-embedding-3-large is much more accurate (3072 dims, 8 K context)
_CLIENT = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
_EMBED_MODEL = "text-embedding-3-large"
_EMBED_MAX_CHARS = 8192 * 4     # ≈8 K tokens → 4 chars / token

def _embed(text: str) -> List[float]:
    """
    Create an embedding for *at most* the first _EMBED_MAX_CHARS.
    """
    snippet = text[:_EMBED_MAX_CHARS]
    resp = _CLIENT.embeddings.create(model=_EMBED_MODEL, input=snippet)
    return resp.data[0].embedding

# ─────────────────────────  pgvector helper  ────────────────────────────
def _vector_literal(vec: List[float]) -> str:
    """
    Render a python list as a pgvector literal. (pgvector ≥ 0.5 syntax)
    >>> _vector_literal([0.1, -0.2]) -> '[0.1000000,-0.2000000]'
    """
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"

# ─────────────────────────  Public helpers  ─────────────────────────────
def save_message(
    chat_id: str,
    sender: str,
    content: str,
    chat_type: str | None = None,  # "oneOnOne" | "group" | None
) -> None:
    """
    Persist one message row with its embedding.
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

# -------------  Tier-1: chronological slices  --------------------------
def fetch_chat_history(chat_id: str, limit: int = 30) -> List[Dict]:
    """
    Last-`limit` messages for *this* chat, oldest → newest.
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

def fetch_global_history(limit: int = 8) -> List[Dict]:
    """
    Global slice – newest first, then reversed for chronological order.
    Only assistant messages are skipped (they’re usually generic).
    """
    resp = (
        supabase.table("message_history")
        .select("sender,content")
        .neq("sender", "assistant")
        .order("timestamp", desc=True)
        .limit(limit)
        .execute()
    )
    return list(reversed(resp.data or []))

# -------------  Tier-2: semantic search  -------------------------------
#
# Two small Postgres RPC helpers are expected (SQL below):
#
#   match_messages_in_chat(chat_id text, query_embedding vector, match_count int)
#   match_messages_global(query_embedding vector, match_count int)
#
# They both return rows with (sender, content) ordered by cosine distance.
#
# A template for each function is included underneath the python code.
# ----------------------------------------------------------------------

def semantic_search(query: str, chat_id: str, k_chat: int = 8, k_global: int = 4) -> List[Dict]:
    """
    Hybrid search:
      1. look *inside this chat*       → up to `k_chat` results
      2. if < k_chat hits, search all  → up to `k_global` extra rows
    Returns a *deduplicated* list (oldest → newest for coherence).
    """
    q_emb = _embed(query)

    # --- in-chat --------------------------------------------------------
    in_chat = (
        supabase.rpc(
            "match_messages_in_chat",
            {"chat_id": chat_id, "query_embedding": q_emb, "match_count": k_chat},
        )
        .execute()
        .data
        or []
    )

    # --- global fallback ------------------------------------------------
    needed = max(0, k_chat - len(in_chat))
    global_hits: List[Dict] = []
    if needed > 0 and k_global > 0:
        global_hits = (
            supabase.rpc(
                "match_messages_global",
                {"query_embedding": q_emb, "match_count": min(k_global, needed)},
            )
            .execute()
            .data
            or []
        )

    # --- oldest → newest & dedup ---------------------------------------
    seen: set[str] = set()
    ordered: List[Dict] = []
    for row in reversed(in_chat + global_hits):  # newest last
        key = row["sender"] + row["content"]
        if key not in seen:
            seen.add(key)
            ordered.append(row)
    return ordered

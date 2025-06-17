"""
Unified memory layer for all agents
───────────────────────────────────
* Persists every turn with pgvector embeddings
* Tier-1  : last-N chronological messages per chat
* Tier-2  : semantic search in-chat, then global
"""

from __future__ import annotations
import os, uuid, logging as _log
from datetime import datetime as _dt
from typing import Any, Dict, List

from supabase import create_client, Client           # pip install supabase
from openai import OpenAI                            # pip install openai

# ──────────────────── Supabase & OpenAI clients ────────────────────────
_SUPA_URL   = os.getenv("SUPABASE_URL")
_SUPA_KEY   = os.getenv("SUPABASE_SERVICE_KEY")
_openai     = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
_supabase: Client = create_client(_SUPA_URL, _SUPA_KEY)

_EMBED_MODEL      = "text-embedding-3-large"
_EMBED_MAX_CHARS  = 8192 * 4                   # ≈ 8 K tokens × 4 chars


# ╭───────────────────────────  MemoryHelper  ───────────────────────────╮
class MemoryHelper:
    """Thread-safe helper shared by *all* agents."""

    # ——— write ————————————————————————————————————————————————
    @staticmethod
    def save(chat_id: str, role: str, content: str,
             chat_type: str | None = None) -> None:
        row: Dict[str, Any] = {
            "id":        str(uuid.uuid4()),
            "chat_id":   chat_id,
            "sender":    role,
            "content":   content,
            "chat_type": chat_type,
            "timestamp": _dt.utcnow().isoformat(timespec="seconds"),
            "embedding": MemoryHelper._vec_literal(MemoryHelper._embed(content)),
        }
        try:
            _supabase.table("message_history").insert(row).execute()
        except Exception as exc:
            _log.exception("Supabase insert failed: %s · payload=%s", exc, row)

    # ——— tier-1 recall ————————————————————————————
    @staticmethod
    def last_messages(chat_id: str, limit: int = 30) -> List[Dict]:
        res = (_supabase.table("message_history")
               .select("sender,content")
               .eq("chat_id", chat_id)
               .order("timestamp", desc=False)
               .limit(limit)
               .execute())
        return res.data or []

    @staticmethod
    def global_slice(limit: int = 8) -> List[Dict]:
        res = (_supabase.table("message_history")
               .select("sender,content")
               .neq("sender", "assistant")      # skip bot chatter
               .order("timestamp", desc=True)
               .limit(limit)
               .execute())
        return list(reversed(res.data or []))

    # ——— tier-2 semantic ————————————————————————————
    @staticmethod
    def semantic(query: str, chat_id: str,
                 k_chat: int = 8, k_global: int = 4) -> List[Dict]:
        emb = MemoryHelper._embed(query)

        in_chat = (_supabase.rpc("match_messages_in_chat",
                                 {"chat_id": chat_id,
                                  "query_embedding": emb,
                                  "match_count": k_chat})
                   .execute()
                   .data or [])

        needed = k_chat - len(in_chat)
        global_hits = []
        if needed > 0 and k_global:
            global_hits = (_supabase.rpc("match_messages_global",
                                         {"query_embedding": emb,
                                          "match_count": min(k_global, needed)})
                           .execute()
                           .data or [])

        seen, ordered = set(), []
        for row in reversed(in_chat + global_hits):
            key = row["sender"] + row["content"]
            if key not in seen:
                seen.add(key)
                ordered.append(row)
        return ordered

    # ——— private helpers ————————————————————————————
    @staticmethod
    def _embed(txt: str) -> List[float]:
        snippet = txt[:_EMBED_MAX_CHARS]
        return (_openai.embeddings
                .create(model=_EMBED_MODEL, input=snippet)
                .data[0]
                .embedding)

    @staticmethod
    def _vec_literal(vec: List[float]) -> str:
        return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"
# ╰──────────────────────────────────────────────────────────────────────╯

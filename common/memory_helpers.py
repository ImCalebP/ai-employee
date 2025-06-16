# common/memory_helpers.py
from common.supabase import supabase
from openai import OpenAI
import os, logging, typing as t

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
EMBED_MODEL = "text-embedding-3-small"

def _embed(txt: str) -> list[float]:
    return client.embeddings.create(
        model=EMBED_MODEL,
        input=(txt or " ")[:4000],
    ).data[0].embedding


# ---------------------------------------------------------------------- #
def _http_code(resp: t.Any) -> int:
    """
    Try to pull an HTTP-like status from *any* Supabase response object.
    Works for PostgrestResponse (.status_code)   – insert/select
            APIResponse        (.status)        – rpc()
    """
    return (
        getattr(resp, "status_code", None)       # PostgrestResponse
        or getattr(resp, "status", None)         # APIResponse
        or 500
    )


def save_message(chat: str, sender: str, content: str) -> None:
    """
    Inserts one row with embedding. Never raises – only logs on failure.
    """
    vec = _embed(content)

    resp = (
        supabase.table("message_history")
        .insert(
            {
                "chat_id":   chat,
                "sender":    sender,
                "content":   content,
                "embedding": vec,
            }
        )
        .execute()
    )

    if _http_code(resp) >= 300:
        logging.error("Supabase insert failed (%s) – %s", _http_code(resp), resp.data)


def fetch_chat_history(chat: str, limit: int = 10):
    resp = (
        supabase.table("message_history")
        .select("sender,content")
        .eq("chat_id", chat)
        .order("timestamp", desc=False)
        .limit(limit)
        .execute()
    )
    return resp.data or []


def fetch_global_history(limit: int = 5):
    resp = (
        supabase.table("message_history")
        .select("sender,content")
        .order("timestamp", desc=True)
        .limit(limit)
        .execute()
    )
    out = resp.data or []
    out.reverse()          # old→new order
    return out


def semantic_search(query: str, k: int = 5):
    vec = _embed(query)
    resp = supabase.rpc(
        "match_messages",
        {"query_embedding": vec, "match_count": k},
    ).execute()
    return resp.data or []

# common/memory_helpers.py
from common.supabase import supabase
from openai import OpenAI
import os, logging

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM   = 1536


def embed(txt: str) -> list[float]:
    txt = txt.strip()[:4000] or " "
    return client.embeddings.create(model=EMBED_MODEL, input=txt).data[0].embedding


# ────────────────────────────────────────────────────────────────────────
def _ok(resp) -> bool:                      # helper for v2 client
    return getattr(resp, "status_code", 500) < 300


def save_message(chat_id: str, sender: str, content: str) -> None:
    """
    Insert a row + embedding.  `sender` = "user" | "assistant".
    """
    vec = embed(content)

    resp = (
        supabase.table("message_history")
        .insert(
            {
                "chat_id":   chat_id,
                "sender":    sender,
                "content":   content,
                "embedding": vec,
            }
        )
        .execute()
    )

    if not _ok(resp):
        logging.error("Supabase insert failed %s – payload=%s", resp.status_code, resp.data)


def fetch_chat_history(chat_id: str, limit: int = 10):
    resp = (
        supabase.table("message_history")
        .select("sender,content")
        .eq("chat_id", chat_id)
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
    data = resp.data or []
    data.reverse()
    return data


def semantic_search(query: str, k: int = 5):
    vec  = embed(query)
    resp = supabase.rpc("match_messages",
                        {"query_embedding": vec, "match_count": k}).execute()
    return resp.data or []

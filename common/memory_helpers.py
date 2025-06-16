from common.supabase import supabase
from openai import OpenAI
import os, logging

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

EMBED_MODEL = "text-embedding-3-small"   # cheap + 1536-dim
EMBED_DIM   = 1536

# ───────────────────────── embeddings ───────────────────────────────────
def embed(text: str) -> list[float]:
    text = text.strip()[:4000] or " "     # empty → OpenAI error
    out  = client.embeddings.create(model=EMBED_MODEL, input=text)
    return out.data[0].embedding

# ───────────────────────── DB helpers ───────────────────────────────────
def save_message(chat_id: str, sender: str, content: str) -> None:
    """
    Insert one row with fresh embedding.
    `sender` must be "user" or "assistant".
    """
    vector = embed(content)
    res = (
        supabase.table("message_history")
        .insert(
            {
                "chat_id": chat_id,
                "sender":  sender,
                "content": content,
                "embedding": vector,
            }
        )
        .execute()
    )
    if res.error:
        logging.error("Supabase insert error: %s", res.error)


def fetch_chat_history(chat_id: str, limit: int = 10):
    rows = (
        supabase.table("message_history")
        .select("sender,content")
        .eq("chat_id", chat_id)
        .order("timestamp", desc=False)
        .limit(limit)
        .execute()
    )
    return rows.data or []


def fetch_global_history(limit: int = 5):
    rows = (
        supabase.table("message_history")
        .select("sender,content")
        .order("timestamp", desc=True)
        .limit(limit)
        .execute()
    )
    data = rows.data or []
    data.reverse()
    return data


def semantic_search(query: str, k: int = 5):
    vec = embed(query)
    rows = (
        supabase.rpc("match_messages", {"query_embedding": vec, "match_count": k})
        .execute()
    )
    return rows.data or []

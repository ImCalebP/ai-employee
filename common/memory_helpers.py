from common.supabase import supabase


def save_message(chat_id: str, sender: str, content: str) -> None:
    """Insert one row into message_history."""
    supabase.table("message_history").insert(
        {"chat_id": chat_id, "sender": sender, "content": content}
    ).execute()


def fetch_chat_history(chat_id: str, limit: int = 10):
    """
    Return last `limit` messages for THIS chat (oldest → newest).
    Shape: [{sender, content}, …]
    """
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
    """
    Return last `limit` messages across ALL chats (oldest → newest).
    Used for global/company-wide context.
    """
    rows = (
        supabase.table("message_history")
        .select("sender,content")
        .order("timestamp", desc=True)
        .limit(limit)
        .execute()
    )
    data = rows.data or []
    data.reverse()  # newest→oldest ➜ oldest→newest
    return data

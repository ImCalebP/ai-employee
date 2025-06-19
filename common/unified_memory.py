"""
Unified memory/search helpers for cross-domain entity resolution:
- Contacts (by name, email, role)
- Documents (by title, content, meeting association)
- Tasks (by description, assignee, status)
- Entity linking (e.g., which contact is mentioned in which message)
"""

from typing import List, Dict, Optional, Any
from common.supabase import supabase

# --- Contacts ---
def search_contacts(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Search contacts by name or email (case-insensitive, partial match).
    """
    resp = (
        supabase.table("contacts")
        .select("*")
        .ilike("name", f"%{query}%")
        .limit(limit)
        .execute()
    )
    results = resp.data or []
    if len(results) < limit:
        # Try email if not enough results
        resp2 = (
            supabase.table("contacts")
            .select("*")
            .ilike("email", f"%{query}%")
            .limit(limit - len(results))
            .execute()
        )
        results += resp2.data or []
    return results[:limit]

def get_contact_by_email(email: str) -> Optional[Dict[str, Any]]:
    resp = (
        supabase.table("contacts")
        .select("*")
        .ilike("email", email.strip().lower())
        .limit(1)
        .execute()
    )
    return (resp.data or [None])[0]

# --- Documents ---
def search_documents(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Search documents by title or content (case-insensitive, partial match).
    Assumes a 'documents' table with 'title' and 'content' fields.
    """
    resp = (
        supabase.table("documents")
        .select("*")
        .ilike("title", f"%{query}%")
        .limit(limit)
        .execute()
    )
    results = resp.data or []
    if len(results) < limit:
        # Try content if not enough results
        resp2 = (
            supabase.table("documents")
            .select("*")
            .ilike("content", f"%{query}%")
            .limit(limit - len(results))
            .execute()
        )
        results += resp2.data or []
    return results[:limit]

def get_document_by_id(doc_id: str) -> Optional[Dict[str, Any]]:
    resp = (
        supabase.table("documents")
        .select("*")
        .eq("id", doc_id)
        .limit(1)
        .execute()
    )
    return (resp.data or [None])[0]

# --- Tasks ---
def search_tasks(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Search tasks by description or assignee (case-insensitive, partial match).
    Assumes a 'tasks' table with 'description' and 'assignee' fields.
    """
    resp = (
        supabase.table("tasks")
        .select("*")
        .ilike("description", f"%{query}%")
        .limit(limit)
        .execute()
    )
    results = resp.data or []
    if len(results) < limit:
        # Try assignee if not enough results
        resp2 = (
            supabase.table("tasks")
            .select("*")
            .ilike("assignee", f"%{query}%")
            .limit(limit - len(results))
            .execute()
        )
        results += resp2.data or []
    return results[:limit]

def get_task_by_id(task_id: str) -> Optional[Dict[str, Any]]:
    resp = (
        supabase.table("tasks")
        .select("*")
        .eq("id", task_id)
        .limit(1)
        .execute()
    )
    return (resp.data or [None])[0]

# --- Entity linking (example stub) ---
def get_documents_for_meeting(meeting_id: str) -> List[Dict[str, Any]]:
    """
    Return all documents linked to a given meeting.
    Assumes a 'documents' table with 'meeting_id' field.
    """
    resp = (
        supabase.table("documents")
        .select("*")
        .eq("meeting_id", meeting_id)
        .execute()
    )
    return resp.data or []

def get_contacts_mentioned_in_message(message_id: str) -> List[Dict[str, Any]]:
    """
    Return all contacts mentioned in a given message.
    Assumes a 'message_mentions' table linking messages and contacts.
    """
    resp = (
        supabase.table("message_mentions")
        .select("contact_id")
        .eq("message_id", message_id)
        .execute()
    )
    contact_ids = [row["contact_id"] for row in (resp.data or [])]
    if not contact_ids:
        return []
    resp2 = (
        supabase.table("contacts")
        .select("*")
        .in_("id", contact_ids)
        .execute()
    )
    return resp2.data or []

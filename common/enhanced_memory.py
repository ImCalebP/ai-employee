"""
Enhanced Memory System with Semantic Search
- Cross-domain semantic search (messages, documents, tasks)
- Document intelligence and context awareness
- Proactive content linking
- Vector embeddings for all entities
"""

from __future__ import annotations
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI
from common.supabase import supabase

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
logging.getLogger(__name__).setLevel(logging.INFO)

# Using text-embedding-ada-002 (1536 dimensions) as specified
EMBED_MODEL = "text-embedding-ada-002"
EMBED_DIMENSIONS = 1536


def _embed(text: str) -> List[float]:
    """Create embedding for text using OpenAI."""
    response = client.embeddings.create(
        model=EMBED_MODEL,
        input=text[:8000]  # Truncate to avoid token limits
    )
    return response.data[0].embedding


def _vector_literal(vec: List[float]) -> str:
    """Convert vector to pgvector literal format."""
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


# ═══════════════════════════════════════════════════════════════════════════════
# Document Intelligence
# ═══════════════════════════════════════════════════════════════════════════════

def save_document_with_embedding(
    title: str,
    content: str,
    doc_type: str,
    file_path: Optional[str] = None,
    chat_id: Optional[str] = None,
    meeting_id: Optional[str] = None,
    author: Optional[str] = None,
    metadata: Optional[Dict] = None
) -> Dict[str, Any]:
    """Save document with semantic embedding."""
    
    # Create embedding from title + content
    embed_text = f"{title}\n\n{content}"
    embedding = _embed(embed_text)
    
    doc_record = {
        "title": title,
        "content": content,
        "type": doc_type,
        "file_path": file_path,
        "chat_id": chat_id,
        "meeting_id": meeting_id,
        "author": author,
        "metadata": metadata or {},
        "embedding": _vector_literal(embedding),
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }
    
    resp = supabase.table("documents").insert(doc_record).execute()
    if getattr(resp, "error", None):
        raise RuntimeError(f"Failed to save document: {resp.error}")
    
    document = resp.data[0]
    logging.info(f"✓ Document saved with embedding: {title}")
    return document


def search_documents_semantic(
    query: str,
    limit: int = 5,
    similarity_threshold: float = 0.7,
    doc_type: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Semantic search across documents."""
    
    query_embedding = _embed(query)
    
    # Call Supabase RPC function
    params = {
        "query_embedding": query_embedding,
        "match_count": limit,
        "similarity_threshold": similarity_threshold
    }
    
    if doc_type:
        params["doc_type_filter"] = doc_type
    
    resp = supabase.rpc("search_documents_semantic", params).execute()
    return resp.data or []


def find_relevant_documents_for_message(
    message: str,
    chat_id: str,
    limit: int = 3
) -> List[Dict[str, Any]]:
    """Find documents relevant to a message for proactive context."""
    
    # Search for relevant documents
    relevant_docs = search_documents_semantic(message, limit=limit, similarity_threshold=0.6)
    
    # Link message to documents if highly relevant
    for doc in relevant_docs:
        if doc.get("similarity", 0) > 0.8:  # High relevance threshold
            link_message_to_document(chat_id, doc["id"], doc["similarity"])
    
    return relevant_docs


def link_message_to_document(
    chat_id: str,
    document_id: str,
    relevance_score: float,
    message_id: Optional[str] = None
) -> None:
    """Link a message to a relevant document."""
    
    link_record = {
        "document_id": document_id,
        "message_id": message_id,
        "chat_id": chat_id,
        "relevance_score": relevance_score,
        "created_at": datetime.utcnow().isoformat()
    }
    
    try:
        supabase.table("document_mentions").insert(link_record).execute()
        logging.info(f"✓ Linked message to document {document_id} (score: {relevance_score:.2f})")
    except Exception as e:
        logging.warning(f"Failed to link message to document: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# Task Intelligence
# ═══════════════════════════════════════════════════════════════════════════════

def save_task_with_embedding(
    description: str,
    assignee: Optional[str] = None,
    assignee_email: Optional[str] = None,
    due_date: Optional[str] = None,
    priority: str = "medium",
    status: str = "pending",
    chat_id: Optional[str] = None,
    project_id: Optional[str] = None,
    metadata: Optional[Dict] = None
) -> Dict[str, Any]:
    """Save task with semantic embedding."""
    
    # Create embedding from description + context
    embed_text = f"{description}"
    if assignee:
        embed_text += f" assigned to {assignee}"
    if priority != "medium":
        embed_text += f" priority {priority}"
    
    embedding = _embed(embed_text)
    
    task_record = {
        "description": description,
        "assignee": assignee,
        "assignee_email": assignee_email,
        "due_date": due_date,
        "priority": priority,
        "status": status,
        "chat_id": chat_id,
        "project_id": project_id,
        "metadata": metadata or {},
        "embedding": _vector_literal(embedding),
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }
    
    resp = supabase.table("tasks").insert(task_record).execute()
    if getattr(resp, "error", None):
        raise RuntimeError(f"Failed to save task: {resp.error}")
    
    task = resp.data[0]
    logging.info(f"✓ Task saved with embedding: {description[:50]}...")
    return task


def search_tasks_semantic(
    query: str,
    limit: int = 5,
    similarity_threshold: float = 0.7,
    status_filter: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Semantic search across tasks."""
    
    query_embedding = _embed(query)
    
    params = {
        "query_embedding": query_embedding,
        "match_count": limit,
        "similarity_threshold": similarity_threshold
    }
    
    if status_filter:
        params["status_filter"] = status_filter
    
    resp = supabase.rpc("search_tasks_semantic", params).execute()
    return resp.data or []


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-Domain Intelligence
# ═══════════════════════════════════════════════════════════════════════════════

def get_contextual_intelligence(
    query: str,
    chat_id: str,
    include_documents: bool = True,
    include_tasks: bool = True,
    include_messages: bool = True
) -> Dict[str, List[Dict]]:
    """Get comprehensive context across all domains."""
    
    context = {
        "documents": [],
        "tasks": [],
        "messages": [],
        "summary": ""
    }
    
    if include_documents:
        context["documents"] = search_documents_semantic(query, limit=3)
    
    if include_tasks:
        context["tasks"] = search_tasks_semantic(query, limit=3)
    
    if include_messages:
        from common.memory_helpers import semantic_search
        context["messages"] = semantic_search(query, chat_id, 5, 3)
    
    # Generate contextual summary
    if any(context[key] for key in ["documents", "tasks", "messages"]):
        context["summary"] = generate_context_summary(context)
    
    return context


def generate_context_summary(context: Dict[str, List[Dict]]) -> str:
    """Generate a summary of relevant context."""
    
    summary_parts = []
    
    if context["documents"]:
        doc_titles = [doc.get("title", "Untitled") for doc in context["documents"][:2]]
        summary_parts.append(f"Relevant documents: {', '.join(doc_titles)}")
    
    if context["tasks"]:
        task_count = len(context["tasks"])
        summary_parts.append(f"{task_count} related task(s)")
    
    if context["messages"]:
        summary_parts.append("Previous conversation context available")
    
    return "; ".join(summary_parts) if summary_parts else "No specific context found"


def update_document_embedding(document_id: str, new_content: str) -> None:
    """Update document embedding when content changes."""
    
    # Get current document
    resp = supabase.table("documents").select("title").eq("id", document_id).execute()
    if not resp.data:
        return
    
    title = resp.data[0]["title"]
    embed_text = f"{title}\n\n{new_content}"
    embedding = _embed(embed_text)
    
    # Update document
    supabase.table("documents").update({
        "content": new_content,
        "embedding": _vector_literal(embedding),
        "updated_at": datetime.utcnow().isoformat()
    }).eq("id", document_id).execute()
    
    logging.info(f"✓ Updated document embedding: {document_id}")


def get_document_context_for_conversation(chat_id: str) -> List[Dict[str, Any]]:
    """Get documents that have been mentioned in this conversation."""
    
    resp = supabase.table("document_mentions").select("""
        document_id,
        relevance_score,
        documents!inner(title, type, content)
    """).eq("chat_id", chat_id).order("relevance_score", desc=True).limit(5).execute()
    
    return resp.data or []


# ═══════════════════════════════════════════════════════════════════════════════
# Contact Intelligence
# ═══════════════════════════════════════════════════════════════════════════════

def search_contacts_by_name(name: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Search contacts by name with fuzzy matching."""
    
    # Search in contacts table
    resp = supabase.table("contacts").select("*").ilike("name", f"%{name}%").limit(limit).execute()
    contacts = resp.data or []
    
    # Also search by email if name contains @ symbol
    if "@" in name:
        email_resp = supabase.table("contacts").select("*").ilike("email", f"%{name}%").limit(limit).execute()
        contacts.extend(email_resp.data or [])
    
    # Remove duplicates
    seen_ids = set()
    unique_contacts = []
    for contact in contacts:
        if contact["id"] not in seen_ids:
            seen_ids.add(contact["id"])
            unique_contacts.append(contact)
    
    return unique_contacts


def get_contact_conversations(contact_email: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Get recent conversations involving a specific contact."""
    
    # Search message history for messages from this contact
    resp = supabase.table("message_history").select("*").eq("sender", contact_email).order("timestamp", desc=True).limit(limit).execute()
    return resp.data or []


def extract_contact_names_from_text(text: str) -> List[str]:
    """Extract potential contact names from text using common patterns."""
    import re
    
    # Common patterns for names in conversation
    patterns = [
        r'\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b',  # First Last
        r'\b([A-Z][a-z]+)\b(?=\s+(?:said|told|mentioned|asked|replied|wrote|emailed))',  # Name before action verbs
        r'(?:from|to|with|about)\s+([A-Z][a-z]+)\b',  # Preposition + Name
        r'(?:email|call|message|contact)\s+([A-Z][a-z]+)\b',  # Action + Name
        r'\b([A-Z][a-z]+)(?:\'s|s)\s+(?:email|phone|number)',  # Possessive + contact info
    ]
    
    potential_names = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            if isinstance(match, tuple):
                # Full name match
                potential_names.append(" ".join(match))
            else:
                # Single name match
                potential_names.append(match)
    
    # Filter out common words that aren't names
    common_words = {"The", "This", "That", "What", "When", "Where", "How", "Why", "Can", "Will", "Should", "Would", "Could"}
    return [name for name in potential_names if name not in common_words]


def analyze_contact_context(
    message: str,
    chat_id: str
) -> Dict[str, Any]:
    """Analyze message for contact-related context and information needs."""
    
    context = {
        "mentioned_contacts": [],
        "contact_queries": [],
        "conversation_history": [],
        "suggested_actions": []
    }
    
    # Extract potential contact names
    potential_names = extract_contact_names_from_text(message)
    
    # Search for each potential contact
    for name in potential_names:
        contacts = search_contacts_by_name(name, limit=3)
        if contacts:
            for contact in contacts:
                context["mentioned_contacts"].append({
                    "name": contact["name"],
                    "email": contact["email"],
                    "phone": contact.get("phone"),
                    "role": contact.get("role"),
                    "confidence": 0.8  # High confidence for exact matches
                })
                
                # Get conversation history with this contact
                conversations = get_contact_conversations(contact["email"], limit=5)
                if conversations:
                    context["conversation_history"].extend(conversations)
    
    # Detect contact information queries
    query_patterns = [
        (r"what.+(?:email|address).+([A-Z][a-z]+)", "email"),
        (r"([A-Z][a-z]+).+(?:email|address)", "email"),
        (r"what.+(?:phone|number).+([A-Z][a-z]+)", "phone"),
        (r"([A-Z][a-z]+).+(?:phone|number)", "phone"),
        (r"how to contact ([A-Z][a-z]+)", "contact_info"),
        (r"([A-Z][a-z]+).+contact", "contact_info"),
    ]
    
    for pattern, info_type in query_patterns:
        matches = re.findall(pattern, message, re.IGNORECASE)
        for match in matches:
            context["contact_queries"].append({
                "name": match,
                "info_type": info_type,
                "query": message
            })
    
    # Generate suggested actions
    if context["contact_queries"]:
        context["suggested_actions"].append("retrieve_contact_info")
    
    if context["mentioned_contacts"]:
        context["suggested_actions"].append("provide_contact_context")
    
    return context


# ═══════════════════════════════════════════════════════════════════════════════
# Proactive Intelligence
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_message_for_proactive_actions(
    message: str,
    chat_id: str,
    sender: str
) -> Dict[str, Any]:
    """Analyze message for proactive opportunities."""
    
    actions = {
        "document_references": [],
        "task_implications": [],
        "follow_up_suggestions": [],
        "context_alerts": []
    }
    
    # Find relevant documents
    relevant_docs = find_relevant_documents_for_message(message, chat_id)
    if relevant_docs:
        actions["document_references"] = relevant_docs
        actions["context_alerts"].append(
            f"Found {len(relevant_docs)} relevant document(s) for this discussion"
        )
    
    # Check for task-related language
    task_keywords = ["i'll", "i will", "todo", "task", "deadline", "by friday", "tomorrow"]
    if any(keyword in message.lower() for keyword in task_keywords):
        actions["task_implications"].append({
            "type": "potential_task",
            "message": message,
            "sender": sender
        })
    
    # Check for follow-up opportunities
    question_keywords = ["what about", "how about", "should we", "can you"]
    if any(keyword in message.lower() for keyword in question_keywords):
        actions["follow_up_suggestions"].append({
            "type": "follow_up_opportunity",
            "message": message
        })
    
    return actions

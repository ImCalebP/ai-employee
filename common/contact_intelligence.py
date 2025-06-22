"""
Enhanced Contact Intelligence System - Fixed Version
- Proactive contact recognition and information retrieval
- Natural language understanding for contact queries
- Automatic contact enrichment and relationship tracking
- Robust fallback mechanisms for search
"""

from __future__ import annotations
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI
from common.supabase import supabase

client = OpenAI()
logging.getLogger(__name__).setLevel(logging.INFO)

# ═══════════════════════════════════════════════════════════════════════════════
# Core Contact Functions with Robust Fallbacks
# ═══════════════════════════════════════════════════════════════════════════════

def search_contacts_smart(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Smart contact search with multiple fallback strategies.
    """
    query = query.strip()
    if not query:
        return []
    
    # Strategy 1: Try the enhanced database function
    try:
        resp = supabase.rpc("search_contacts_enhanced", {
            "search_query": query,
            "limit_count": limit
        }).execute()
        
        contacts = resp.data or []
        if contacts:
            logging.info(f"Found {len(contacts)} contacts using enhanced search for '{query}'")
            return contacts
    except Exception as e:
        logging.debug(f"Enhanced search not available: {e}")
    
    # Strategy 2: Direct database search with multiple field matching
    try:
        # Build a comprehensive search query
        search_pattern = f"%{query}%"
        
        # Search across multiple fields
        resp = supabase.table("contacts").select("*").or_(
            f"name.ilike.{search_pattern},"
            f"email.ilike.{search_pattern},"
            f"first_name.ilike.{search_pattern},"
            f"last_name.ilike.{search_pattern},"
            f"display_name.ilike.{search_pattern},"
            f"role.ilike.{search_pattern},"
            f"company.ilike.{search_pattern}"
        ).limit(limit).execute()
        
        contacts = resp.data or []
        
        # Add basic scoring for relevance
        scored_contacts = []
        for contact in contacts:
            score = 0
            query_lower = query.lower()
            
            # Exact matches get highest scores
            if contact.get('name', '').lower() == query_lower:
                score = 100
            elif contact.get('first_name', '').lower() == query_lower:
                score = 95
            elif contact.get('last_name', '').lower() == query_lower:
                score = 95
            elif contact.get('email', '').lower().startswith(query_lower):
                score = 90
            elif query_lower in contact.get('name', '').lower():
                score = 80
            elif query_lower in contact.get('email', '').lower():
                score = 70
            else:
                score = 50
            
            contact['score'] = score
            scored_contacts.append(contact)
        
        # Sort by score
        scored_contacts.sort(key=lambda x: x['score'], reverse=True)
        
        if scored_contacts:
            logging.info(f"Found {len(scored_contacts)} contacts using direct search for '{query}'")
            return scored_contacts[:limit]
            
    except Exception as e:
        logging.error(f"Direct database search failed: {e}")
    
    # Strategy 3: Fallback to basic table scan (last resort)
    try:
        # Get all contacts and filter in Python
        resp = supabase.table("contacts").select("*").execute()
        all_contacts = resp.data or []
        
        query_lower = query.lower()
        matching_contacts = []
        
        for contact in all_contacts:
            # Check if query matches any field
            if (query_lower in str(contact.get('name', '')).lower() or
                query_lower in str(contact.get('email', '')).lower() or
                query_lower in str(contact.get('first_name', '')).lower() or
                query_lower in str(contact.get('last_name', '')).lower() or
                query_lower in str(contact.get('display_name', '')).lower() or
                query_lower in str(contact.get('role', '')).lower() or
                query_lower in str(contact.get('company', '')).lower()):
                
                matching_contacts.append(contact)
        
        if matching_contacts:
            logging.info(f"Found {len(matching_contacts)} contacts using table scan for '{query}'")
            return matching_contacts[:limit]
            
    except Exception as e:
        logging.error(f"Table scan search failed: {e}")
    
    # Strategy 4: Ultimate fallback - try unified memory
    try:
        from common.unified_memory import search_contacts
        contacts = search_contacts(query, limit)
        if contacts:
            logging.info(f"Found {len(contacts)} contacts using unified memory for '{query}'")
            return contacts
    except Exception as e:
        logging.error(f"Unified memory search failed: {e}")
    
    logging.warning(f"No contacts found for query '{query}' after trying all strategies")
    return []


def get_contact_by_identifier(identifier: str) -> Optional[Dict[str, Any]]:
    """
    Get a single contact by email, name, or alias with robust search.
    """
    if not identifier:
        return None
    
    # First try exact email match
    if "@" in identifier:
        try:
            resp = supabase.table("contacts").select("*").eq("email", identifier.lower()).limit(1).execute()
            if resp.data:
                return resp.data[0]
        except Exception as e:
            logging.error(f"Error in email search: {e}")
    
    # Try smart search
    contacts = search_contacts_smart(identifier, limit=1)
    return contacts[0] if contacts else None


def create_or_update_contact(
    email: str,
    name: str,
    **kwargs
) -> Dict[str, Any]:
    """
    Create a new contact or update existing one.
    """
    # Check if contact exists
    existing = get_contact_by_identifier(email)
    
    if existing:
        # Update existing contact
        update_data = {
            "updated_at": datetime.utcnow().isoformat()
        }
        
        # Only update fields that are provided and different
        for field, value in kwargs.items():
            if value and existing.get(field) != value:
                update_data[field] = value
        
        if len(update_data) > 1:  # More than just updated_at
            resp = supabase.table("contacts").update(update_data).eq("id", existing["id"]).execute()
            logging.info(f"Updated contact: {email}")
            return resp.data[0]
        return existing
    else:
        # Create new contact
        contact_data = {
            "email": email.lower(),
            "name": name,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            **kwargs
        }
        
        resp = supabase.table("contacts").insert(contact_data).execute()
        logging.info(f"Created new contact: {email}")
        return resp.data[0]


# ═══════════════════════════════════════════════════════════════════════════════
# Natural Language Processing for Contacts
# ═══════════════════════════════════════════════════════════════════════════════

def extract_contact_intent(message: str) -> Dict[str, Any]:
    """
    Extract contact-related intent from a message using advanced pattern matching.
    """
    intent = {
        "has_contact_query": False,
        "query_type": None,  # email, phone, info, etc.
        "mentioned_names": [],
        "requested_info": [],
        "confidence": 0.0
    }
    
    message_lower = message.lower()
    
    # Patterns for contact information requests
    info_patterns = [
        # Email queries
        (r"(?:what(?:'s|s| is)|whats)\s+(\w+)(?:'s|s)?\s+email", "email"),
        (r"(\w+)(?:'s|s)?\s+email\s*\??", "email"),
        (r"email\s+(?:of|for)\s+(\w+)", "email"),
        (r"(?:can you |could you |please )?(?:give me |tell me |share )?(\w+)(?:'s|s)?\s+email", "email"),
        
        # Phone queries
        (r"(?:what(?:'s|s| is)|whats)\s+(\w+)(?:'s|s)?\s+(?:phone|number)", "phone"),
        (r"(\w+)(?:'s|s)?\s+(?:phone|number)\s*\??", "phone"),
        (r"(?:phone|number)\s+(?:of|for)\s+(\w+)", "phone"),
        
        # General contact info
        (r"(?:how (?:do i|can i|to)) (?:contact|reach|get in touch with)\s+(\w+)", "contact_info"),
        (r"(?:contact )?(?:info|information|details)\s+(?:for|about|on)\s+(\w+)", "contact_info"),
        
        # Role/position queries
        (r"(?:what(?:'s|s| is)|whats)\s+(\w+)(?:'s|s)?\s+(?:role|position|title)", "role"),
        (r"(?:who is|whos)\s+(\w+)", "general_info"),
        
        # Simple name mentions that might be contact queries
        (r"(?:tell me about|info on|information about)\s+(\w+)", "general_info"),
    ]
    
    # Check each pattern
    for pattern, query_type in info_patterns:
        matches = re.findall(pattern, message_lower, re.IGNORECASE)
        for match in matches:
            name = match.strip().title()
            if name and len(name) > 1:  # Filter out single letters
                intent["has_contact_query"] = True
                intent["query_type"] = query_type
                intent["mentioned_names"].append(name)
                intent["requested_info"].append(query_type)
                intent["confidence"] = 0.9
    
    # Also look for any capitalized names in the message
    name_pattern = r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b'
    potential_names = re.findall(name_pattern, message)
    
    for name in potential_names:
        if name not in intent["mentioned_names"] and name not in ["I", "The", "This", "That", "What", "Who", "Where", "When", "Why", "How"]:
            intent["mentioned_names"].append(name)
            if not intent["has_contact_query"]:
                intent["confidence"] = 0.5  # Lower confidence for just name mentions
    
    # Remove duplicates
    intent["mentioned_names"] = list(set(intent["mentioned_names"]))
    intent["requested_info"] = list(set(intent["requested_info"]))
    
    return intent


def analyze_contact_context(message: str, chat_id: str) -> Dict[str, Any]:
    """
    Comprehensive contact context analysis for a message.
    """
    context = {
        "intent": extract_contact_intent(message),
        "found_contacts": [],
        "missing_contacts": [],
        "suggestions": [],
        "response_needed": False
    }
    
    # Extract intent
    intent = context["intent"]
    
    # Search for mentioned contacts
    for name in intent["mentioned_names"]:
        contacts = search_contacts_smart(name, limit=3)
        
        if contacts:
            # Add found contacts with their requested information
            for contact in contacts:
                contact_info = {
                    "name": contact.get("name", "Unknown"),
                    "email": contact.get("email", "No email"),
                    "match_score": contact.get("score", 0)
                }
                
                # Add requested fields
                if "phone" in intent["requested_info"]:
                    contact_info["phone"] = contact.get("phone") or contact.get("mobile_phone") or "Not available"
                
                if "role" in intent["requested_info"]:
                    contact_info["role"] = contact.get("role") or "Not specified"
                    contact_info["company"] = contact.get("company") or "Not specified"
                
                context["found_contacts"].append(contact_info)
        else:
            context["missing_contacts"].append(name)
    
    # Determine if a response is needed
    if intent["has_contact_query"] and (context["found_contacts"] or context["missing_contacts"]):
        context["response_needed"] = True
    
    # Generate suggestions
    if context["missing_contacts"]:
        context["suggestions"].append(f"I couldn't find contact information for: {', '.join(context['missing_contacts'])}")
    
    return context


def format_contact_response(context: Dict[str, Any]) -> str:
    """
    Format a natural language response for contact queries.
    """
    if not context["response_needed"]:
        return ""
    
    response_parts = []
    intent = context["intent"]
    
    # Handle found contacts
    for contact in context["found_contacts"]:
        if intent["query_type"] == "email":
            response_parts.append(f"{contact['name']}'s email is {contact['email']}")
        
        elif intent["query_type"] == "phone":
            phone = contact.get("phone", "Not available")
            if phone != "Not available":
                response_parts.append(f"{contact['name']}'s phone is {phone}")
            else:
                response_parts.append(f"I don't have a phone number for {contact['name']}")
        
        elif intent["query_type"] == "role":
            role = contact.get("role", "Not specified")
            company = contact.get("company", "")
            if role != "Not specified":
                role_text = f"{contact['name']} is {role}"
                if company:
                    role_text += f" at {company}"
                response_parts.append(role_text)
            else:
                response_parts.append(f"I don't have role information for {contact['name']}")
        
        elif intent["query_type"] in ["contact_info", "general_info"]:
            info_parts = [f"{contact['name']}:"]
            info_parts.append(f"  Email: {contact['email']}")
            if contact.get("phone"):
                info_parts.append(f"  Phone: {contact['phone']}")
            if contact.get("role"):
                info_parts.append(f"  Role: {contact['role']}")
            if contact.get("company"):
                info_parts.append(f"  Company: {contact['company']}")
            response_parts.append("\n".join(info_parts))
    
    # Handle missing contacts
    if context["missing_contacts"]:
        missing_names = ", ".join(context["missing_contacts"])
        response_parts.append(f"I couldn't find contact information for {missing_names}")
    
    return "\n\n".join(response_parts)


# ═══════════════════════════════════════════════════════════════════════════════
# Contact Enrichment and Tracking
# ═══════════════════════════════════════════════════════════════════════════════

def track_contact_mention(
    contact_id: int,
    chat_id: str,
    message_id: Optional[str] = None,
    context: Optional[str] = None
) -> None:
    """
    Track when a contact is mentioned in a conversation.
    """
    try:
        mention_data = {
            "contact_id": contact_id,
            "chat_id": chat_id,
            "message_id": message_id,
            "mention_context": context,
            "mention_type": "direct",
            "created_at": datetime.utcnow().isoformat()
        }
        
        # Note: This table would need to be created in the database
        # For now, we'll just log it
        logging.info(f"Contact {contact_id} mentioned in chat {chat_id}")
    except Exception as e:
        logging.error(f"Error tracking contact mention: {e}")


def update_contact_interaction(
    contact_id: int,
    interaction_type: str = "chat",
    chat_id: Optional[str] = None
) -> None:
    """
    Update contact interaction statistics.
    """
    try:
        update_data = {
            "last_interaction": datetime.utcnow().isoformat(),
            "interaction_count": supabase.rpc("increment", {"x": 1}),  # This would need a DB function
            "updated_at": datetime.utcnow().isoformat()
        }
        
        resp = supabase.table("contacts").update(update_data).eq("id", contact_id).execute()
        logging.info(f"Updated interaction for contact {contact_id}")
    except Exception as e:
        logging.error(f"Error updating contact interaction: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def get_contact_summary_for_ai(contact_ids: List[int]) -> str:
    """
    Get a formatted summary of contacts for AI context.
    """
    if not contact_ids:
        return ""
    
    summaries = []
    
    for contact_id in contact_ids[:5]:  # Limit to 5 contacts
        resp = supabase.table("contacts").select("*").eq("id", contact_id).limit(1).execute()
        if resp.data:
            contact = resp.data[0]
            summary = f"• {contact['name']} ({contact['email']})"
            if contact.get('role'):
                summary += f" - {contact['role']}"
            if contact.get('company'):
                summary += f" at {contact['company']}"
            if contact.get('last_interaction'):
                summary += f" (last contact: {contact['last_interaction'][:10]})"
            summaries.append(summary)
    
    return "\n".join(summaries)


def extract_contacts_from_email_headers(email_headers: Dict[str, str]) -> List[Dict[str, str]]:
    """
    Extract contact information from email headers.
    """
    contacts = []
    
    # Extract from common email fields
    for field in ["from", "to", "cc"]:
        if field in email_headers:
            # Parse email addresses (simplified - you might want a proper email parser)
            email_pattern = r'([^<\s]+@[^>\s]+)'
            name_pattern = r'^([^<]+)<'
            
            value = email_headers[field]
            emails = re.findall(email_pattern, value)
            names = re.findall(name_pattern, value)
            
            for i, email in enumerate(emails):
                contact = {"email": email.strip()}
                if i < len(names):
                    contact["name"] = names[i].strip()
                else:
                    # Use email prefix as name
                    contact["name"] = email.split("@")[0].replace(".", " ").title()
                contacts.append(contact)
    
    return contacts

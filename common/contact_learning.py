"""
Advanced Contact Learning System
- Detects new people mentioned in conversations
- Extracts contact details from natural conversation flow
- Manages pending contact information gathering
- Handles the "Alex got fired" -> "he was a manager" -> "what's his email?" scenario
"""

from __future__ import annotations
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

from openai import OpenAI
from common.supabase import supabase
from common.contact_intelligence import get_contact_by_identifier, create_or_update_contact

client = OpenAI()
logging.getLogger(__name__).setLevel(logging.INFO)

@dataclass
class PendingContact:
    """Represents a contact that's been mentioned but needs more information"""
    name: str
    chat_id: str
    mentioned_at: str
    context: str
    known_info: Dict[str, Any]
    missing_info: List[str]
    confidence: float
    status: str = "pending"  # pending, gathering, complete, abandoned

@dataclass
class ContactMention:
    """Represents a mention of a person in conversation"""
    name: str
    context: str
    message_id: Optional[str] = None
    extracted_info: Optional[Dict[str, Any]] = None
    confidence: float = 0.0

class ContactLearningSystem:
    """Advanced system for learning about contacts from natural conversation"""
    
    @staticmethod
    def detect_person_mentions(message: str, chat_history: List[Dict]) -> List[ContactMention]:
        """
        Detect when people are mentioned in a message using advanced NLP
        """
        mentions = []
        
        # Use OpenAI to detect person mentions with context
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": """You are an expert at detecting when people are mentioned in conversations. 
                        Analyze the message and return a JSON object with:
                        {
                            "mentions": [
                                {
                                    "name": "person's name",
                                    "context": "surrounding context about this person",
                                    "extracted_info": {
                                        "role": "if mentioned",
                                        "company": "if mentioned", 
                                        "email": "if mentioned",
                                        "phone": "if mentioned",
                                        "relationship": "colleague/manager/client/etc if clear"
                                    },
                                    "confidence": 0.0-1.0
                                }
                            ]
                        }
                        
                        Only include actual people (not companies, products, etc.). 
                        Extract any information mentioned about them.
                        Be conservative - only high confidence mentions."""
                    },
                    {"role": "user", "content": f"Message: {message}"}
                ],
                temperature=0.1,
            )
            
            result = json.loads(response.choices[0].message.content)
            
            for mention_data in result.get("mentions", []):
                mention = ContactMention(
                    name=mention_data["name"],
                    context=mention_data["context"],
                    extracted_info=mention_data.get("extracted_info", {}),
                    confidence=mention_data.get("confidence", 0.0)
                )
                mentions.append(mention)
                
        except Exception as e:
            logging.error(f"Error detecting person mentions: {e}")
            
            # Fallback to regex-based detection
            mentions.extend(ContactLearningSystem._fallback_person_detection(message))
        
        return mentions
    
    @staticmethod
    def _fallback_person_detection(message: str) -> List[ContactMention]:
        """Fallback regex-based person detection"""
        mentions = []
        
        # Pattern for names (capitalized words)
        name_patterns = [
            r'\b([A-Z][a-z]+ [A-Z][a-z]+)\b',  # First Last
            r'\b([A-Z][a-z]+)\b(?=\s+(?:said|told|mentioned|asked|replied|got|was|is))',  # Single name with action
        ]
        
        for pattern in name_patterns:
            matches = re.findall(pattern, message, re.IGNORECASE)
            for match in matches:
                name = match.strip()
                if len(name) > 2 and name not in ["The", "This", "That", "And", "But"]:
                    mentions.append(ContactMention(
                        name=name,
                        context=message,
                        confidence=0.6
                    ))
        
        return mentions
    
    @staticmethod
    def process_contact_mentions(
        mentions: List[ContactMention], 
        chat_id: str, 
        message_id: str = None
    ) -> List[PendingContact]:
        """
        Process detected mentions and create pending contacts for unknown people
        """
        pending_contacts = []
        
        for mention in mentions:
            # Check if this person already exists in our contacts
            existing_contact = get_contact_by_identifier(mention.name)
            
            if existing_contact:
                # Person exists - update interaction tracking
                ContactLearningSystem._track_contact_mention(
                    existing_contact["id"], chat_id, message_id, mention.context
                )
                logging.info(f"Known contact mentioned: {mention.name}")
            else:
                # New person - create pending contact
                pending = ContactLearningSystem._create_pending_contact(
                    mention, chat_id, message_id
                )
                if pending:
                    pending_contacts.append(pending)
                    logging.info(f"New person detected: {mention.name}")
        
        return pending_contacts
    
    @staticmethod
    def _create_pending_contact(
        mention: ContactMention, 
        chat_id: str, 
        message_id: str = None
    ) -> Optional[PendingContact]:
        """Create a pending contact from a mention"""
        
        # Determine what information we have and what we need
        known_info = mention.extracted_info or {}
        missing_info = []
        
        # Essential fields we always want
        if not known_info.get("email"):
            missing_info.append("email")
        if not known_info.get("role"):
            missing_info.append("role")
        
        # Create pending contact
        pending = PendingContact(
            name=mention.name,
            chat_id=chat_id,
            mentioned_at=datetime.utcnow().isoformat(),
            context=mention.context,
            known_info=known_info,
            missing_info=missing_info,
            confidence=mention.confidence
        )
        
        # Store in database
        try:
            supabase.table("pending_contacts").insert(asdict(pending)).execute()
            return pending
        except Exception as e:
            logging.error(f"Error storing pending contact: {e}")
            return None
    
    @staticmethod
    def update_pending_contact(
        name: str, 
        chat_id: str, 
        new_info: Dict[str, Any]
    ) -> Optional[PendingContact]:
        """Update a pending contact with new information"""
        
        try:
            # Get existing pending contact
            resp = supabase.table("pending_contacts").select("*").eq("name", name).eq("chat_id", chat_id).eq("status", "pending").limit(1).execute()
            
            if not resp.data:
                return None
            
            pending_data = resp.data[0]
            
            # Update known info
            known_info = pending_data.get("known_info", {})
            known_info.update(new_info)
            
            # Update missing info
            missing_info = pending_data.get("missing_info", [])
            for key in new_info.keys():
                if key in missing_info:
                    missing_info.remove(key)
            
            # Update in database
            update_data = {
                "known_info": known_info,
                "missing_info": missing_info,
                "updated_at": datetime.utcnow().isoformat()
            }
            
            supabase.table("pending_contacts").update(update_data).eq("id", pending_data["id"]).execute()
            
            # Check if we have enough info to create the contact
            if not missing_info or (known_info.get("email") and known_info.get("name")):
                ContactLearningSystem._complete_pending_contact(pending_data["id"], known_info)
            
            return PendingContact(**{**pending_data, **update_data})
            
        except Exception as e:
            logging.error(f"Error updating pending contact: {e}")
            return None
    
    @staticmethod
    def _complete_pending_contact(pending_id: int, contact_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Complete a pending contact by creating the actual contact record"""
        
        try:
            # Create the contact
            contact = create_or_update_contact(
                email=contact_info.get("email", f"{contact_info['name'].lower().replace(' ', '.')}@unknown.com"),
                name=contact_info["name"],
                **{k: v for k, v in contact_info.items() if k not in ["name", "email"]}
            )
            
            # Mark pending contact as complete
            supabase.table("pending_contacts").update({
                "status": "complete",
                "completed_at": datetime.utcnow().isoformat(),
                "contact_id": contact["id"]
            }).eq("id", pending_id).execute()
            
            logging.info(f"Completed pending contact: {contact_info['name']}")
            return contact
            
        except Exception as e:
            logging.error(f"Error completing pending contact: {e}")
            return None
    
    @staticmethod
    def get_pending_contacts(chat_id: str) -> List[PendingContact]:
        """Get all pending contacts for a chat"""
        
        try:
            resp = supabase.table("pending_contacts").select("*").eq("chat_id", chat_id).eq("status", "pending").execute()
            
            return [PendingContact(**data) for data in resp.data or []]
            
        except Exception as e:
            logging.error(f"Error getting pending contacts: {e}")
            return []
    
    @staticmethod
    def generate_info_gathering_prompt(pending_contacts: List[PendingContact]) -> Optional[str]:
        """Generate a natural prompt to gather missing contact information"""
        
        if not pending_contacts:
            return None
        
        # Focus on the most recent or highest confidence pending contact
        target_contact = max(pending_contacts, key=lambda x: x.confidence)
        
        missing = target_contact.missing_info
        name = target_contact.name
        
        if "email" in missing:
            return f"Pour pouvoir contacter {name}, j'aurais besoin de son adresse email. Peux-tu me la donner?"
        elif "role" in missing:
            return f"Quel est le rôle de {name}? Cela m'aiderait à mieux comprendre le contexte."
        else:
            return f"J'ai quelques informations sur {name}, mais j'aimerais en savoir plus. Peux-tu me donner plus de détails?"
    
    @staticmethod
    def _track_contact_mention(
        contact_id: int, 
        chat_id: str, 
        message_id: str = None, 
        context: str = None
    ) -> None:
        """Track when an existing contact is mentioned"""
        
        try:
            mention_data = {
                "contact_id": contact_id,
                "chat_id": chat_id,
                "message_id": message_id,
                "context": context,
                "mentioned_at": datetime.utcnow().isoformat()
            }
            
            supabase.table("contact_mentions").insert(mention_data).execute()
            
        except Exception as e:
            logging.error(f"Error tracking contact mention: {e}")
    
    @staticmethod
    def extract_contact_info_from_conversation(
        message: str, 
        chat_history: List[Dict], 
        pending_contact_name: str
    ) -> Dict[str, Any]:
        """
        Extract contact information about a specific person from conversation context
        """
        
        try:
            # Build context from recent messages
            context_messages = []
            for msg in chat_history[-10:]:  # Last 10 messages
                if pending_contact_name.lower() in msg.get("content", "").lower():
                    context_messages.append(msg["content"])
            
            context_messages.append(message)
            context_text = "\n".join(context_messages)
            
            response = client.chat.completions.create(
                model="gpt-4o",
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": f"""Extract any information about {pending_contact_name} from this conversation.
                        Return JSON with any of these fields if mentioned:
                        {{
                            "email": "email address",
                            "role": "job title/role",
                            "company": "company name",
                            "phone": "phone number",
                            "department": "department",
                            "relationship": "how they relate to the speakers"
                        }}
                        Only include information that is explicitly stated or clearly implied."""
                    },
                    {"role": "user", "content": context_text}
                ],
                temperature=0.1,
            )
            
            return json.loads(response.choices[0].message.content)
            
        except Exception as e:
            logging.error(f"Error extracting contact info: {e}")
            return {}


# Database setup for pending contacts and mentions
def setup_contact_learning_tables():
    """Set up database tables for contact learning system"""
    
    # This would be run as part of database migration
    sql_commands = [
        """
        CREATE TABLE IF NOT EXISTS pending_contacts (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            mentioned_at TIMESTAMPTZ DEFAULT NOW(),
            context TEXT,
            known_info JSONB DEFAULT '{}',
            missing_info TEXT[] DEFAULT '{}',
            confidence FLOAT DEFAULT 0.0,
            status TEXT DEFAULT 'pending',
            completed_at TIMESTAMPTZ,
            contact_id BIGINT REFERENCES contacts(id),
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS contact_mentions (
            id BIGSERIAL PRIMARY KEY,
            contact_id BIGINT REFERENCES contacts(id),
            chat_id TEXT NOT NULL,
            message_id TEXT,
            context TEXT,
            mentioned_at TIMESTAMPTZ DEFAULT NOW()
        );
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_pending_contacts_chat_status 
        ON pending_contacts(chat_id, status);
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_contact_mentions_contact_chat 
        ON contact_mentions(contact_id, chat_id);
        """
    ]
    
    return sql_commands

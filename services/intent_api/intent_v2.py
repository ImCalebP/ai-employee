"""
Enhanced Intent Detection & Action Orchestration System
- Multi-step action sequences
- Entity resolution (contacts, documents, tasks)
- Missing info handling with context preservation
- Urgency/tone analysis
- Proactive suggestions
"""

from __future__ import annotations
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI

# ───────── Dependencies ─────────
from common.graph_auth import get_access_token
from common.memory_helpers import (
    save_message,
    fetch_chat_history,
    fetch_global_history,
    semantic_search,
)
from common.unified_memory import (
    search_contacts,
    get_contact_by_email,
    search_documents,
    search_tasks,
)
from common.enhanced_memory import (
    get_contextual_intelligence,
    analyze_message_for_proactive_actions,
    find_relevant_documents_for_message,
)
from services.intent_api.email_agent import process_email_request
from services.intent_api.reply_agent import process_reply
from services.intent_api.document_agent import process_document_request
from services.intent_api.task_agent import process_task_request
from services.intent_api.parallel_executor import execute_actions_parallel, create_action

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
app = FastAPI(title="AI-Employee • Enhanced Intent System v2")
logging.basicConfig(level=logging.INFO)


class Intent(str, Enum):
    REPLY = "reply"
    SEND_EMAIL = "send_email"
    SCHEDULE_MEETING = "schedule_meeting"
    CANCEL_MEETING = "cancel_meeting"
    GENERATE_DOCUMENT = "generate_document"
    SHARE_DOCUMENT = "share_document"
    CREATE_TASK = "create_task"
    UPDATE_TASK = "update_task"
    GENERATE_REPORT = "generate_report"
    MEETING_SUMMARY = "meeting_summary"
    PROACTIVE_FOLLOWUP = "proactive_followup"
    ALERT_HUMAN = "alert_human"
    SEARCH_INFO = "search_info"
    UNKNOWN = "unknown"


class ActionStep(BaseModel):
    action: str
    params: Dict[str, Any]
    requires_resolution: Optional[List[str]] = None  # entities that need resolution
    status: str = "pending"  # pending, completed, failed, blocked


class IntentAnalysis(BaseModel):
    primary_intent: Intent
    action_sequence: List[ActionStep]
    urgency: str  # low, medium, high, critical
    tone: str  # neutral, positive, negative, urgent
    missing_info: Optional[Dict[str, str]] = None  # what's missing and why
    entities_mentioned: Dict[str, List[str]]  # type -> list of mentions
    confidence: float


class TeamsWebhookPayload(BaseModel):
    messageId: str
    conversationId: str


def _graph(url: str, token: str, *,
           method: str = "GET",
           payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    import requests
    r = requests.request(
        method, url,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=payload, timeout=10,
    )
    r.raise_for_status()
    return r.json() if r.text else {}


def analyze_intent_advanced(
    text: str,
    chat_history: List[Dict],
    global_history: List[Dict],
    semantic_context: List[Dict]
) -> IntentAnalysis:
    """
    Advanced intent analysis with multi-step planning and entity extraction.
    """
    
    def _format_history(rows):
        return [{"role": "user" if r["sender"] == "user" else "assistant",
                 "content": r["content"]} for r in rows]
    
    messages = [{
        "role": "system",
        "content": """You are an advanced intent analyzer for an AI assistant. Analyze the user's message and return a detailed JSON analysis.

Your response must include:
1. primary_intent: The main intent (reply, send_email, schedule_meeting, cancel_meeting, generate_document, share_document, create_task, update_task, generate_report, meeting_summary, proactive_followup, alert_human, search_info, unknown)
2. action_sequence: List of actions needed to fulfill the intent, each with:
   - action: specific action to take
   - params: parameters for the action
   - requires_resolution: list of entities that need to be resolved (e.g., ["contact:Marc", "document:meeting_summary"])
3. urgency: low, medium, high, or critical
4. tone: neutral, positive, negative, or urgent
5. missing_info: Dictionary of missing information (e.g., {"recipient": "Need email address for Marc", "subject": "Email subject not specified"})
6. entities_mentioned: Dictionary mapping entity types to mentions (e.g., {"contacts": ["Marc"], "documents": ["meeting summary"], "meetings": ["today's meeting"]})
7. confidence: 0.0 to 1.0

Example for "Send the meeting summary to Marc":
{
  "primary_intent": "send_email",
  "action_sequence": [
    {
      "action": "resolve_contact",
      "params": {"name": "Marc"},
      "requires_resolution": ["contact:Marc"]
    },
    {
      "action": "fetch_meeting_summary",
      "params": {"meeting_ref": "latest"},
      "requires_resolution": ["document:meeting_summary"]
    },
    {
      "action": "send_email",
      "params": {"to": "{{resolved:contact:Marc}}", "subject": "Meeting Summary", "body": "{{resolved:document:meeting_summary}}"}
    }
  ],
  "urgency": "medium",
  "tone": "neutral",
  "missing_info": null,
  "entities_mentioned": {"contacts": ["Marc"], "documents": ["meeting summary"]},
  "confidence": 0.85
}

IMPORTANT: Always think through the complete action sequence needed. If information is missing, include it in missing_info."""
    }]
    
    # Add chat history
    messages.extend(_format_history(chat_history[-20:]))  # Last 20 messages
    
    # Add semantic context
    if semantic_context:
        messages.append({"role": "system", "content": "🔎 Relevant context:"})
        messages.extend(_format_history(semantic_context))
    
    # Add global context
    if global_history:
        messages.append({"role": "system", "content": "🌐 Other chats context:"})
        messages.extend(_format_history(global_history))
    
    # Add the current message
    messages.append({"role": "user", "content": text})
    messages.append({"role": "system", "content": "Analyze the intent and return the JSON response."})
    
    response = client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},
        messages=messages,
        temperature=0.3,
    )
    
    analysis_dict = json.loads(response.choices[0].message.content)
    
    # Convert to IntentAnalysis model
    return IntentAnalysis(
        primary_intent=Intent(analysis_dict.get("primary_intent", "unknown")),
        action_sequence=[ActionStep(**step) for step in analysis_dict.get("action_sequence", [])],
        urgency=analysis_dict.get("urgency", "medium"),
        tone=analysis_dict.get("tone", "neutral"),
        missing_info=analysis_dict.get("missing_info"),
        entities_mentioned=analysis_dict.get("entities_mentioned", {}),
        confidence=analysis_dict.get("confidence", 0.5),
    )


def resolve_entities(
    entities_mentioned: Dict[str, List[str]],
    action_sequence: List[ActionStep]
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Resolve mentioned entities (contacts, documents, tasks) from the database.
    Returns: (resolved_entities, unresolved_entities)
    """
    resolved = {}
    unresolved = []
    
    # Resolve contacts
    for contact_ref in entities_mentioned.get("contacts", []):
        # First try email search
        if "@" in contact_ref:
            contact = get_contact_by_email(contact_ref)
            if contact:
                resolved[f"contact:{contact_ref}"] = contact
            else:
                unresolved.append(f"contact:{contact_ref}")
        else:
            # Try name search
            contacts = search_contacts(contact_ref, limit=1)
            if contacts:
                resolved[f"contact:{contact_ref}"] = contacts[0]
            else:
                unresolved.append(f"contact:{contact_ref}")
    
    # Resolve documents
    for doc_ref in entities_mentioned.get("documents", []):
        docs = search_documents(doc_ref, limit=1)
        if docs:
            resolved[f"document:{doc_ref}"] = docs[0]
        else:
            # Check if it's a special reference like "meeting summary"
            if "meeting" in doc_ref.lower() and "summary" in doc_ref.lower():
                # This might need to be fetched from Fireflies or generated
                resolved[f"document:{doc_ref}"] = {"type": "pending_meeting_summary"}
            else:
                unresolved.append(f"document:{doc_ref}")
    
    # Resolve tasks
    for task_ref in entities_mentioned.get("tasks", []):
        tasks = search_tasks(task_ref, limit=1)
        if tasks:
            resolved[f"task:{task_ref}"] = tasks[0]
        else:
            unresolved.append(f"task:{task_ref}")
    
    return resolved, unresolved


def generate_missing_info_prompt(
    missing_info: Dict[str, str],
    unresolved_entities: List[str]
) -> str:
    """
    Generate a natural language prompt for missing information.
    """
    prompts = []
    
    # Handle missing info from intent analysis
    if missing_info:
        for field, reason in missing_info.items():
            prompts.append(reason)
    
    # Handle unresolved entities
    for entity in unresolved_entities:
        entity_type, entity_ref = entity.split(":", 1)
        if entity_type == "contact":
            prompts.append(f"Je ne trouve pas {entity_ref} dans mes contacts. Peux-tu me donner son adresse email?")
        elif entity_type == "document":
            prompts.append(f"Je ne trouve pas le document '{entity_ref}'. Peux-tu être plus précis?")
        elif entity_type == "task":
            prompts.append(f"Je ne trouve pas la tâche '{entity_ref}'. Peux-tu me donner plus de détails?")
    
    return " ".join(prompts) if prompts else ""


@app.post("/webhook/v2")
async def webhook_handler_v2(payload: TeamsWebhookPayload):
    """Enhanced webhook handler with multi-step action orchestration."""
    
    chat_id, msg_id = payload.conversationId, payload.messageId
    logging.info("→ webhook v2 chat=%s msg=%s", chat_id, msg_id)
    
    # 1️⃣ Get access token
    try:
        access_token, _ = get_access_token()
    except RuntimeError:
        raise HTTPException(401, "Run /auth/login once from a browser first.")
    
    # 2️⃣ Fetch the message
    msg = _graph(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages/{msg_id}",
        access_token,
    )
    sender = (msg.get("from") or {}).get("user", {}).get("displayName", "Unknown")
    text = (msg.get("body") or {}).get("content", "").strip()
    
    if sender == "BARA Software" or not text:
        return {"status": "skipped"}
    
    # Get chat type
    chat_type = _graph(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}?$select=chatType",
        access_token,
    ).get("chatType", "unknown")
    
    # Save the message
    save_message(chat_id, "user", text, chat_type)
    
    # 3️⃣ Build context
    chat_history = fetch_chat_history(chat_id, 30)
    global_history = fetch_global_history(8)
    semantic_context = semantic_search(text, chat_id, 8, 4)
    
    # 4️⃣ Advanced intent analysis
    analysis = analyze_intent_advanced(text, chat_history, global_history, semantic_context)
    
    logging.info("Intent analysis: %s", analysis.dict())
    
    # 5️⃣ Entity resolution
    resolved_entities, unresolved_entities = resolve_entities(
        analysis.entities_mentioned,
        analysis.action_sequence
    )
    
    # 6️⃣ Check for missing info or unresolved entities
    if analysis.missing_info or unresolved_entities:
        # Generate prompt for missing info
        prompt = generate_missing_info_prompt(analysis.missing_info, unresolved_entities)
        if prompt:
            # Store the pending action sequence for later
            # TODO: Store in database with a session ID
            process_reply(chat_id, text, custom_prompt=prompt)
            return {
                "status": "missing_info",
                "intent": analysis.primary_intent,
                "missing": analysis.missing_info,
                "unresolved": unresolved_entities,
            }
    
    # 7️⃣ Execute action sequence with parallel execution
    results = []
    
    # If intent is unknown or no actions, default to reply
    if analysis.primary_intent == Intent.UNKNOWN or not analysis.action_sequence:
        process_reply(chat_id, text)
        return {
            "status": "ok",
            "intent": "reply",
            "urgency": analysis.urgency,
            "tone": analysis.tone,
            "confidence": analysis.confidence,
            "actions_executed": [{"action": "reply", "result": "replied"}],
            "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
        }
    
    # Check if we can use parallel execution (multiple independent actions)
    if len(analysis.action_sequence) > 1:
        # Convert action sequence to parallel execution format
        parallel_actions = []
        for i, step in enumerate(analysis.action_sequence):
            # Replace entity references with resolved values
            if step.requires_resolution:
                for entity_ref in step.requires_resolution:
                    if entity_ref in resolved_entities:
                        # Replace placeholder with resolved value
                        step.params = json.loads(
                            json.dumps(step.params).replace(
                                f"{{{{resolved:{entity_ref}}}}}",
                                json.dumps(resolved_entities[entity_ref])
                            )
                        )
            
            parallel_actions.append(create_action(
                action=step.action,
                params=step.params,
                action_id=f"action_{i}",
                depends_on=step.requires_resolution if step.requires_resolution else None
            ))
        
        # Execute actions in parallel
        import asyncio
        parallel_results = asyncio.run(execute_actions_parallel(chat_id, parallel_actions))
        
        return {
            "status": "ok",
            "intent": analysis.primary_intent,
            "urgency": analysis.urgency,
            "tone": analysis.tone,
            "confidence": analysis.confidence,
            "actions_executed": parallel_results["results"],
            "execution_summary": {
                "total_actions": parallel_results["total_actions"],
                "completed": parallel_results["completed"],
                "failed": parallel_results["failed"],
                "success_rate": parallel_results["success_rate"],
                "total_duration": parallel_results["total_duration"]
            },
            "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
        }
    
    # Single action - execute sequentially
    for step in analysis.action_sequence:
        # Replace entity references with resolved values
        if step.requires_resolution:
            for entity_ref in step.requires_resolution:
                if entity_ref in resolved_entities:
                    # Replace placeholder with resolved value
                    step.params = json.loads(
                        json.dumps(step.params).replace(
                            f"{{{{resolved:{entity_ref}}}}}",
                            json.dumps(resolved_entities[entity_ref])
                        )
                    )
        
        # Execute the action based on type
        if step.action == "send_email":
            # Use existing email agent
            result = process_email_request(chat_id)
            results.append({"action": step.action, "result": result})
        
        elif step.action == "reply":
            # Use existing reply agent
            process_reply(chat_id, text)
            results.append({"action": step.action, "result": "replied"})
        
        elif step.action == "generate_document":
            # Generate document from text or meeting summary
            result = process_document_request(
                chat_id,
                "generate_from_text",
                step.params
            )
            results.append({"action": step.action, "result": result})
        
        elif step.action == "share_document":
            # Share document via Teams
            result = process_document_request(
                chat_id,
                "share_document",
                step.params
            )
            results.append({"action": step.action, "result": result})
        
        elif step.action == "fetch_meeting_summary":
            # TODO: Integrate with Fireflies.ai API
            # For now, return a placeholder
            results.append({
                "action": step.action,
                "result": {
                    "status": "pending_integration",
                    "message": "Fireflies.ai integration pending"
                }
            })
        
        elif step.action == "create_task":
            # Create a task
            result = process_task_request(
                chat_id,
                "create",
                step.params
            )
            results.append({"action": step.action, "result": result})
        
        elif step.action == "extract_tasks":
            # Extract and create tasks from conversation
            result = process_task_request(
                chat_id,
                "extract_and_create",
                {"text": text}
            )
            results.append({"action": step.action, "result": result})
        
        elif step.action == "update_task":
            # Update existing task
            result = process_task_request(
                chat_id,
                "update",
                step.params
            )
            results.append({"action": step.action, "result": result})
        
        elif step.action == "compile_conversation_summary":
            # Generate a conversation summary document
            conversation_context = step.params.get("conversation_context", [])
            summary_text = "\n".join(conversation_context)
            
            result = process_document_request(
                chat_id,
                "generate_from_text",
                {
                    "text": f"Conversation Summary:\n\n{summary_text}",
                    "type": "conversation_summary"
                }
            )
            results.append({"action": step.action, "result": result})
        
        elif step.action in ["generate_reply", "send_reply", "send_message"]:
            # Handle reply actions - use the reply agent
            message = step.params.get("message", "") or step.params.get("content", "")
            if message:
                # Use custom message from the action
                process_reply(chat_id, text, custom_prompt=message)
            else:
                # Use standard reply
                process_reply(chat_id, text)
            results.append({"action": step.action, "result": "replied"})
        
        else:
            logging.warning(f"Unhandled action: {step.action}")
            results.append({"action": step.action, "result": "not_implemented"})
    
    return {
        "status": "ok",
        "intent": analysis.primary_intent,
        "urgency": analysis.urgency,
        "tone": analysis.tone,
        "confidence": analysis.confidence,
        "actions_executed": results,
        "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
    }


# Keep the original endpoint for backward compatibility
@app.post("/webhook")
async def webhook_handler_legacy(payload: TeamsWebhookPayload):
    """Legacy webhook handler - redirects to v2."""
    return await webhook_handler_v2(payload)

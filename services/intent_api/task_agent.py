"""
Task Management Agent
- Creates and updates tasks from conversations
- Extracts tasks from messages (e.g., "I'll do it tomorrow")
- Tracks task status and assignees
- Integrates with task management platforms (Notion, Trello, ClickUp)
"""

from __future__ import annotations
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI
from common.graph_auth import get_access_token
from common.memory_helpers import save_message
from common.supabase import supabase
from common.unified_memory import search_contacts

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
logging.getLogger(__name__).setLevel(logging.INFO)


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


def _teams_post(chat_id: str, text: str, token: str) -> None:
    import requests
    body = {"body": {"contentType": "text", "content": text}}
    requests.post(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=body, timeout=10
    ).raise_for_status()


def extract_tasks_from_text(
    text: str,
    context: Optional[List[Dict]] = None
) -> List[Dict[str, Any]]:
    """
    Extract implicit tasks from conversation text using GPT.
    """
    prompt = """Extract any tasks or action items from the following text. Look for:
- Direct commitments ("I'll do X", "I will send Y")
- Requests ("Can you do X?", "Please send Y")
- Deadlines ("by tomorrow", "before Friday")
- Assignments ("John should do X")

Return a JSON array of tasks, each with:
- description: what needs to be done
- assignee: who should do it (or null if unclear)
- due_date: when it should be done (ISO format or null)
- priority: low, medium, or high
- source_quote: the exact text that indicates this task

Text to analyze:
"""
    
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": text}
    ]
    
    if context:
        context_text = "\n".join([f"{msg['sender']}: {msg['content']}" for msg in context[-5:]])
        messages.append({"role": "system", "content": f"Recent context:\n{context_text}"})
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=messages,
        temperature=0.3
    )
    
    result = json.loads(response.choices[0].message.content)
    return result.get("tasks", [])


def parse_due_date(date_str: str) -> Optional[datetime]:
    """
    Parse natural language dates into datetime objects.
    """
    if not date_str:
        return None
    
    date_str_lower = date_str.lower()
    now = datetime.utcnow()
    
    # Common patterns
    if "tomorrow" in date_str_lower:
        return now + timedelta(days=1)
    elif "today" in date_str_lower:
        return now
    elif "next week" in date_str_lower:
        return now + timedelta(weeks=1)
    elif "friday" in date_str_lower:
        # Find next Friday
        days_ahead = 4 - now.weekday()  # Friday is 4
        if days_ahead <= 0:
            days_ahead += 7
        return now + timedelta(days=days_ahead)
    
    # Try to parse ISO format
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except:
        return None


def create_task(
    description: str,
    assignee: Optional[str] = None,
    due_date: Optional[str] = None,
    priority: str = "medium",
    chat_id: Optional[str] = None,
    project_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Create a new task in the database.
    """
    # Resolve assignee if it's a name
    assignee_email = None
    if assignee:
        if "@" in assignee:
            assignee_email = assignee
        else:
            # Try to find contact by name
            contacts = search_contacts(assignee, limit=1)
            if contacts:
                assignee_email = contacts[0].get("email")
    
    # Parse due date
    due_datetime = parse_due_date(due_date) if due_date else None
    
    task_record = {
        "description": description,
        "assignee": assignee_email or assignee,
        "due_date": due_datetime.isoformat() if due_datetime else None,
        "priority": priority,
        "status": "pending",
        "chat_id": chat_id,
        "project_id": project_id,
        "created_at": datetime.utcnow().isoformat(),
        "metadata": metadata or {}
    }
    
    # Insert into tasks table
    resp = supabase.table("tasks").insert(task_record).execute()
    if getattr(resp, "error", None):
        raise RuntimeError(f"Failed to create task: {resp.error}")
    
    task = resp.data[0]
    logging.info(f"✓ Task created: {task['id']} - {description[:50]}...")
    
    return task


def update_task(
    task_id: str,
    updates: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Update an existing task.
    """
    # Validate updates
    allowed_fields = {"description", "assignee", "due_date", "priority", "status"}
    filtered_updates = {k: v for k, v in updates.items() if k in allowed_fields}
    
    if not filtered_updates:
        raise ValueError("No valid fields to update")
    
    # Update in database
    resp = supabase.table("tasks").update(filtered_updates).eq("id", task_id).execute()
    if getattr(resp, "error", None):
        raise RuntimeError(f"Failed to update task: {resp.error}")
    
    task = resp.data[0]
    logging.info(f"✓ Task updated: {task_id}")
    
    return task


def get_tasks_for_user(
    user_email: str,
    status: Optional[str] = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Get tasks assigned to a specific user.
    """
    query = supabase.table("tasks").select("*").eq("assignee", user_email)
    
    if status:
        query = query.eq("status", status)
    
    resp = query.order("due_date", desc=False).limit(limit).execute()
    return resp.data or []


def get_overdue_tasks() -> List[Dict[str, Any]]:
    """
    Get all overdue tasks.
    """
    now = datetime.utcnow().isoformat()
    resp = (
        supabase.table("tasks")
        .select("*")
        .lt("due_date", now)
        .eq("status", "pending")
        .execute()
    )
    return resp.data or []


def process_task_request(
    chat_id: str,
    action: str,
    params: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Main entry point for task-related actions from the intent system.
    """
    access_token, _ = get_access_token()
    
    if action == "create":
        # Create a new task
        task = create_task(
            description=params.get("description", ""),
            assignee=params.get("assignee"),
            due_date=params.get("due_date"),
            priority=params.get("priority", "medium"),
            chat_id=chat_id,
            metadata=params.get("metadata", {})
        )
        
        # Notify in Teams
        assignee_text = f" to {task['assignee']}" if task.get("assignee") else ""
        due_text = f" by {task['due_date'][:10]}" if task.get("due_date") else ""
        _teams_post(
            chat_id,
            f"✅ Task created{assignee_text}: {task['description']}{due_text}",
            access_token
        )
        
        return {"status": "created", "task": task}
    
    elif action == "extract_and_create":
        # Extract tasks from conversation and create them
        from common.memory_helpers import fetch_chat_history
        context = fetch_chat_history(chat_id, 10)
        text = params.get("text", "")
        
        extracted_tasks = extract_tasks_from_text(text, context)
        created_tasks = []
        
        for task_data in extracted_tasks:
            try:
                task = create_task(
                    description=task_data["description"],
                    assignee=task_data.get("assignee"),
                    due_date=task_data.get("due_date"),
                    priority=task_data.get("priority", "medium"),
                    chat_id=chat_id,
                    metadata={"source_quote": task_data.get("source_quote")}
                )
                created_tasks.append(task)
            except Exception as e:
                logging.error(f"Failed to create extracted task: {e}")
        
        if created_tasks:
            summary = f"✅ {len(created_tasks)} task(s) extracted and created:\n"
            for task in created_tasks:
                summary += f"• {task['description']}\n"
            _teams_post(chat_id, summary, access_token)
        
        return {"status": "extracted", "tasks": created_tasks}
    
    elif action == "update":
        # Update existing task
        task_id = params.get("task_id")
        updates = params.get("updates", {})
        
        if not task_id:
            raise ValueError("task_id is required for update action")
        
        task = update_task(task_id, updates)
        
        # Notify in Teams
        _teams_post(
            chat_id,
            f"✅ Task updated: {task['description']} (Status: {task['status']})",
            access_token
        )
        
        return {"status": "updated", "task": task}
    
    elif action == "list":
        # List tasks for a user
        user_email = params.get("user_email")
        status = params.get("status")
        
        if not user_email:
            # Try to get current user's email
            # This is a simplified approach - you might need to enhance this
            user_email = "current_user@company.com"
        
        tasks = get_tasks_for_user(user_email, status)
        
        if tasks:
            summary = f"📋 Tasks for {user_email}:\n"
            for task in tasks[:5]:  # Show first 5
                status_emoji = "✅" if task["status"] == "completed" else "⏳"
                due_text = f" (Due: {task['due_date'][:10]})" if task.get("due_date") else ""
                summary += f"{status_emoji} {task['description']}{due_text}\n"
            
            if len(tasks) > 5:
                summary += f"... and {len(tasks) - 5} more"
            
            _teams_post(chat_id, summary, access_token)
        else:
            _teams_post(chat_id, "No tasks found.", access_token)
        
        return {"status": "listed", "tasks": tasks}
    
    elif action == "check_overdue":
        # Check for overdue tasks
        overdue_tasks = get_overdue_tasks()
        
        if overdue_tasks:
            summary = f"⚠️ {len(overdue_tasks)} overdue task(s):\n"
            for task in overdue_tasks[:5]:
                assignee_text = f" ({task['assignee']})" if task.get("assignee") else ""
                summary += f"• {task['description']}{assignee_text}\n"
            
            _teams_post(chat_id, summary, access_token)
        else:
            _teams_post(chat_id, "✅ No overdue tasks!", access_token)
        
        return {"status": "checked", "overdue_tasks": overdue_tasks}
    
    else:
        raise ValueError(f"Unknown task action: {action}")

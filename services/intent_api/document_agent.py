"""
Document Generation Agent
- Generates documents from text/meeting summaries
- Converts between formats (DOCX, PDF)
- Shares documents via Teams/email
- Stores documents in the unified memory system
"""

from __future__ import annotations
import json
import logging
import os
import requests
import tempfile
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from openai import OpenAI
from common.graph_auth import get_access_token
from common.memory_helpers import save_message
from common.supabase import supabase
from common.enhanced_memory import save_document_with_embedding

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
logging.getLogger(__name__).setLevel(logging.INFO)

# Document generation API endpoints (configure these)
DOCX_GEN_API_URL = os.getenv("DOCX_GEN_API_URL", "http://localhost:5000/generate-docx")
DOCX_TO_TEXT_API_URL = os.getenv("DOCX_TO_TEXT_API_URL", "http://localhost:5001/convert")


def _graph(url: str, token: str, *,
           method: str = "GET",
           payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    r = requests.request(
        method, url,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=payload, timeout=10,
    )
    r.raise_for_status()
    return r.json() if r.text else {}


def _teams_post(chat_id: str, text: str, token: str) -> None:
    body = {"body": {"contentType": "text", "content": text}}
    requests.post(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=body, timeout=10
    ).raise_for_status()


def generate_document_from_text(
    text: str,
    doc_type: str = "report",
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Generate a DOCX document from text using the document generation API.
    Returns document metadata including file path and ID.
    """
    try:
        # Call the document generation API
        response = requests.post(
            DOCX_GEN_API_URL,
            json={"text": text},
            timeout=30
        )
        response.raise_for_status()
        
        # Save the document locally
        doc_id = str(uuid.uuid4())
        temp_path = f"/tmp/{doc_id}.docx"
        
        with open(temp_path, "wb") as f:
            f.write(response.content)
        
        # Extract title from content-disposition or generate one
        content_disp = response.headers.get("content-disposition", "")
        if "filename=" in content_disp:
            filename = content_disp.split("filename=")[-1].strip('"')
            title = filename.replace(".docx", "").replace("_", " ")
        else:
            title = f"{doc_type.capitalize()} - {datetime.utcnow().strftime('%Y-%m-%d')}"
        
        # Save document with semantic embedding using enhanced memory
        doc_record = save_document_with_embedding(
            title=title,
            content=text,
            doc_type=doc_type,
            file_path=temp_path,
            metadata=metadata
        )
        
        logging.info(f"✓ Document generated with embedding: {title}")
        return doc_record
        
    except Exception as e:
        logging.error(f"Document generation failed: {e}")
        raise


def generate_meeting_summary_document(
    meeting_data: Dict[str, Any],
    chat_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generate a meeting summary document from meeting data (e.g., from Fireflies).
    """
    # Format the meeting data into a structured text
    meeting_text = format_meeting_summary(meeting_data)
    
    # Generate the document
    doc_metadata = {
        "meeting_id": meeting_data.get("id"),
        "meeting_title": meeting_data.get("title"),
        "meeting_date": meeting_data.get("date"),
        "participants": meeting_data.get("participants", [])
    }
    
    document = generate_document_from_text(
        meeting_text,
        doc_type="meeting_summary",
        metadata=doc_metadata
    )
    
    # If chat_id provided, notify in Teams
    if chat_id:
        try:
            access_token, _ = get_access_token()
            _teams_post(
                chat_id,
                f"✅ Meeting summary document created: {document['title']}",
                access_token
            )
        except Exception as e:
            logging.error(f"Failed to notify Teams: {e}")
    
    return document


def format_meeting_summary(meeting_data: Dict[str, Any]) -> str:
    """
    Format meeting data into a structured text for document generation.
    """
    # Use GPT to format the meeting summary nicely
    prompt = f"""Format the following meeting data into a professional meeting summary:

Meeting Title: {meeting_data.get('title', 'Untitled Meeting')}
Date: {meeting_data.get('date', 'Unknown')}
Duration: {meeting_data.get('duration', 'Unknown')}
Participants: {', '.join(meeting_data.get('participants', []))}

Summary: {meeting_data.get('summary', '')}

Key Points:
{chr(10).join('- ' + point for point in meeting_data.get('key_points', []))}

Action Items:
{chr(10).join('- ' + item for item in meeting_data.get('action_items', []))}

Decisions:
{chr(10).join('- ' + decision for decision in meeting_data.get('decisions', []))}

Please format this into a professional meeting summary document."""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a professional document formatter."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3
    )
    
    return response.choices[0].message.content.strip()


def share_document_via_teams(
    document_id: str,
    chat_id: str,
    message: Optional[str] = None
) -> Dict[str, Any]:
    """
    Share a document in a Teams chat.
    """
    # Get document from database
    resp = supabase.table("documents").select("*").eq("id", document_id).limit(1).execute()
    document = (resp.data or [None])[0]
    
    if not document:
        raise ValueError(f"Document {document_id} not found")
    
    access_token, _ = get_access_token()
    
    # Upload document to OneDrive first
    file_path = document["file_path"]
    file_name = f"{document['title']}.docx"
    
    # Read file content
    with open(file_path, "rb") as f:
        file_content = f.read()
    
    # Upload to OneDrive (simplified - you may need to adjust the path)
    upload_url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{file_name}:/content"
    upload_resp = requests.put(
        upload_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        },
        data=file_content,
        timeout=30
    )
    upload_resp.raise_for_status()
    
    # Get the sharing link
    share_url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{file_name}:/createLink"
    share_resp = _graph(
        share_url,
        access_token,
        method="POST",
        payload={"type": "view", "scope": "organization"}
    )
    
    sharing_link = share_resp.get("link", {}).get("webUrl", "")
    
    # Post message with link to Teams
    final_message = message or f"📄 Document: {document['title']}"
    final_message += f"\n🔗 {sharing_link}"
    
    _teams_post(chat_id, final_message, access_token)
    
    logging.info(f"✓ Document {document_id} shared in chat {chat_id}")
    
    return {
        "status": "shared",
        "document_id": document_id,
        "sharing_link": sharing_link,
        "chat_id": chat_id
    }


def process_document_request(
    chat_id: str,
    action: str,
    params: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Main entry point for document-related actions from the intent system.
    """
    if action == "generate_from_text":
        text = params.get("text", "")
        doc_type = params.get("type", "report")
        return generate_document_from_text(text, doc_type)
    
    elif action == "generate_meeting_summary":
        meeting_data = params.get("meeting_data", {})
        return generate_meeting_summary_document(meeting_data, chat_id)
    
    elif action == "share_document":
        document_id = params.get("document_id")
        message = params.get("message")
        return share_document_via_teams(document_id, chat_id, message)
    
    else:
        raise ValueError(f"Unknown document action: {action}")

from __future__ import annotations
import json, logging, os
from datetime import datetime
from typing import Dict, Any, List

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI

from common.memory_helpers import MemoryHelper
from common.graph_auth import get_access_token
from services.intent_api.email_agent import EmailAgent     # already exists

client  = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
log     = logging.getLogger("intent")
app     = FastAPI(title="AI-Employee • Intent handler")

# ─────────────────────────  Teams helpers  ──────────────────────────────
def _ms_graph(url: str, token: str, *, method: str = "GET",
              payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    r = requests.request(method, url,
                         headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": "application/json"},
                         json=payload, timeout=10)
    r.raise_for_status()
    return r.json() if r.text else {}

def _post_to_teams(chat_id: str, text: str, token: str) -> int:
    url  = f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages"
    body = {"body": {"contentType": "text", "content": text}}
    return requests.post(url, headers={"Authorization": f"Bearer {token}",
                                       "Content-Type": "application/json"},
                         json=body, timeout=10).status_code

# ─────────────────────────  Payload model  ──────────────────────────────
class TeamsWebhookPayload(BaseModel):
    messageId: str
    conversationId: str

# ─────────────────────────  Main webhook  ───────────────────────────────
@app.post("/webhook")
async def webhook_handler(payload: TeamsWebhookPayload):
    chat_id, msg_id = payload.conversationId, payload.messageId
    log.info("→ webhook chat=%s msg=%s", chat_id, msg_id)

    # 1) Token
    try:
        access_token, _ = get_access_token()
    except RuntimeError:
        raise HTTPException(401, "Run /auth/login first.")

    # 2) Fetch incoming text
    msg = _ms_graph(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages/{msg_id}",
        access_token
    )
    sender_name = (msg.get("from") or {}).get("user", {}).get("displayName", "")
    text        = (msg.get("body") or {}).get("content", "").strip()

    if sender_name == "BARA Software" or not text:
        return {"status": "skipped"}   # bot self-echo or empty

    # 3) Persist user turn
    MemoryHelper.save(chat_id, "user", text,
                      msg.get("chatType", None))

    # 4) Build context for GPT
    ctx_short  = MemoryHelper.last_messages(chat_id)
    ctx_global = MemoryHelper.global_slice()
    ctx_sem    = MemoryHelper.semantic(text, chat_id)

    messages: List[Dict[str, str]] = [
        {"role": "system",
         "content": (
            "You are an executive assistant.  "
            "Your ONLY job is to decide the user's intent and reply. "
            "Supported intents: send_email · book_meeting · reply. "
            "Return exactly ONE JSON object, no free text."
         )},
    ]
    def _add(ctx):                     # chronological
        for row in ctx:
            messages.append({"role": row["sender"], "content": row["content"]})

    _add(ctx_short)
    if ctx_sem:
        messages.append({"role": "system", "content": "🔎 Semantic context:"})
        _add(ctx_sem)
    if ctx_global:
        messages.append({"role": "system", "content": "🌐 Global context:"})
        _add(ctx_global)

    messages.append({"role": "user", "content": text})

    # 5) GPT – force JSON
    raw = client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},
        messages=messages,
        temperature=0.2,
        max_tokens=256
    ).choices[0].message.content

    parsed: Dict[str, Any] = json.loads(raw)
    intent = parsed.get("intent", "reply")
    reply  = parsed.get("reply", "").strip()

    # 6) Delegate to specialist agents
    if intent == "send_email":
        EmailAgent(MemoryHelper).execute(chat_id, parsed["emailDetails"])
    elif intent == "book_meeting":
        # from services.intent_api.booking_agent import BookingAgent
        # BookingAgent(MemoryHelper).execute(chat_id, parsed["meetingDetails"])
        reply = "✅ Meeting booked." if not reply else reply

    # 7) Persist assistant turn + reply to Teams
    MemoryHelper.save(chat_id, "assistant", reply)
    status_code = _post_to_teams(chat_id, reply, access_token)

    return {
        "status": "sent" if status_code == 201 else "graph_error",
        "intent": intent,
        "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
    }

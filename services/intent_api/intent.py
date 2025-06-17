# ─────────────────────────────────────────────────────────────────────────────
# services/intent_api/intent.py
"""
Teams webhook → detect intent → delegate.

•  GPT here **only** decides intent.
•  If intent == "send_email" we invoke email_agent.
      –  When email_agent raises **ValueError("missing …")** we
         immediately hand control to reply_agent so it can
         ask the user for whatever is missing.
•  All other situations → reply_agent.
"""
from __future__ import annotations
import json, logging, os
from datetime import datetime
from typing import Any, Dict, List

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI

# ───────── OpenAI ─────────
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ───────── Project helpers ─────────
from common.graph_auth import get_access_token
from common.memory_helpers import (
    save_message,
    fetch_chat_history,
    fetch_global_history,
    semantic_search,
)
from services.intent_api.email_agent import process_email_request
from services.intent_api.reply_agent import process_reply

# ───────── FastAPI basics ─────────
app = FastAPI(title="AI-Employee • intent detector")
logging.basicConfig(level=logging.INFO)


# ───────── MS Graph helper ─────────
def _ms_graph(url: str, token: str, *,
              method: str = "GET",
              payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    r = requests.request(
        method,
        url,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=payload,
        timeout=10,
    )
    r.raise_for_status()
    return r.json() if r.text else {}


# ───────── Webhook payload schema ─────────
class TeamsWebhookPayload(BaseModel):
    messageId: str
    conversationId: str


# ───────── Main webhook ─────────
@app.post("/webhook")
async def webhook_handler(payload: TeamsWebhookPayload):
    chat_id, msg_id = payload.conversationId, payload.messageId
    logging.info("→ webhook chat=%s msg=%s", chat_id, msg_id)

    # 1️⃣  Graph access token
    try:
        access_token, _ = get_access_token()
    except RuntimeError:
        raise HTTPException(401, "Run /auth/login once from a browser first.")

    # 2️⃣  Fetch incoming message
    msg = _ms_graph(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages/{msg_id}",
        access_token,
    )
    sender = (msg.get("from") or {}).get("user", {}).get("displayName", "Unknown")
    text   = (msg.get("body") or {}).get("content", "").strip()

    if sender == "BARA Software" or not text:
        return {"status": "skipped"}

    # 3️⃣  Chat type (analytics only)
    chat_type = _ms_graph(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}?$select=chatType",
        access_token,
    ).get("chatType", "unknown")

    # 4️⃣  Persist user turn
    save_message(chat_id, "user", text, chat_type)

    # 5️⃣  Build context for intent classifier
    chat_mem     = fetch_chat_history(chat_id, limit=30)
    global_mem   = fetch_global_history(limit=8)
    semantic_mem = semantic_search(text, chat_id, k_chat=8, k_global=4)

    def _append(dst: List[Dict[str, str]], rows):
        for r in rows:
            dst.append({
                "role": "user" if r["sender"] == "user" else "assistant",
                "content": r["content"],
            })

    messages: List[Dict[str, str]] = [{
        "role": "system",
        "content": (
            "Your ONLY task is to classify the user's intent.\n\n"
            "Intents:\n"
            "• send_email  – user clearly wants an Outlook e-mail sent\n"
            "• reply       – everything else (questions, confirmations…)\n\n"
            "Return ONE JSON exactly: {\"intent\":\"send_email\"} or {\"intent\":\"reply\"}.\n"
            "NO additional keys, comments, or text."
        ),
    }]
    _append(messages, chat_mem)
    if semantic_mem:
        messages.append({"role": "system", "content": "🔎 Relevant context:"})
        _append(messages, semantic_mem)
    if global_mem:
        messages.append({"role": "system", "content": "🌐 Other chats context:"})
        _append(messages, global_mem)
    messages.append({"role": "user", "content": text})
    messages.append({"role": "system",
                     "content": "Output strictly one JSON with key 'intent' and nothing else."})

    intent_json = json.loads(
        client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=messages,
        ).choices[0].message.content
    )
    intent = intent_json.get("intent", "reply")
    logging.info("≈ intent → %s", intent)

    # 6️⃣  Delegate according to intent
    email_agent_called = False
    reply_agent_called = False

    if intent == "send_email":
        try:
            process_email_request(chat_id)
            email_agent_called = True
            logging.info("✓ email_agent completed")
        except ValueError as exc:            # missing details
            logging.warning("email_agent needs info → %s", exc)
            # Let reply_agent ask the user for what’s missing.
            process_reply(chat_id, text)     # reuse last user turn
            reply_agent_called = True
    else:
        process_reply(chat_id, text)
        reply_agent_called = True

    return {
        "status":        "ok",
        "intent":        intent,
        "email_agent":   email_agent_called,
        "reply_agent":   reply_agent_called,
        "timestamp":     datetime.utcnow().isoformat(timespec="seconds"),
    }

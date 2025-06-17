# ─────────────────────────────────────────────────────────────────────────────
# services/intent_api/intent.py
"""
Teams webhook → detect intent → delegate to email_agent or reply_agent.

• intent == "send_email"  → email_agent.process_email_request()
      └─ email_agent returns {"status":"sent"}  → done
                         or {"status":"missing","missing":"subject"}
           → reply_agent.process_reply(..., missing_info="subject")

• intent == "reply"       → reply_agent.process_reply()
"""
from __future__ import annotations
import json, logging, os, requests
from datetime import datetime
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI

# ───────── OpenAI ─────────
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ───────── Helpers & deps ─────────
from common.graph_auth import get_access_token
from common.memory_helpers import (
    save_message,
    fetch_chat_history,
    fetch_global_history,
    semantic_search,
)
from services.intent_api.email_agent import process_email_request
from services.intent_api.reply_agent import process_reply

app = FastAPI(title="AI-Employee • intent detector")
logging.basicConfig(level=logging.INFO)


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


class TeamsWebhookPayload(BaseModel):
    messageId: str
    conversationId: str


@app.post("/webhook")
async def webhook_handler(payload: TeamsWebhookPayload):
    chat_id, msg_id = payload.conversationId, payload.messageId
    logging.info("→ webhook chat=%s msg=%s", chat_id, msg_id)

    # 1️⃣  MS Graph token
    try:
        access_token, _ = get_access_token()
    except RuntimeError:
        raise HTTPException(401, "Run /auth/login once from a browser first.")

    # 2️⃣  Incoming message
    msg = _graph(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages/{msg_id}",
        access_token,
    )
    sender = (msg.get("from") or {}).get("user", {}).get("displayName", "Unknown")
    text   = (msg.get("body") or {}).get("content", "").strip()

    if sender == "BARA Software" or not text:
        return {"status": "skipped"}

    chat_type = _graph(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}?$select=chatType",
        access_token,
    ).get("chatType", "unknown")

    save_message(chat_id, "user", text, chat_type)

    # 3️⃣  Build context for intent classifier
    chat_mem     = fetch_chat_history(chat_id, 30)
    global_mem   = fetch_global_history(8)
    semantic_mem = semantic_search(text, chat_id, 8, 4)

    def _add(dst: List[Dict[str, str]], rows):
        for r in rows:
            dst.append({"role": "user" if r["sender"] == "user" else "assistant",
                        "content": r["content"]})

    msgs: List[Dict[str, str]] = [{
        "role": "system",
        "content": (
            "Classify the user's intent. Two options only:\n"
            "• send_email – user wants an Outlook e-mail sent now or soon\n"
            "• reply      – any other request\n\n"
            "Return ONE JSON exactly {\"intent\":\"send_email\"} or {\"intent\":\"reply\"}."
        ),
    }]
    _add(msgs, chat_mem)
    if semantic_mem:
        msgs.append({"role": "system", "content": "🔎 Relevant context:"})
        _add(msgs, semantic_mem)
    if global_mem:
        msgs.append({"role": "system", "content": "🌐 Other chats context:"})
        _add(msgs, global_mem)
    msgs.append({"role": "user", "content": text})
    msgs.append({"role": "system",
                 "content": "Output strictly one JSON object with key intent."})

    intent = json.loads(
        client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=msgs,
        ).choices[0].message.content
    ).get("intent", "reply")

    # 4️⃣  Delegate
    if intent == "send_email":
        result = process_email_request(chat_id)
        if result["status"] == "missing":
            process_reply(chat_id, text, missing_info=result["missing"])
    else:
        process_reply(chat_id, text)

    return {
        "status": "ok",
        "intent": intent,
        "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
    }

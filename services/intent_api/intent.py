"""
FastAPI webhook that receives Power-Automate payloads, chats as **John**
(a corporate lawyer), stores memory in Supabase (incl. chatType),
does pgvector recall and can draft / send Outlook e-mails.
"""
from __future__ import annotations

import json, logging, os
from datetime import datetime
from typing import Any, Dict, List

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from openai import OpenAI
from pydantic import BaseModel

# ─────────────────────────── OpenAI ──────────────────────────────────────
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ─────────────────────────── Project helpers ────────────────────────────
from common.graph_auth import get_msal_app, exchange_code_for_tokens, get_access_token
from common.memory_helpers import (
    save_message,
    fetch_chat_history,
    fetch_global_history,
    semantic_search,     # ← now hybrid + scoped
)
from services.intent_api.email_agent import process_email_request

# ─────────────────────────── FastAPI basics ─────────────────────────────
REDIRECT_URI = "https://ai-employee-28l9.onrender.com/auth/callback"
app = FastAPI(title="AI-Employee • intent handler")
logging.basicConfig(level=logging.INFO)

# ─────────────────────────── Utility helpers  ───────────────────────────
def _ms_graph(url: str, token: str, *, method: str = "GET", payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    resp = requests.request(
        method,
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload, timeout=10,
    )
    resp.raise_for_status()
    return resp.json() if resp.text else {}

def _send_teams_reply(chat_id: str, message: str, token: str) -> int:
    url = f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages"
    body = {"body": {"contentType": "text", "content": message}}
    return requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body, timeout=10,
    ).status_code

# ─────────────────────────── Pydantic model ─────────────────────────────
class TeamsWebhookPayload(BaseModel):
    messageId: str
    conversationId: str

# ─────────────────────────── Auth endpoints (unchanged) ─────────────────
# … (same /auth/login and /auth/callback handlers) …

# ─────────────────────────── Main webhook  ──────────────────────────────
@app.post("/webhook")
async def webhook_handler(payload: TeamsWebhookPayload):
    chat_id, msg_id = payload.conversationId, payload.messageId
    logging.info("→ webhook chat=%s msg=%s", chat_id, msg_id)

    # 1️⃣  Graph token ----------------------------------------------------
    try:
        access_token, _ = get_access_token()
    except RuntimeError:
        raise HTTPException(401, "Run /auth/login once from a browser first.")

    # 2️⃣  chatType -------------------------------------------------------
    chat_type = _ms_graph(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}?$select=chatType",
        access_token,
    ).get("chatType", "unknown")

    # 3️⃣  Full message ---------------------------------------------------
    msg = _ms_graph(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages/{msg_id}",
        access_token,
    )
    sender = (msg.get("from") or {}).get("user", {}).get("displayName", "Unknown")
    text   = (msg.get("body") or {}).get("content", "").strip()

    #   Skip our own bot or blank lines
    if sender == "BARA Software" or not text:
        return {"status": "skipped"}

    # 4️⃣  Persist user turn ---------------------------------------------
    save_message(chat_id, "user", text, chat_type)

    # 5️⃣  Tier-1 memory (larger slices) ---------------------------------
    chat_mem   = fetch_chat_history(chat_id, limit=30)
    global_mem = fetch_global_history(limit=8)

    # 6️⃣  Tier-2 semantic recall (always) -------------------------------
    semantic_mem = semantic_search(text, chat_id, k_chat=8, k_global=4)

    # 7️⃣  Build GPT context ---------------------------------------------
    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are **John**, an experienced corporate lawyer. "
                "Reply formally, concisely and avoid unnecessary jargon."
            ),
        }
    ]

    def _append(rows: List[Dict[str, str]]):
        for row in rows:
            messages.append(
                {
                    "role": "user" if row["sender"] == "user" else "assistant",
                    "content": row["content"],
                }
            )

    _append(chat_mem)
    if semantic_mem:
        messages.append({"role": "system", "content": "🔎 Relevant context:"})
        _append(semantic_mem)
    if global_mem:
        messages.append({"role": "system", "content": "🌐 Other chats context:"})
        _append(global_mem)

    messages.append({"role": "user", "content": text})

    # 8️⃣  Generate structured response ----------------------------------
    schema = {
        "role": "system",
        "content": (
            "Return ONE **json** object only.\n\n"
            'E-mail draft → {"intent":"send_email","reply":"…","emailDetails":{…}}\n'
            'Normal reply → {"intent":"reply","reply":"…"}\n'
            "Never invent e-mail addresses – ask the user."
        ),
    }

    parsed: Dict[str, Any] = json.loads(
        client.chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=messages + [schema],
        ).choices[0].message.content
    )

    intent = parsed.get("intent", "reply")
    reply  = parsed.get("reply", "").strip()

    # 9️⃣  Handle intents (unchanged) ------------------------------------
    sent_ok = False
    if intent == "send_email":
        try:
            process_email_request(parsed["emailDetails"])
            sent_ok = True
            logging.info("✓ Outlook e-mail sent")
        except Exception as exc:
            logging.exception("E-mail send failed")
            reply = f"⚠️ I couldn’t send the e-mail: {exc}"

    # 🔟  Persist assistant turn & push to Teams -------------------------
    save_message(chat_id, "assistant", reply, chat_type)
    status = _send_teams_reply(chat_id, reply, access_token)

    return {
        "status": "sent" if status == 201 else "graph_error",
        "chat_type": chat_type,
        "intent": intent,
        "email_sent": sent_ok,
        "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
    }

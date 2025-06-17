from __future__ import annotations

import json, logging, os
from datetime import datetime
from typing import Any, Dict, List

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI

# ─────────────────────────── OpenAI ──────────────────────────────────────
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ─────────────────────────── Project helpers ────────────────────────────
from common.graph_auth import get_msal_app, exchange_code_for_tokens, get_access_token
from common.memory_helpers import (
    save_message,
    fetch_chat_history,
    fetch_global_history,
    semantic_search,
    # fetch_contacts_for_chat, # (optional: your own contacts DB, see note below)
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

    # 7️⃣  Gather all known contacts from context (optional: from DB) ----
    known_contacts = []
    # Example: fetch_contacts_for_chat(chat_id) if you have it
    # known_contacts = fetch_contacts_for_chat(chat_id)  # [{'name': 'Maxime', 'email': 'maximegermain3@gmail.com'}]

    # You could also extract possible email/name pairs from chat_mem/global_mem if not using a DB.

    contacts_str = ""
    if known_contacts:
        contacts_str = "Known contacts:\n" + "\n".join(f"{c['name']} <{c['email']}>" for c in known_contacts)

    # 8️⃣  Build GPT context ---------------------------------------------
    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are John, an executive assistant and corporate lawyer. "
                "You always use all available prior chat memory and known contact info to act. "
                "If a user asks you to send an email, search all available context for the real email address, subject, and body. "
                "If all details are present, respond ONLY as:\n"
                '{"intent":"send_email","reply":"The email has been sent.","emailDetails":{"to":["recipient@example.com"],"subject":"...","body":"..."}}\n'
                "If any required detail (recipient email, subject, body) is missing after searching ALL context, "
                "respond ONLY with:\n"
                '{"intent":"reply","reply":"Please provide the missing info (recipient email, subject, or body)."}\n'
                "If multiple contacts match, reply: "
                '{"intent":"reply","reply":"Multiple contacts found for Maxime. Please clarify."}\n'
                "NEVER return a draft or plain text email. NEVER invent or hallucinate emails. "
                "Be bold, proactive, and reliable."
                + ("\n\n" + contacts_str if contacts_str else "")
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

    # 9️⃣  Advanced schema to force JSON structure -----------------------
    schema = {
        "role": "system",
        "content": (
            "Return ONE JSON object only. "
            'E-mail send: {"intent":"send_email","reply":"…","emailDetails":{...}}. '
            'Missing info: {"intent":"reply","reply":"Please provide the missing info (recipient email, subject, or body)."} '
            "Never return a draft or any text outside this JSON structure. Never apologize."
        ),
    }

    messages.append(schema)

    # 🔟  Call OpenAI + parse JSON only ----------------------------------
    parsed: Dict[str, Any] = json.loads(
        client.chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=messages,
        ).choices[0].message.content
    )

    intent = parsed.get("intent", "reply")
    reply  = parsed.get("reply", "").strip()

    # 1️⃣1️⃣  Handle intents (unchanged) ---------------------------------
    sent_ok = False
    if intent == "send_email":
        try:
            process_email_request(parsed["emailDetails"])
            sent_ok = True
            logging.info("✓ Outlook e-mail sent")
        except Exception as exc:
            logging.exception("E-mail send failed")
            reply = f"⚠️ I couldn’t send the e-mail: {exc}"

    # 1️⃣2️⃣  Persist assistant turn & push to Teams ---------------------
    save_message(chat_id, "assistant", reply, chat_type)
    status = _send_teams_reply(chat_id, reply, access_token)

    return {
        "status": "sent" if status == 201 else "graph_error",
        "chat_type": chat_type,
        "intent": intent,
        "email_sent": sent_ok,
        "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
    }

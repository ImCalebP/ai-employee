# ─────────────────────────────────────────────────────────────────────────────
# services/intent_api/intent.py
"""
FastAPI webhook that receives Power-Automate payloads, chats as **John**
(a corporate lawyer), stores memory in Supabase (incl. chatType),
does pgvector recall and can draft / send Outlook e-mails.

• One-time login  →  GET /auth/login   (seeds refresh-token)
• Silent refresh  →  handled inside common.graph_auth
"""
from __future__ import annotations

import json
import logging
import os
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
    save_message,            # expects (chat_id, sender, content, chat_type)
    fetch_chat_history,
    fetch_global_history,
    semantic_search,
)
from services.intent_api.email_agent import process_email_request

# ─────────────────────────── FastAPI basics ─────────────────────────────
REDIRECT_URI = "https://ai-employee-28l9.onrender.com/auth/callback"

app = FastAPI(title="AI-Employee • intent handler")
logging.basicConfig(level=logging.INFO)

# ─────────────────────────── Tiny Graph helpers ─────────────────────────
def _ms_graph(
    url: str,
    token: str,
    *,
    method: str = "GET",
    payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Minimal wrapper around MS Graph; raises on non-2xx."""
    resp = requests.request(
        method,
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json() if resp.text else {}


def _send_teams_reply(chat_id: str, message: str, token: str) -> int:
    url = f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages"
    body = {"body": {"contentType": "text", "content": message}}
    return requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=10,
    ).status_code


# ─────────────────────────── Pydantic model ─────────────────────────────
class TeamsWebhookPayload(BaseModel):
    messageId: str
    conversationId: str


# ─────────────────────────── Auth endpoints ─────────────────────────────
@app.get("/auth/login")
def auth_login() -> RedirectResponse:
    url = get_msal_app().get_authorization_request_url(
        scopes=["Chat.ReadWrite"],
        redirect_uri=REDIRECT_URI,
        state="ai-login",
        prompt="login",
    )
    return RedirectResponse(url)


@app.get("/auth/callback")
def auth_callback(code: str | None = None, error: str | None = None) -> HTMLResponse:
    if error:
        raise HTTPException(400, f"Azure AD error: {error}")
    try:
        exchange_code_for_tokens(code, REDIRECT_URI)
    except Exception as exc:  # noqa: BLE001
        logging.exception("Token exchange failed")
        raise HTTPException(400, f"Token exchange failed: {exc}") from exc
    return HTMLResponse("<h2>✅ Login successful — you can close this tab.</h2>")


# ─────────────────────────── Main webhook ───────────────────────────────
@app.post("/webhook")
async def webhook_handler(payload: TeamsWebhookPayload):
    chat_id, msg_id = payload.conversationId, payload.messageId
    logging.info("→ webhook chat=%s msg=%s", chat_id, msg_id)

    # 1️⃣  Graph token
    try:
        access_token, _ = get_access_token()
    except RuntimeError:
        raise HTTPException(401, "Run /auth/login once from a browser first.")

    # 2️⃣  chatType (oneOnOne / group / meeting / unknown)
    chat_type = _ms_graph(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}?$select=chatType",
        access_token,
    ).get("chatType", "unknown")

    # 3️⃣  Full message
    msg = _ms_graph(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages/{msg_id}",
        access_token,
    )
    sender = (msg.get("from") or {}).get("user", {}).get("displayName", "Unknown")
    text   = (msg.get("body") or {}).get("content", "").strip()

    #   Skip our own bot or blank lines
    if sender == "BARA Software" or not text:
        return {"status": "skipped"}

    # 4️⃣  Persist user turn
    save_message(chat_id, "user", text, chat_type)

    # 5️⃣  Tier-1 memory
    chat_mem   = fetch_chat_history(chat_id, limit=15)
    global_mem = fetch_global_history(limit=5)

    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are **John**, an experienced corporate lawyer. "
                "Reply formally, concisely and avoid unnecessary jargon."
            ),
        }
    ]
    for row in chat_mem:
        messages.append(
            {
                "role": "user" if row["sender"] == "user" else "assistant",
                "content": row["content"],
            }
        )
    if global_mem:
        messages.append({"role": "system", "content": "Context from other chats:"})
        for row in global_mem:
            messages.append(
                {
                    "role": "user" if row["sender"] == "user" else "assistant",
                    "content": row["content"],
                }
            )
    messages.append({"role": "user", "content": text})

    # 6️⃣  Need extra memory?
    need_prompt = {
        "role": "system",
        "content": (
            'Respond ONLY with a json object: {"need_memory":true} '
            'or {"need_memory":false}.'
        ),
    }
    need_more = json.loads(
        client.chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=messages + [need_prompt],
        ).choices[0].message.content
    ).get("need_memory", False)

    if need_more:
        for row in semantic_search(text, k=5):
            messages.append(
                {
                    "role": "user" if row["sender"] == "user" else "assistant",
                    "content": row["content"],
                }
            )

    # 7️⃣  Final structured response
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

    # 8️⃣  Handle intents
    sent_ok = False
    if intent == "send_email":
        try:
            process_email_request(parsed["emailDetails"])
            sent_ok = True
            logging.info("✓ Outlook e-mail sent")
        except Exception as exc:  # noqa: BLE001
            logging.exception("E-mail send failed")
            reply = f"⚠️ I couldn’t send the e-mail: {exc}"

    # 9️⃣  Persist assistant turn & push to Teams
    save_message(chat_id, "assistant", reply, chat_type)
    status = _send_teams_reply(chat_id, reply, access_token)

    return {
        "status": "sent" if status == 201 else "graph_error",
        "chat_type": chat_type,
        "intent": intent,
        "email_sent": sent_ok,
        "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
    }

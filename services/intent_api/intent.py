# ─────────────────────────────────────────────────────────────────────────────
# services/intent_api/intent.py
"""
FastAPI webhook that receives Power-Automate payloads, chats as **John**
(a professional corporate lawyer), stores memory in Supabase, recalls extra
context with pgvector **and** can send Outlook e-mails when the LLM tells it to.

• One-time interactive login:  GET  /auth/login   (stores encrypted refresh-token)
• Graph access-token refresh:  automatic via common.graph_auth
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from openai import OpenAI
from pydantic import BaseModel

# ─────────────────────────  OpenAI / model  ──────────────────────────────
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ─────────────────────────  Graph auth helpers  ──────────────────────────
from common.graph_auth import (
    get_msal_app,
    exchange_code_for_tokens,
    get_access_token,
)

# ─────────────────────────  Memory helpers  ─────────────────────────────
from common.memory_helpers import (
    save_message,
    fetch_chat_history,
    fetch_global_history,
    semantic_search,
)

# ─────────────────────────  E-mail helper  ───────────────────────────────
from services.intent_api.email_agent import process_email_request  # new

# ─────────────────────────  FastAPI basics  ─────────────────────────────
REDIRECT_URI = "https://ai-employee-28l9.onrender.com/auth/callback"

app = FastAPI(title="AI-Employee • intent handler")
logging.basicConfig(level=logging.INFO)


# ─────────────────────────  Auth endpoints  ─────────────────────────────
@app.get("/auth/login")
def auth_login() -> RedirectResponse:
    """Run ONCE manually – stores an encrypted refresh-token."""
    url = get_msal_app().get_authorization_request_url(
        scopes=["Chat.ReadWrite"],  # MSAL auto-adds openid profile offline_access
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


# ─────────────────────────  Teams helpers  ──────────────────────────────
def send_teams_reply(chat_id: str, message: str, token: str) -> tuple[int, str]:
    resp = requests.post(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "body": {
                "contentType": "text",
                "content": message,
            }
        },
        timeout=10,
    )
    return resp.status_code, resp.text


# ─────────────────────────  Webhook model  ──────────────────────────────
class TeamsWebhookPayload(BaseModel):
    messageId: str
    conversationId: str


# ─────────────────────────  Main webhook  ───────────────────────────────
@app.post("/webhook")
async def webhook_handler(payload: TeamsWebhookPayload):
    chat_id, msg_id = payload.conversationId, payload.messageId
    logging.info("→ webhook chat=%s msg=%s", chat_id, msg_id)

    # ─── 1. Graph token ────────────────────────────────────────────────
    try:
        access_token, _ = get_access_token()
    except RuntimeError:
        raise HTTPException(
            401,
            "Refresh-token missing. Run /auth/login once in a browser and sign in.",
        )

    # ─── 2. Fetch the full Teams message ───────────────────────────────
    ms_resp = requests.get(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages/{msg_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    ms_resp.raise_for_status()
    msg = ms_resp.json()

    sender = (msg.get("from") or {}).get("user", {}).get("displayName", "Unknown")
    text = (msg.get("body") or {}).get("content", "").strip()

    # ignore our own bot / blank
    if sender == "BARA Software" or not text:
        return {"status": "skipped"}

    # ─── 3. Persist user turn  ──────────────────────────────────────────
    save_message(chat_id, "user", text)

    # ─── 4. Tier-1 memory  ──────────────────────────────────────────────
    chat_mem   = fetch_chat_history(chat_id, limit=10)
    global_mem = fetch_global_history(limit=5)

    base_messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You are **John**, a highly experienced corporate lawyer at a large "
                "firm. Respond formally, confidently and concisely, avoiding "
                "unnecessary jargon."
            ),
        }
    ]

    # recent chat history
    for row in chat_mem:
        role = "user" if row["sender"] == "user" else "assistant"
        base_messages.append({"role": role, "content": row["content"]})

    # thin global slice
    if global_mem:
        base_messages.append(
            {"role": "system", "content": "Context from other recent chats:"}
        )
        for row in global_mem:
            role = "user" if row["sender"] == "user" else "assistant"
            base_messages.append({"role": role, "content": row["content"]})

    # user’s new message
    base_messages.append({"role": "user", "content": text})

    # ─── 5. Pass-1: ask if we need more memory  ─────────────────────────
    need_prompt = {
        "role": "system",
        "content": (
            "If you still need earlier context OUTSIDE what you see, "
            'reply ONLY with JSON: {"need_memory": true, "reason": "..."} '
            'Otherwise reply ONLY with JSON: {"need_memory": false}'
        ),
    }

    first = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=base_messages + [need_prompt],
    ).choices[0].message

    need_memory = False
    try:
        need_memory = json.loads(first.content).get("need_memory", False)
    except Exception:  # noqa: BLE001
        pass  # weird answer → assume no extra memory

    # ─── 6. Tier-2 recall if requested ──────────────────────────────────
    extra_memory: list[dict[str, str]] = []
    if need_memory:
        matches = semantic_search(text, k=5)
        if matches:
            extra_memory.append({"role": "system", "content": "Additional memories:"})
            for row in matches:
                role = "user" if row["sender"] == "user" else "assistant"
                extra_memory.append({"role": role, "content": row["content"]})

    # ─── 7. Pass-2: final structured answer  ────────────────────────────
    format_instruction = {
        "role": "system",
        "content": (
            "When you answer, output **ONLY a single JSON object**.\n\n"
            "If the user wants you to write & send an e-mail, use:\n"
            '{\n'
            '  "intent": "send_email",\n'
            '  "reply":   "What you will tell the user back in Teams",\n'
            '  "emailDetails": {\n'
            '    "to":     ["a@example.com", "b@example.com"],\n'
            '    "subject":"...",\n'
            '    "body":   "..."  \n'
            '  }\n'
            '}\n\n'
            "If you’re just replying normally, use:\n"
            '{\n'
            '  "intent": "reply",\n'
            '  "reply":  "Your message here"\n'
            '}\n\n'
            "NEVER invent unknown e-mail addresses – ask the user instead."
        ),
    }

    final_resp = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=base_messages + extra_memory + [format_instruction],
    ).choices[0].message

    # ─── 8. Parse & act  ────────────────────────────────────────────────
    try:
        parsed: dict[str, Any] = json.loads(final_resp.content)
    except json.JSONDecodeError:
        parsed = {"intent": "reply", "reply": final_resp.content.strip()}

    intent  = parsed.get("intent", "reply")
    reply   = parsed.get("reply", "").strip()
    sent_ok = False

    if intent == "send_email":
        try:
            process_email_request(parsed["emailDetails"])  # may raise
            sent_ok = True
            logging.info("✓ Outlook e-mail sent")
        except Exception as exc:  # noqa: BLE001
            # tell the user what went wrong
            reply = f"⚠️ I couldn’t send the e-mail: {exc}"
            logging.exception("Email sending failed")

    # ─── 9. Persist assistant turn & send back to Teams  ────────────────
    save_message(chat_id, "assistant", reply)
    status, body = send_teams_reply(chat_id, reply, access_token)

    return {
        "status": "sent" if status == 201 else f"graph_error:{body}",
        "intent": intent,
        "email_sent": sent_ok,
        "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
    }

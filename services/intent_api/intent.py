# services/intent_api/intent.py
"""
FastAPI webhook that:
• receives Power-Automate JSON payloads
• fetches the full Teams message
• stores user / assistant turns + embeddings in Supabase
• uses tier-1 (chat + global) memory first
• falls back to pgvector similarity search when GPT says it needs it
• answers as “John”, a professional corporate lawyer
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from openai import OpenAI
from pydantic import BaseModel

# ────────────────────────────────────────────────────────────────────────
#  Initialise deps
# ────────────────────────────────────────────────────────────────────────
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 1️⃣  Azure-AD helpers (delegated auth)
from common.graph_auth import (
    get_msal_app,
    exchange_code_for_tokens,
    get_access_token,
)

# 2️⃣  Memory helpers (Supabase + embeddings)
from common.memory_helpers import (
    save_message,
    fetch_chat_history,
    fetch_global_history,
    semantic_search,
)

# ────────────────────────────────────────────────────────────────────────
REDIRECT_URI = "https://ai-employee-28l9.onrender.com/auth/callback"

app = FastAPI(title="AI-Employee  • tier-2 recall")
logging.basicConfig(level=logging.INFO)

# ───────────────────────────  Auth endpoints  ───────────────────────────


@app.get("/auth/login")
def auth_login() -> RedirectResponse:
    """
    One-time manual login.
    Visit /auth/login, sign in **once** as info@barasoftware.com,
    refresh-token is stored → service can reply forever after.
    """
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
    except Exception as exc:
        logging.exception("Token exchange failed")
        raise HTTPException(400, f"Token exchange failed: {exc}") from exc
    return HTMLResponse("<h2>✅ Login successful — you can close this tab.</h2>")


# ───────────────────────────  Teams helpers  ────────────────────────────
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


# ───────────────────────────  Webhook model  ────────────────────────────
class TeamsWebhookPayload(BaseModel):
    messageId: str
    conversationId: str


# ───────────────────────────  Main webhook  ─────────────────────────────
@app.post("/webhook")
async def webhook_handler(payload: TeamsWebhookPayload):
    chat_id, msg_id = payload.conversationId, payload.messageId
    logging.info("→ webhook chat=%s msg=%s", chat_id, msg_id)

    # ---- 1. Graph token -------------------------------------------------
    try:
        access_token, _ = get_access_token()
    except RuntimeError:
        raise HTTPException(
            401,
            "Refresh-token missing. Run /auth/login once in a browser and sign in.",
        )

    # ---- 2. Fetch the full Teams message -------------------------------
    ms_resp = requests.get(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages/{msg_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    ms_resp.raise_for_status()
    msg = ms_resp.json()

    sender = (msg.get("from") or {}).get("user", {}).get("displayName", "Unknown")
    text = (msg.get("body") or {}).get("content", "").strip()

    # Skip our own bot or blank messages
    if sender == "BARA Software" or not text:
        return {"status": "skipped"}

    # ---- 3. Persist the user message (embedding + row) ------------------
    save_message(chat_id, "user", text)

    # ---- 4. Tier-1 memory (chat + small global slice) -------------------
    chat_mem = fetch_chat_history(chat_id, limit=10)
    global_mem = fetch_global_history(limit=5)

    messages: list[dict] = [
        {
            "role": "system",
            "content": (
                "You are **John**, a highly experienced corporate lawyer. "
                "Respond formally, confidently and concisely, avoiding unnecessary jargon."
            ),
        }
    ]

    # past chat
    for row in chat_mem:
        role = "user" if row["sender"] == "user" else "assistant"
        messages.append({"role": role, "content": row["content"]})

    # thin global slice
    if global_mem:
        messages.append(
            {
                "role": "system",
                "content": "Snippets from other recent conversations for context:",
            }
        )
        for row in global_mem:
            role = "user" if row["sender"] == "user" else "assistant"
            messages.append({"role": role, "content": row["content"]})

    messages.append({"role": "user", "content": text})

    # ---- 5. First GPT pass – ask if it needs more memory ---------------
    need_prompt = {
        "role": "system",
        "content": (
            "If you still need earlier context OUTSIDE what you see, "
            'reply ONLY with JSON: {"need_memory": true, "reason": "..."} . '
            'Otherwise reply ONLY with JSON: {"need_memory": false, "answer": "..."}'
        ),
    }

    first = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=messages + [need_prompt],
    ).choices[0].message

    need_memory = False
    answer_txt = ""

    try:
        parsed = json.loads(first.content)  # ← .content is the JSON string
        need_memory = parsed.get("need_memory", False)
        answer_txt = parsed.get("answer", "").strip()
    except json.JSONDecodeError:
        # Model decided to ignore the schema → treat as final answer
        answer_txt = first.content.strip()

    # ---- 6. Tier-2 recall if requested ---------------------------------
    if need_memory:
        matches = semantic_search(text, k=5)
        if matches:
            mem_msgs = [{"role": "system", "content": "Additional memories:"}]
            for row in matches:
                role = "user" if row["sender"] == "user" else "assistant"
                mem_msgs.append({"role": role, "content": row["content"]})

            answer_txt = (
                client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=messages + mem_msgs + [{"role": "user", "content": text}],
                )
                .choices[0]
                .message.content.strip()
            )

    # ---- 7. Store assistant reply & push to Teams ----------------------
    save_message(chat_id, "assistant", answer_txt)
    status, _ = send_teams_reply(chat_id, answer_txt, access_token)

    return {
        "status": "sent" if status == 201 else "error",
        "answer": answer_txt,
        "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
    }

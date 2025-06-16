# services/intent_api/intent.py
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel
from openai import OpenAI
import os, requests, logging
from datetime import datetime

# ───── OpenAI ────────────────────────────────────────────────────────────
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ───── MSAL utilities ────────────────────────────────────────────────────
from common.graph_auth import (
    get_msal_app,
    exchange_code_for_tokens,
    get_access_token,
)

# ───── Supabase client for chat memory ───────────────────────────────────
from common.supabase import supabase

REDIRECT_URI = "https://ai-employee-28l9.onrender.com/auth/callback"

app = FastAPI(title="AI-Employee with Supabase memory")
logging.basicConfig(level=logging.INFO)


# ───────────────────────── auth helpers (one-time) ───────────────────────
@app.get("/auth/login")
def auth_login():
    auth_url = get_msal_app().get_authorization_request_url(
        scopes=["Chat.ReadWrite"],
        redirect_uri=REDIRECT_URI,
        state="ai-login",
        prompt="login",
    )
    return RedirectResponse(auth_url)


@app.get("/auth/callback")
def auth_callback(code: str | None = None, error: str | None = None):
    if error:
        raise HTTPException(400, f"Azure AD error: {error}")
    try:
        exchange_code_for_tokens(code, REDIRECT_URI)
    except Exception as e:
        logging.exception("Token exchange failed")
        raise HTTPException(400, f"Token exchange failed: {e}")
    return HTMLResponse("<h2>✅ Login successful — you can close this tab.</h2>")


# ────────────────────────── Teams & memory helpers ───────────────────────
def send_teams_reply(chat_id: str, message: str, token: str):
    url = f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"body": {"contentType": "text", "content": message}},
        timeout=10,
    )
    return resp.status_code, resp.text


def save_message(chat_id: str, sender: str, content: str):
    supabase.table("message_history").insert(
        {"chat_id": chat_id, "sender": sender, "content": content}
    ).execute()


def fetch_recent_history(chat_id: str, limit: int = 10):
    rows = (
        supabase.table("message_history")
        .select("sender,content")
        .eq("chat_id", chat_id)
        .order("timestamp", desc=False)
        .limit(limit)
        .execute()
    )
    return rows.data or []


# ───────────────────────── webhook schema & route ────────────────────────
class TeamsWebhookPayload(BaseModel):
    messageId: str
    conversationId: str


@app.post("/webhook")
async def webhook_handler(payload: TeamsWebhookPayload):
    logging.info("[Webhook] %s / %s", payload.conversationId, payload.messageId)

    # access-token
    try:
        access_token, _ = get_access_token()
    except RuntimeError:
        raise HTTPException(
            401,
            "No refresh token stored. Visit /auth/login once and sign in.",
        )

    # fetch full Teams message
    msg_url = (
        f"https://graph.microsoft.com/v1.0/chats/"
        f"{payload.conversationId}/messages/{payload.messageId}"
    )
    r = requests.get(msg_url, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
    r.raise_for_status()
    msg_data = r.json()

    # robust sender extraction
    sender_name = (
        msg_data.get("from") or {}
    ).get("user", {}).get("displayName", "") or "Unknown"

    if sender_name == "BARA Software":
        return {"status": "skipped", "reason": "self message"}

    user_message = (msg_data.get("body") or {}).get("content", "").strip()
    if not user_message:
        return {"status": "skipped", "reason": "empty message"}

    # save user message
    save_message(payload.conversationId, "user", user_message)

    # pull recent memory
    history = fetch_recent_history(payload.conversationId)

    # build context
    messages = [
        {
            "role": "system",
            "content": (
                "You are **John**, a professional corporate lawyer with excellent "
                "communication skills. Reply formally, confidently and concisely."
            ),
        }
    ]
    for row in history:
        role = "user" if row["sender"] == "user" else "assistant"
        messages.append({"role": role, "content": row["content"]})
    messages.append({"role": "user", "content": user_message})

    # GPT-4
    chat = client.chat.completions.create(model="gpt-4", messages=messages)
    reply = chat.choices[0].message.content.strip()

    # save assistant reply
    save_message(payload.conversationId, "assistant", reply)

    # send to Teams
    status, ms_result = send_teams_reply(payload.conversationId, reply, access_token)

    return {
        "status": "sent" if status == 201 else "error",
        "http_status": status,
        "ai_reply": reply,
        "graph_response": ms_result,
        "timestamp": datetime.utcnow().isoformat(),
    }

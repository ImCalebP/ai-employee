# services/intent_api/intent.py
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel
from openai import OpenAI

import os, requests, logging
from datetime import datetime

# ───── OpenAI ────────────────────────────────────────────────────────────
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ───── MSAL helper utilities (delegated flow) ────────────────────────────
from common.graph_auth import (
    get_msal_app,
    exchange_code_for_tokens,
    get_access_token,
)

# ───── Supabase client (memory) ──────────────────────────────────────────
from common.supabase import supabase

REDIRECT_URI = "https://ai-employee-28l9.onrender.com/auth/callback"

app = FastAPI(title="AI-Employee intent API (delegated auth + memory)")
logging.basicConfig(level=logging.INFO)


# ─────────────────────────────────────────────────────────────────────────
#  Auth endpoints ─ only needed once
# ─────────────────────────────────────────────────────────────────────────
@app.get("/auth/login")
def auth_login():
    msal_app = get_msal_app()
    auth_url = msal_app.get_authorization_request_url(
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


# ───── Helpers: Teams + Supabase memory ──────────────────────────────────
def send_teams_reply(chat_id: str, message: str, token: str):
    url = f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"body": {"contentType": "text", "content": message}}
    resp = requests.post(url, json=body, headers=headers, timeout=10)
    return resp.status_code, resp.text


def save_message(chat_id: str, sender: str, content: str):
    supabase.table("message_history").insert(
        {"chat_id": chat_id, "sender": sender, "content": content}
    ).execute()


def fetch_recent_history(chat_id: str, limit: int = 10):
    """Return [{sender,content}] oldest→newest (max `limit`)."""
    res = (
        supabase.table("message_history")
        .select("sender,content")
        .eq("chat_id", chat_id)
        .order("timestamp", desc=False)
        .limit(limit)
        .execute()
    )
    return res.data or []


# ───── Webhook payload from Power Automate ───────────────────────────────
class TeamsWebhookPayload(BaseModel):
    messageId: str
    conversationId: str


# ───── Main webhook endpoint ────────────────────────────────────────────
@app.post("/webhook")
async def webhook_handler(payload: TeamsWebhookPayload):
    logging.info(f"[Webhook] msg {payload.messageId} in chat {payload.conversationId}")

    # 1. Get access token (refresh if needed)
    try:
        access_token, _ = get_access_token()
    except RuntimeError:
        raise HTTPException(
            401,
            "No refresh token stored. Visit /auth/login in a browser, "
            "sign in as info@barasoftware.com, then retry.",
        )

    # 2. Fetch full Teams message
    msg_url = (
        f"https://graph.microsoft.com/v1.0/chats/"
        f"{payload.conversationId}/messages/{payload.messageId}"
    )
    headers = {"Authorization": f"Bearer {access_token}"}
    r = requests.get(msg_url, headers=headers, timeout=10)
    r.raise_for_status()
    msg_data = r.json()

    sender_name = msg_data.get("from", {}).get("user", {}).get("displayName", "")
    if sender_name == "BARA Software":
        logging.info("Skipping self-message")
        return {"status": "skipped", "reason": "self message"}

    user_message = msg_data["body"]["content"].strip()

    # 3. Save user message to memory
    save_message(payload.conversationId, "user", user_message)

    # 4. Retrieve last N messages for context
    history = fetch_recent_history(payload.conversationId, limit=10)

    # 5. Build GPT context
    messages = [
        {
            "role": "system",
            "content": (
                "You are **John**, a professional corporate lawyer with excellent "
                "communication skills. Reply formally, confidently, and concisely, "
                "providing clear legal guidance."
            ),
        }
    ]

    for h in history:
        role = "user" if h["sender"] == "user" else "assistant"
        messages.append({"role": role, "content": h["content"]})

    # Add current user message last (already in history, but ensures recency)
    messages.append({"role": "user", "content": user_message})

    # 6. Generate GPT-4 answer
    chat = client.chat.completions.create(model="gpt-4", messages=messages)
    reply = chat.choices[0].message.content.strip()

    # 7. Save assistant reply
    save_message(payload.conversationId, "assistant", reply)

    # 8. Post reply to Teams
    status, ms_result = send_teams_reply(payload.conversationId, reply, access_token)

    return {
        "status": "sent" if status == 201 else "error",
        "http_status": status,
        "ai_reply": reply,
        "graph_response": ms_result,
        "timestamp": datetime.utcnow().isoformat(),
    }

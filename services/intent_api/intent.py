from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel
from openai import OpenAI
import os, requests, logging
from datetime import datetime

# ───── OpenAI ────────────────────────────────────────────────────────────
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ───── MSAL helpers (delegated auth) ─────────────────────────────────────
from common.graph_auth import (
    get_msal_app,
    exchange_code_for_tokens,
    get_access_token,
)

# ───── Supabase memory helpers ───────────────────────────────────────────
from common.memory_helpers import (
    save_message,
    fetch_chat_history,
    fetch_global_history,
)

REDIRECT_URI = "https://ai-employee-28l9.onrender.com/auth/callback"

app = FastAPI(title="AI-Employee with per-chat + global memory")
logging.basicConfig(level=logging.INFO)


# ───────────────────────── auth endpoints (one-time) ─────────────────────
@app.get("/auth/login")
def auth_login():
    url = get_msal_app().get_authorization_request_url(
        scopes=["Chat.ReadWrite"],
        redirect_uri=REDIRECT_URI,
        state="ai-login",
        prompt="login",
    )
    return RedirectResponse(url)


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


# ───────────────────────── Teams POST helper ─────────────────────────────
def send_teams_reply(chat_id: str, message: str, token: str):
    url = f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"body": {"contentType": "text", "content": message}},
        timeout=10,
    )
    return resp.status_code, resp.text


# ───────────────────── Power-Automate webhook payload ────────────────────
class TeamsWebhookPayload(BaseModel):
    messageId: str
    conversationId: str


# ─────────────────────────── main webhook ────────────────────────────────
@app.post("/webhook")
async def webhook_handler(payload: TeamsWebhookPayload):
    chat_id = payload.conversationId
    msg_id = payload.messageId
    logging.info("[Webhook] chat=%s  msg=%s", chat_id, msg_id)

    # 1. Get Graph access-token (refresh if needed)
    try:
        access_token, _ = get_access_token()
    except RuntimeError:
        raise HTTPException(
            401,
            "No refresh token stored. Visit /auth/login once and sign in.",
        )

    # 2. Pull full Teams message
    msg_url = f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages/{msg_id}"
    r = requests.get(msg_url, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
    r.raise_for_status()
    msg_data = r.json()

    sender_name = (msg_data.get("from") or {}).get("user", {}).get("displayName", "") or "Unknown"
    if sender_name == "BARA Software":
        return {"status": "skipped", "reason": "self message"}

    user_message = (msg_data.get("body") or {}).get("content", "").strip()
    if not user_message:
        return {"status": "skipped", "reason": "empty message"}

    # 3. Save user message
    save_message(chat_id, "user", user_message)

    # 4. Fetch memory
    chat_history   = fetch_chat_history(chat_id,  limit=10)  # per-thread
    global_history = fetch_global_history(limit=5)           # cross-thread

    # 5. Build GPT context
    messages = [
        {
            "role": "system",
            "content": (
                "You are **John**, a professional corporate lawyer with excellent "
                "communication skills. Reply formally, confidently and concisely."
            ),
        }
    ]

    # thread-specific
    for row in chat_history:
        role = "user" if row["sender"] == "user" else "assistant"
        messages.append({"role": role, "content": row["content"]})

    # global awareness slice
    if global_history:
        messages.append(
            {
                "role": "system",
                "content": (
                    "Below are a few snippets from other recent conversations "
                    "for additional company context."
                ),
            }
        )
        for row in global_history:
            role = "user" if row["sender"] == "user" else "assistant"
            messages.append({"role": role, "content": row["content"]})

    # newest user input last
    messages.append({"role": "user", "content": user_message})

    # 6. GPT-4
    chat = client.chat.completions.create(model="gpt-4", messages=messages)
    reply = chat.choices[0].message.content.strip()

    # 7. Save assistant reply
    save_message(chat_id, "assistant", reply)

    # 8. Send back to Teams
    status, ms_result = send_teams_reply(chat_id, reply, access_token)

    return {
        "status": "sent" if status == 201 else "error",
        "ai_reply": reply,
        "graph_response": ms_result,
        "timestamp": datetime.utcnow().isoformat(),
    }

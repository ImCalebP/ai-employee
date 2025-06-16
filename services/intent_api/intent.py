from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel
from openai import OpenAI
import os, requests
from urllib.parse import urlencode

# ───── OpenAI ────────────────────────────────────────────────────────────
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ───── Graph delegated-auth helpers you created earlier ──────────────────
from common.graph_auth import (
    get_msal_app,
    exchange_code_for_tokens,
    get_access_token,
)

MS_CLIENT_ID = os.getenv("MS_CLIENT_ID")
REDIRECT_URI = "https://ai-employee-28l9.onrender.com/auth/callback"

app = FastAPI(title="AI-Employee intent API (delegated auth)")

# ────────────────────────────────────────────────────────────────
#  Auth endpoints – run ONCE, then token is cached & refreshed
# ────────────────────────────────────────────────────────────────
@app.get("/auth/login")
def auth_login():
    msal_app = get_msal_app()
    auth_url = msal_app.get_authorization_request_url(
        scopes=["offline_access", "Chat.ReadWrite"],
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
        raise HTTPException(400, f"Token exchange failed: {e}")
    return HTMLResponse("<h2>✅ Login successful — you can close this tab.</h2>")


# ───────────────── Teams helper – send message in chat ───────────────────
def send_teams_reply(chat_id: str, reply: str, token: str):
    url = f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"body": {"contentType": "text", "content": reply}}
    r = requests.post(url, json=payload, headers=headers, timeout=10)
    return r.status_code, r.text


# ─────────────── Webhook payload model (from Power Automate) ─────────────
class TeamsWebhookPayload(BaseModel):
    messageId: str
    conversationId: str
    message: str


# ───────────────────────── /webhook – main entrypoint ────────────────────
@app.post("/webhook")
async def webhook_handler(payload: TeamsWebhookPayload):
    print(f"[Webhook] New Teams message:\n> {payload.message}")

    # 1️⃣ GPT-4 reply
    chat = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You're a helpful assistant."},
            {"role": "user", "content": payload.message},
        ],
    )
    reply = chat.choices[0].message.content.strip()

    # 2️⃣ Send reply as info@barasoftware.com (delegated token)
    try:
        access_token, _ = get_access_token()
    except RuntimeError as e:
        # No refresh token yet: instruct caller to visit /auth/login once
        raise HTTPException(
            401,
            "Refresh token not found. Visit /auth/login in browser, "
            "sign in as info@barasoftware.com, then retry."
        ) from e

    status, result = send_teams_reply(payload.conversationId, reply, access_token)

    return {
        "status": "sent" if status == 201 else "error",
        "http_status": status,
        "ai_reply": reply,
        "graph_response": result,
    }

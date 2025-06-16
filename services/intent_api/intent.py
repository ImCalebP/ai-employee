# services/intent_api/intent.py
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel
from openai import OpenAI
import os, requests, logging

# ───── OpenAI ────────────────────────────────────────────────────────────
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ───── MSAL helper utilities (delegated flow) ────────────────────────────
from common.graph_auth import (
    get_msal_app,
    exchange_code_for_tokens,
    get_access_token,          # returns (access_token, expires_in)
)

REDIRECT_URI = "https://ai-employee-28l9.onrender.com/auth/callback"

app = FastAPI(title="AI-Employee intent API (delegated auth)")

# ─────────────────────────────────────────────────────────────────────────
# AUTH – call /auth/login once; /auth/callback stores refresh-token
# ─────────────────────────────────────────────────────────────────────────
@app.get("/auth/login")
def auth_login():
    """Redirect the browser to Microsoft login once (manual step)."""
    msal_app = get_msal_app()

    # ⚠️  Do NOT include openid/profile/offline_access – MSAL adds them
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


# ───── Helper: send message to Teams chat ────────────────────────────────
def send_teams_reply(chat_id: str, message: str, token: str):
    url = f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"body": {"contentType": "text", "content": message}}
    resp = requests.post(url, json=body, headers=headers, timeout=10)
    return resp.status_code, resp.text


# ───── Webhook payload coming from Power Automate ────────────────────────
class TeamsWebhookPayload(BaseModel):
    messageId: str
    conversationId: str
    message: str


# ───── Main endpoint Power Automate calls ────────────────────────────────
@app.post("/webhook")
async def webhook_handler(payload: TeamsWebhookPayload):
    logging.info("Webhook received: %s", payload)

    # 1. Generate GPT-4 answer
    chat = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You're a helpful assistant."},
            {"role": "user",   "content": payload.message},
        ],
    )
    reply = chat.choices[0].message.content.strip()

    # 2. Get delegated access token (auto-refresh with stored refresh-token)
    try:
        access_token, _ = get_access_token()
    except RuntimeError:
        raise HTTPException(
            401,
            "No refresh token stored. Run /auth/login in a browser, "
            "sign in as info@barasoftware.com, then retry."
        )

    # 3. Post reply back to the same conversation
    status, ms_result = send_teams_reply(payload.conversationId, reply, access_token)

    return {
        "status": "sent" if status == 201 else "error",
        "http_status": status,
        "ai_reply": reply,
        "graph_response": ms_result,
    }

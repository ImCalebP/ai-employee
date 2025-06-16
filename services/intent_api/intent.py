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
logging.basicConfig(level=logging.INFO)


# ─────────────────────────────────────────────────────────────────────────
# AUTH – call /auth/login once; /auth/callback stores refresh-token
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


# ───── Main endpoint Power Automate calls ────────────────────────────────
@app.post("/webhook")
async def webhook_handler(payload: TeamsWebhookPayload):
    logging.info(f"[Webhook] Received for msg {payload.messageId} in chat {payload.conversationId}")

    try:
        access_token, _ = get_access_token()
    except RuntimeError:
        raise HTTPException(
            401,
            "No refresh token stored. Run /auth/login in a browser, "
            "sign in as info@barasoftware.com, then retry."
        )

    # 1. Fetch full message from Graph API
    msg_url = f"https://graph.microsoft.com/v1.0/chats/{payload.conversationId}/messages/{payload.messageId}"
    headers = {"Authorization": f"Bearer {access_token}"}
    r = requests.get(msg_url, headers=headers, timeout=10)
    r.raise_for_status()
    msg_data = r.json()

    # 2. Check sender name
    sender_name = msg_data.get("from", {}).get("user", {}).get("displayName", "")
    if sender_name == "BARA Software":
        logging.info("Skipping message from self: BARA Software")
        return {"status": "skipped", "reason": "Message sent by self."}

    user_message = msg_data["body"]["content"]
    logging.info(f"[Teams] {sender_name} wrote: {user_message.strip()}")

    # 3. Generate GPT-4 answer
    chat = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You're a helpful assistant."},
            {"role": "user",   "content": user_message},
        ],
    )
    reply = chat.choices[0].message.content.strip()

    # 4. Post reply to chat
    status, ms_result = send_teams_reply(payload.conversationId, reply, access_token)

    return {
        "status": "sent" if status == 201 else "error",
        "http_status": status,
        "ai_reply": reply,
        "graph_response": ms_result,
    }

from fastapi import FastAPI, Request
from pydantic import BaseModel
import openai
import requests
import os

app = FastAPI()

# Load secrets from Render environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MS_CLIENT_ID = os.getenv("MS_CLIENT_ID")
MS_CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET")
MS_TENANT_ID = os.getenv("MS_TENANT_ID")

openai.api_key = OPENAI_API_KEY

# ───────────────────────────────────────────────────────── #
#                      Graph Token Helper                   #
# ───────────────────────────────────────────────────────── #
def get_graph_token():
    url = f"https://login.microsoftonline.com/{MS_TENANT_ID}/oauth2/v2.0/token"
    data = {
        "client_id": MS_CLIENT_ID,
        "client_secret": MS_CLIENT_SECRET,
        "grant_type": "client_credentials",
        "scope": "https://graph.microsoft.com/.default"
    }
    r = requests.post(url, data=data)
    r.raise_for_status()
    return r.json()["access_token"]

# ───────────────────────────────────────────────────────── #
#                      POST to Teams Chat                   #
# ───────────────────────────────────────────────────────── #
def send_teams_reply(conversation_id: str, reply: str, token: str):
    url = f"https://graph.microsoft.com/v1.0/chats/{conversation_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "body": {
            "contentType": "text",
            "content": reply
        }
    }
    r = requests.post(url, headers=headers, json=payload)
    return r.status_code, r.text

# ───────────────────────────────────────────────────────── #
#                    Webhook Payload Model                  #
# ───────────────────────────────────────────────────────── #
class TeamsWebhookPayload(BaseModel):
    messageId: str
    conversationId: str
    message: str

# ───────────────────────────────────────────────────────── #
#                        /webhook endpoint                  #
# ───────────────────────────────────────────────────────── #
@app.post("/webhook")
async def webhook_handler(payload: TeamsWebhookPayload):
    print(f"[Webhook] New message from Teams:\n> {payload.message}")

    # 1. Generate AI reply
    completion = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You're a helpful assistant."},
            {"role": "user", "content": payload.message}
        ]
    )
    reply = completion.choices[0].message.content.strip()

    # 2. Send reply back to Teams chat
    token = get_graph_token()
    status, result = send_teams_reply(payload.conversationId, reply, token)

    return {
        "status": "sent" if status == 201 else "error",
        "messageId": payload.messageId,
        "conversationId": payload.conversationId,
        "ai_reply": reply,
        "graph_response": result
    }

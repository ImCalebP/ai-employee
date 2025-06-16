from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel
from openai import OpenAI
import os, requests, logging
from datetime import datetime

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ── internal helpers ────────────────────────────────────────────────────
from common.graph_auth     import get_msal_app, exchange_code_for_tokens, get_access_token
from common.memory_helpers import (
    save_message, fetch_chat_history, fetch_global_history, semantic_search
)

REDIRECT_URI = "https://ai-employee-28l9.onrender.com/auth/callback"

app = FastAPI(title="AI-Employee  • tier-2 recall")
logging.basicConfig(level=logging.INFO)

# ───────────────────────── auth (one-time) ──────────────────────────────
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
    exchange_code_for_tokens(code, REDIRECT_URI)
    return HTMLResponse("<h2>✅ Login successful — close this tab.</h2>")

# ───────────────────────── Teams helper ─────────────────────────────────
def send_teams_reply(chat_id: str, message: str, token: str):
    resp = requests.post(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"body": {"contentType": "text", "content": message}},
        timeout=10,
    )
    return resp.status_code, resp.text

# ───────────────────────── Webhook model ────────────────────────────────
class TeamsWebhookPayload(BaseModel):
    messageId: str
    conversationId: str

# ───────────────────────── main webhook ─────────────────────────────────
@app.post("/webhook")
async def webhook_handler(payload: TeamsWebhookPayload):
    chat_id, msg_id = payload.conversationId, payload.messageId
    logging.info("→ webhook chat=%s msg=%s", chat_id, msg_id)

    try:
        access_token, _ = get_access_token()
    except RuntimeError:
        raise HTTPException(401, "Run /auth/login once in a browser.")

    # 1 pull full Teams message
    r = requests.get(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages/{msg_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    r.raise_for_status()
    msg = r.json()
    sender = (msg.get("from") or {}).get("user", {}).get("displayName", "Unknown")
    text   = (msg.get("body") or {}).get("content", "").strip()

    if sender == "BARA Software" or not text:
        return {"status": "skipped"}

    # 2 save user message
    save_message(chat_id, "user", text)

    # 3 build initial context
    chat_mem   = fetch_chat_history(chat_id, limit=10)
    global_mem = fetch_global_history(limit=5)

    messages = [
        {
            "role": "system",
            "content": (
                "You are **John**, a professional corporate lawyer. "
                "Write formal, confident, concise answers."
            ),
        }
    ]
    for row in chat_mem:
        role = "user" if row["sender"] == "user" else "assistant"
        messages.append({"role": role, "content": row["content"]})

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

    # 4 first GPT pass asking if more memory is needed
    prompt_flag = {
        "role": "system",
        "content": (
            "If you need more historical context *outside* what you see, "
            'respond exactly with JSON: {"need_memory": true, "reason": "..."} . '
            "Otherwise respond JSON: {\"need_memory\": false, \"answer\": \"...\"}"
        ),
    }
    draft = client.chat.completions.create(
        model="gpt-4o-mini", response_format={"type": "json_object"},
        messages=messages + [prompt_flag]
    ).choices[0].message

    need_memory = False
    answer_txt  = ""

    try:
        d = draft.json()          # because we used response_format=json_object
        need_memory = d.get("need_memory", False)
        answer_txt  = d.get("answer", "").strip()
    except Exception:
        # model decided to answer directly
        answer_txt = draft.content.strip()

    # 5 optional semantic search + second GPT
    if need_memory:
        matches = semantic_search(text, k=5)
        if matches:
            mem_msgs = [{"role": "system", "content": "Extra memories:"}]
            for row in matches:
                role = "user" if row["sender"] == "user" else "assistant"
                mem_msgs.append({"role": role, "content": row["content"]})

            answer_txt = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages + mem_msgs + [{"role": "user", "content": text}],
            ).choices[0].message.content.strip()

    # 6 save assistant reply + send to Teams
    save_message(chat_id, "assistant", answer_txt)
    status, ms = send_teams_reply(chat_id, answer_txt, access_token)

    return {
        "status": "sent" if status == 201 else "error",
        "answer": answer_txt,
        "ts": datetime.utcnow().isoformat(),
    }

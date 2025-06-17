# ─────────────────────────────────────────────────────────────────────────────
# services/intent_api/intent.py
"""
FastAPI webhook that receives Power-Automate payloads, chats as **John**
(a professional corporate lawyer), stores memory in Supabase, recalls extra
context with pgvector **and** can send Outlook e-mails ― **only after an
explicit user confirmation**.

• One-time interactive login:  GET  /auth/login   (stores encrypted refresh-token)
• Graph access-token refresh:  automatic via common.graph_auth
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from openai import OpenAI
from pydantic import BaseModel

# ─────────────────────────  OpenAI / model  ──────────────────────────────
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ─────────────────────────  Graph auth helpers  ──────────────────────────
from common.graph_auth import (
    exchange_code_for_tokens,
    get_access_token,
    get_msal_app,
)

# ─────────────────────────  Memory helpers  ─────────────────────────────
from common.memory_helpers import (
    fetch_chat_history,
    fetch_global_history,
    save_message,
    semantic_search,
    supabase,  # re-exported Supabase client
)

# ─────────────────────────  Outlook helper  ─────────────────────────────
from services.intent_api.email_agent import send_with_outlook  # <-- already sends

# ─────────────────────────  FastAPI basics  ─────────────────────────────
REDIRECT_URI = "https://ai-employee-28l9.onrender.com/auth/callback"

app = FastAPI(title="AI-Employee • intent handler")
logging.basicConfig(level=logging.INFO)

# ─────────────────────────  Draft helpers  ──────────────────────────────
DRAFT_TABLE = "email_drafts"


def _save_draft(chat_id: str, details: Dict[str, Any]) -> str:
    draft_id = str(uuid.uuid4())
    supabase.table(DRAFT_TABLE).insert(
        {
            "id": draft_id,
            "chat_id": chat_id,
            "details": json.dumps(details),
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
        }
    ).execute()
    return draft_id


def _get_pending_draft(chat_id: str) -> Dict[str, Any] | None:
    resp = (
        supabase.table(DRAFT_TABLE)
        .select("id,details")
        .eq("chat_id", chat_id)
        .eq("status", "pending")
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def _mark_draft_sent(draft_id: str):
    supabase.table(DRAFT_TABLE).update({"status": "sent"}).eq("id", draft_id).execute()


# ─────────────────────────  Teams helpers  ──────────────────────────────
def send_teams_reply(chat_id: str, message: str, token: str) -> int:
    resp = requests.post(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"body": {"contentType": "text", "content": message}},
        timeout=10,
    )
    return resp.status_code


# ─────────────────────────  Webhook model  ──────────────────────────────
class TeamsWebhookPayload(BaseModel):
    messageId: str
    conversationId: str


# ─────────────────────────  Auth endpoints  ─────────────────────────────
@app.get("/auth/login")
def auth_login() -> RedirectResponse:
    url = get_msal_app().get_authorization_request_url(
        scopes=["Chat.ReadWrite"],
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


# ─────────────────────────  Main webhook  ───────────────────────────────
@app.post("/webhook")
async def webhook_handler(payload: TeamsWebhookPayload):
    chat_id, msg_id = payload.conversationId, payload.messageId
    logging.info("→ webhook chat=%s msg=%s", chat_id, msg_id)

    # ─── 0. Graph token ────────────────────────────────────────────────
    try:
        access_token, _ = get_access_token()
    except RuntimeError:
        raise HTTPException(401, "Run /auth/login once from a browser first.")

    # ─── 1. Fetch the full Teams message ───────────────────────────────
    msg = (
        requests.get(
            f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages/{msg_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        .json()
    )
    sender = (msg.get("from") or {}).get("user", {}).get("displayName", "Unknown")
    text = (msg.get("body") or {}).get("content", "").strip()

    # ignore bot / blank
    if sender == "BARA Software" or not text:
        return {"status": "skipped"}

    # ─── 2. Check confirmation for a pending draft ──────────────────────
    draft = _get_pending_draft(chat_id)
    confirm = bool(re.fullmatch(r"\s*(yes|send|okay|go ahead)\s*\.?", text, re.I))
    if draft and confirm:
        details = json.loads(draft["details"])
        send_with_outlook(details)  # <-- actually sends via Graph SMTP/REST
        _mark_draft_sent(draft["id"])
        reply_txt = "📧 Email sent as requested."
        save_message(chat_id, "assistant", reply_txt)
        send_teams_reply(chat_id, reply_txt, access_token)
        return {"status": "sent", "email_sent": True}

    # ─── 3. Persist user turn ───────────────────────────────────────────
    save_message(chat_id, "user", text)

    # ─── 4. Tier-1 memory ------------------------------------------------
    chat_mem: List[Dict[str, str]] = fetch_chat_history(chat_id, limit=10)
    global_mem: List[Dict[str, str]] = fetch_global_history(limit=5)

    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are **John**, an experienced corporate lawyer. "
                "Reply formally and concisely."
            ),
        }
    ]
    for row in chat_mem:
        messages.append({"role": "user" if row["sender"] == "user" else "assistant", "content": row["content"]})
    if global_mem:
        messages.append({"role": "system", "content": "Context from other chats:"})
        for row in global_mem:
            messages.append({"role": "user" if row["sender"] == "user" else "assistant", "content": row["content"]})
    messages.append({"role": "user", "content": text})

    # ─── 5. Need more memory? -------------------------------------------
    need_prompt = {
        "role": "system",
        "content": (
            'Respond ONLY {"need_memory":true} or {"need_memory":false} – nothing else.'
        ),
    }
    first = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=messages + [need_prompt],
    ).choices[0].message
    need_more = json.loads(first.content).get("need_memory", False)

    if need_more:
        mem_matches = semantic_search(text, k=5)
        if mem_matches:
            messages.append({"role": "system", "content": "Additional memories:"})
            for row in mem_matches:
                messages.append(
                    {"role": "user" if row["sender"] == "user" else "assistant", "content": row["content"]}
                )

    # ─── 6. Final structured answer -------------------------------------
    schema = {
        "role": "system",
        "content": (
            "Output ONLY one JSON object.\n\n"
            'For an e-mail draft:\n{"intent":"send_email","reply":"…","emailDetails":{"to":[],"subject":"","body":""}}\n\n'
            'For a normal reply:\n{"intent":"reply","reply":"…"}\n'
            "Never invent an address; ask if unknown."
        ),
    }
    final = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=messages + [schema],
    ).choices[0].message
    try:
        parsed = json.loads(final.content)
    except json.JSONDecodeError:
        parsed = {"intent": "reply", "reply": final.content.strip()}

    intent = parsed.get("intent", "reply")
    reply = parsed.get("reply", "").strip()

    # ─── 7. Intent branch -------------------------------------------------
    if intent == "send_email":
        draft_id = _save_draft(chat_id, parsed["emailDetails"])
        pretty_list = ", ".join(parsed["emailDetails"]["to"])
        preview = (
            f"📄 **Draft e-mail** to {pretty_list} (ID `{draft_id}`):\n\n"
            f"**Subject:** {parsed['emailDetails']['subject']}\n\n"
            f"{parsed['emailDetails']['body']}\n\n"
            "Reply with **yes** to send it."
        )
        reply = preview

    # ─── 8. Persist & send back to Teams ---------------------------------
    save_message(chat_id, "assistant", reply)
    status = send_teams_reply(chat_id, reply, access_token)

    return {
        "status": "sent" if status == 201 else "graph_error",
        "intent": intent,
        "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
    }

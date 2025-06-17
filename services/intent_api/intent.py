# ─────────────────────────────────────────────────────────────────────────────
# services/intent_api/intent.py
from __future__ import annotations
import json, logging, os, re, requests
from datetime import datetime
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

from common.graph_auth import get_access_token
from common.memory_helpers import (
    save_message, fetch_chat_history, fetch_global_history, semantic_search
)
from services.intent_api.email_agent import process_email_request
from services.intent_api.reply_agent import process_reply

app = FastAPI(title="AI-Employee • intent detector")
logging.basicConfig(level=logging.INFO)


def _graph(url: str, token: str, *, method: str = "GET",
           payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    r = requests.request(method, url,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=payload, timeout=10)
    r.raise_for_status()
    return r.json() if r.text else {}


class TeamsWebhookPayload(BaseModel):
    messageId: str
    conversationId: str


# ──────────────────────────────────────────────────────────────────────
@app.post("/webhook")
async def webhook_handler(payload: TeamsWebhookPayload):
    chat_id, msg_id = payload.conversationId, payload.messageId
    logging.info("→ webhook chat=%s msg=%s", chat_id, msg_id)

    # Graph token
    try:
        access_token, _ = get_access_token()
    except RuntimeError:
        raise HTTPException(401, "Run /auth/login once from a browser first.")

    # Incoming Teams message
    msg = _graph(f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages/{msg_id}",
                 access_token)
    sender = (msg.get("from") or {}).get("user", {}).get("displayName", "Unknown")
    text   = (msg.get("body") or {}).get("content", "").strip()
    if sender == "BARA Software" or not text:
        return {"status": "skipped"}

    chat_type = _graph(f"https://graph.microsoft.com/v1.0/chats/{chat_id}"
                       "?$select=chatType", access_token).get("chatType", "unknown")
    save_message(chat_id, "user", text, chat_type)

    # Memory & context
    chat_mem   = fetch_chat_history(chat_id, 35)
    global_mem = fetch_global_history(8)
    sem_mem    = semantic_search(text, chat_id, 8, 4)

    last_assistant = next(
        (r["content"] for r in reversed(chat_mem) if r["sender"] == "assistant"),
        ""
    )

    # ---------------- GPT intent classifier -----------------
    def _add(dst, rows):
        for r in rows:
            dst.append({"role":"user" if r["sender"]=="user" else "assistant",
                        "content":r["content"]})

    msgs: List[Dict[str,str]] = [{
        "role":"system",
        "content":(
            "Classify intent:\n"
            "• send_email – user requests a *new* Outlook e-mail\n"
            "• reply      – anything else\n\n"
            "If the last assistant message already confirms an e-mail was sent, "
            "simple acknowledgements (thanks, perfect, great, 👍 etc.) should be "
            "classified as reply.\n"
            "Return ONE JSON {\"intent\":\"send_email|reply\"}."
        ),
    },
    {"role":"system","content":f"🕑 Last assistant: {last_assistant}"}]
    _add(msgs, chat_mem)
    if sem_mem:
        msgs += [{"role":"system","content":"🔎 Relevant context:"}]
        _add(msgs, sem_mem)
    if global_mem:
        msgs += [{"role":"system","content":"🌐 Other chats context:"}]
        _add(msgs, global_mem)
    msgs += [
        {"role":"user","content":text},
        {"role":"system","content":"Output strictly one JSON with key intent."}
    ]

    intent = json.loads(
        client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type":"json_object"},
            messages=msgs,
        ).choices[0].message.content
    ).get("intent","reply")

    # ---------------- Python duplicate-send guard ----------------
    if intent == "send_email" and last_assistant.startswith("✅ E-mail sent"):
        ack_pattern = r"^(thanks|thank you|merci|perfect|great|awesome|ok|okay|cool|\+?1|👍)\b"
        if re.match(ack_pattern, text.strip().lower()):
            logging.info("Duplicate-send guard: treating as reply")
            intent = "reply"

    # ---------------- Delegate ----------------
    if intent == "send_email":
        result = process_email_request(chat_id)
        if result["status"] == "missing":
            process_reply(chat_id, text, missing_info=result["missing"])
    else:
        process_reply(chat_id, text)

    return {
        "status":"ok",
        "intent":intent,
        "timestamp":datetime.utcnow().isoformat(timespec="seconds")
    }

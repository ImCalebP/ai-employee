# services/intent_api/email_agent.py
from __future__ import annotations
import json, logging, os, requests
from typing import Any, Dict, List

from openai import OpenAI
from common.graph_auth import get_access_token
from common.memory_helpers import (
    save_message, fetch_chat_history, fetch_global_history, semantic_search
)
from services.intent_api.reply_agent import process_reply   # circular-safe

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
logging.getLogger(__name__).setLevel(logging.INFO)


# ───────── Graph helpers ─────────
def _graph(url: str, token: str, *, method: str = "GET",
           payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    r = requests.request(method, url,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=payload, timeout=10)
    r.raise_for_status()
    return r.json() if r.text else {}


def _teams_post(chat_id: str, text: str, token: str) -> None:
    body = {"body": {"contentType": "text", "content": text}}
    requests.post(f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=body, timeout=10).raise_for_status()


def _send_outlook(details: Dict[str, Any], token: str) -> None:
    payload = {
        "message": {
            "subject": details["subject"],
            "body": {"contentType": "Text", "content": details["body"]},
            "toRecipients": [
                {"emailAddress": {"address": addr}} for addr in details["to"]
            ],
        }
    }
    _graph("https://graph.microsoft.com/v1.0/me/sendMail",
           token, method="POST", payload=payload)


# ───────── Public entry-point ─────────
def process_email_request(chat_id: str) -> Dict[str, str]:
    """
    Draft & (if complete) send an e-mail. Sends a confirmation message
    back to Teams when the mail succeeds.
    """
    access_token, _ = get_access_token()

    chat_mem   = fetch_chat_history(chat_id, 40)
    user_turns = [r for r in chat_mem if r["sender"] == "user"]
    last_user  = user_turns[-1]["content"] if user_turns else ""
    glb_mem    = fetch_global_history(8)
    sem_mem    = semantic_search(last_user, chat_id, 8, 4)

    def _add(dst, rows):
        for r in rows:
            dst.append({"role":"user" if r["sender"]=="user" else "assistant",
                        "content":r["content"]})

    msgs: List[Dict[str,str]] = [{
        "role":"system",
        "content":(
            "Draft a concise, professional Outlook e-mail.\n"
            "Return JSON: {\"to\":[],\"subject\":\"…\",\"body\":\"…\"}\n"
            "If any field missing: {\"missing\":\"subject\"} etc.  Never invent."
        ),
    }]
    _add(msgs, chat_mem)
    if sem_mem:
        msgs += [{"role":"system","content":"🔎 Relevant context:"}]
        _add(msgs, sem_mem)
    if glb_mem:
        msgs += [{"role":"system","content":"🌐 Other chats context:"}]
        _add(msgs, glb_mem)
    msgs.append({"role":"system",
                 "content":"Output strictly one JSON as specified."})

    draft = json.loads(
        client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type":"json_object"},
            messages=msgs
        ).choices[0].message.content
    )

    # Missing-info branch
    if "missing" in draft:
        process_reply(chat_id, last_user, missing_info=draft["missing"])
        return {"status":"missing","missing":draft["missing"]}

    for key in ("to","subject","body"):
        if key not in draft or not draft[key]:
            process_reply(chat_id, last_user, missing_info=key)
            return {"status":"missing","missing":key}

    # Send mail
    _send_outlook(draft, access_token)
    logging.info("✓ Outlook e-mail sent: %s → %s",
                 draft["subject"], ", ".join(draft["to"]))

    # Confirmation message to Teams
    confirm = f"✅ E-mail sent: “{draft['subject']}” ➜ {', '.join(draft['to'])}"
    _teams_post(chat_id, confirm, access_token)

    # Persist assistant turn
    chat_type = next((r.get("chat_type") for r in chat_mem if r.get("chat_type")), None)
    save_message(chat_id, "assistant", confirm, chat_type or "unknown")

    return {"status":"sent"}

# ─────────────────────────────────────────────────────────────────────────────
# services/intent_api/email_agent.py
"""
Rédige et envoie un e-mail Outlook.
• utilise contact_agent pour enregistrer / vérifier les destinataires
• demande via reply_agent quand il manque des adresses
"""
from __future__ import annotations
import json, logging, os, re, requests
from typing import Any, Dict, List

from openai import OpenAI
from common.graph_auth import get_access_token
from common.memory_helpers import (
    fetch_chat_history, fetch_global_history, semantic_search, save_message
)
from services.intent_api.reply_agent import process_reply
from services.intent_api.contact_agent import upsert_contact

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
logging.getLogger(__name__).setLevel(logging.INFO)


# ────────────── helpers Graph / Teams ──────────────
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
    requests.post(
        f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages",
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


# ────────────── e-mail agent ──────────────
def _extract_emails(text: str) -> List[str]:
    """Rapide regex pour repérer les emails dans une phrase."""
    return re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)


def process_email_request(chat_id: str) -> Dict[str, str]:
    """
    Essaie de rédiger & envoyer l’email.
    • retourne {"status":"missing","missing":"recipients"} s’il manque encore des infos
    • retourne {"status":"sent"} quand le mail est envoyé
    """
    access_token, _ = get_access_token()

    # ---------- mémoire ----------
    chat_mem   = fetch_chat_history(chat_id, 40)
    user_turns = [r for r in chat_mem if r["sender"] == "user"]
    last_user  = user_turns[-1]["content"] if user_turns else ""
    global_mem = fetch_global_history(8)
    sem_mem    = semantic_search(last_user, chat_id, 8, 4)

    # ---------- résolution rapide des emails explicitement écrits ----------
    explicit_emails = list(set(_extract_emails(last_user)))
    for addr in explicit_emails:
        upsert_contact(email=addr, conversation_id=chat_id)

    # ---------- prompt GPT ----------
    def _add(dst, rows):
        for r in rows:
            dst.append({"role":"user" if r["sender"]=="user" else "assistant",
                        "content":r["content"]})

    msgs: List[Dict[str,str]] = [{
        "role":"system",
        "content":(
            "Rédige un e-mail professionnel Outlook.\n"
            "Retourne JSON stricte : {\"to\":[],\"subject\":\"…\",\"body\":\"…\"}\n"
            "Si un champ est manquant → {\"missing\":\"recipients|subject|body\"}.\n"
            "N’invente JAMAIS les adresses."
        ),
    }]
    _add(msgs, chat_mem)
    if sem_mem:
        msgs += [{"role":"system","content":"🔎 Contexte pertinent:"}]
        _add(msgs, sem_mem)
    if global_mem:
        msgs += [{"role":"system","content":"🌐 Autres conversations:"}]
        _add(msgs, global_mem)
    msgs.append({"role":"system","content":"Réponds uniquement par l’objet JSON."})

    draft = json.loads(
        client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type":"json_object"},
            messages=msgs
        ).choices[0].message.content
    )

    # ---------- manque d’info ----------
    if "missing" in draft:
        process_reply(chat_id, last_user, missing_info=draft["missing"])
        return {"status":"missing", "missing":draft["missing"]}

    for key in ("to","subject","body"):
        if key not in draft or not draft[key]:
            process_reply(chat_id, last_user, missing_info=key)
            return {"status":"missing", "missing":key}

    # ---------- enregistrement des destinataires ----------
    for addr in draft["to"]:
        upsert_contact(email=addr, conversation_id=chat_id)

    # ---------- envoi Outlook ----------
    _send_outlook(draft, access_token)
    confirm = f"✅ E-mail sent: “{draft['subject']}” ➜ {', '.join(draft['to'])}"
    _teams_post(chat_id, confirm, access_token)

    chat_type = next((r.get("chat_type") for r in chat_mem if r.get("chat_type")), None)
    save_message(chat_id, "assistant", confirm, chat_type or "unknown")
    logging.info("✓ Outlook e-mail envoyé: %s", draft["subject"])

    return {"status":"sent"}

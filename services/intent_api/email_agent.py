# ─────────────────────────────────────────────────────────────────────────────
# services/intent_api/email_agent.py
"""
Rédige et envoie un e-mail Outlook.

Règles strictes sur les destinataires :
• adresse absente de la table `contacts`  ➜ on demande à l’utilisateur
• adresse factice (@example.com, placeholder…) ➜ on demande à l’utilisateur
• sinon on envoie, ET on enregistre / enrichit le contact
"""
from __future__ import annotations
import json, logging, os, re, requests
from typing import Any, Dict, List, Tuple

from openai import OpenAI
from common.graph_auth import get_access_token
from common.memory_helpers import (
    fetch_chat_history, fetch_global_history, semantic_search, save_message
)
from services.intent_api.reply_agent import process_reply
from services.intent_api.contact_agent import upsert_contact, get_contact

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
logging.getLogger(__name__).setLevel(logging.INFO)


# ─────────────── utilitaires Graph / Teams ───────────────
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
        json=body, timeout=10
    ).raise_for_status()


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


# ─────────────── helpers internes ───────────────
_PLACEHOLDER_RE = re.compile(
    r"(@example\.com$)|(^placeholder)|(^test@)|(^foo@)|(^bar@)", re.I
)

def _extract_emails(text: str) -> List[str]:
    """Trouve toutes les adresses email explicites dans un texte."""
    return re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)


def _split_recipients(addresses: List[str]) -> Tuple[List[str], List[str]]:
    """
    Retourne (valides, manquantes).
    • valide = existe dans contacts ET n’est pas un placeholder
    • manquante = tout le reste
    """
    valides, manquantes = [], []
    for addr in addresses:
        if _PLACEHOLDER_RE.search(addr):
            manquantes.append(addr)
            continue
        if get_contact(email=addr):
            valides.append(addr)
        else:
            manquantes.append(addr)
    return valides, manquantes


# ─────────────── MAIN entry-point ───────────────
def process_email_request(chat_id: str) -> Dict[str, str]:
    access_token, _ = get_access_token()

    # mémoire
    chat_mem   = fetch_chat_history(chat_id, 40)
    user_turns = [r for r in chat_mem if r["sender"] == "user"]
    last_user  = user_turns[-1]["content"] if user_turns else ""
    global_mem = fetch_global_history(8)
    sem_mem    = semantic_search(last_user, chat_id, 8, 4)

    # enregistre immédiatement les adresses explicites
    for addr in set(_extract_emails(last_user)):
        upsert_contact(email=addr, conversation_id=chat_id)

    # ---------- GPT génère brouillon ----------
    def _add(dst, rows):
        for r in rows:
            dst.append({
                "role": "user" if r["sender"] == "user" else "assistant",
                "content": r["content"],
            })

    msgs: List[Dict[str, str]] = [{
        "role": "system",
        "content": (
            "Rédige un e-mail Outlook professionnel.\n"
            "Réponds SEULEMENT par un JSON strict :\n"
            '{"to":["a@b.com"],"subject":"...","body":"..."}\n'
            "Si un champ est manquant : {\"missing\":\"recipients|subject|body\"}.\n"
            "N’invente JAMAIS de nouvelle adresse : utilise uniquement celles qui "
            "apparaissent déjà dans le contexte ou retourne missing."
        ),
    }]
    _add(msgs, chat_mem)
    if sem_mem:
        msgs += [{"role":"system","content":"🔎 Contexte pertinent:"}]
        _add(msgs, sem_mem)
    if global_mem:
        msgs += [{"role":"system","content":"🌐 Autres conversations:"}]
        _add(msgs, global_mem)
    msgs.append({"role":"system",
                 "content":"Réponds uniquement par l’objet JSON mentionné."})

    draft = json.loads(
        client.chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=msgs
        ).choices[0].message.content
    )

    # ---------- brouillon incomplet ----------
    if "missing" in draft:
        process_reply(chat_id, last_user, missing_info=draft["missing"])
        return {"status": "missing", "missing": draft["missing"]}

    for field in ("to", "subject", "body"):
        if field not in draft or not draft[field]:
            process_reply(chat_id, last_user, missing_info=field)
            return {"status": "missing", "missing": field}

    # ---------- validation des destinataires ----------
    valid_to, missing_to = _split_recipients(draft["to"])
    if missing_to:
        noms = ", ".join(missing_to)
        prompt = f"I dont have the right email address : {noms}. Can you provide it please ?"
        process_reply(chat_id, last_user,
                      missing_info="recipients",
                      custom_prompt=prompt)
        return {"status": "missing", "missing": "recipients"}

    # ---------- enregistrement (enrichissement) ----------
    for addr in valid_to:
        upsert_contact(email=addr, conversation_id=chat_id)

    # ---------- envoi Outlook ----------
    draft["to"] = valid_to               # on ne garde QUE les valides
    _send_outlook(draft, access_token)

    confirm = f"✅ E-mail sent: “{draft['subject']}” ➜ {', '.join(valid_to)}"
    _teams_post(chat_id, confirm, access_token)

    chat_type = next((r.get("chat_type") for r in chat_mem if r.get("chat_type")), None)
    save_message(chat_id, "assistant", confirm, chat_type or "unknown")
    logging.info("✓ Outlook e-mail envoyé: %s", draft["subject"])

    return {"status": "sent"}

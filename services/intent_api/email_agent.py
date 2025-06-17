"""
Light-weight Outlook e-mail helper
----------------------------------
• expects *validated*  emailDetails  dict:
    {
        "to": ["a@corp.com", "b@corp.com"],
        "subject": "…",
        "body": "…"
    }
• relies on the delegated refresh-token handled in common.graph_auth
"""

from __future__ import annotations

import json
import logging
import os
from typing import Dict, List

import requests

from common.graph_auth import get_access_token

# optional – set a default “From:” address if different from the login user
SENDER = os.getenv("MS_SEND_AS")        # e.g. "info@barasoftware.com"


def _validate(details: Dict[str, str]) -> None:
    """Very defensive – we never want the model to sneak junk through."""
    required = {"to", "subject", "body"}
    if not required <= set(details):
        missing = ", ".join(required - set(details))
        raise ValueError(f"emailDetails missing: {missing}")

    if not isinstance(details["to"], list) or not details["to"]:
        raise ValueError("to must be a non-empty list")

    bad = [addr for addr in details["to"] if "@" not in addr]
    if bad:
        raise ValueError(f"Invalid e-mail address(es): {', '.join(bad)}")

    if not details["subject"].strip():
        raise ValueError("subject cannot be blank")
    if not details["body"].strip():
        raise ValueError("body cannot be blank")


def send_with_outlook(details: Dict[str, str]) -> str:
    """
    Sends the message and returns Graph's message-id.
    Raises ValueError on bad input, RuntimeError on Graph failure.
    """
    _validate(details)

    access_token, _ = get_access_token()

    # Build Graph payload
    mail: Dict[str, object] = {
        "message": {
            "subject": details["subject"],
            "body": {"contentType": "HTML", "content": details["body"]},
            "toRecipients": [
                {"emailAddress": {"address": addr}} for addr in details["to"]
            ],
        },
        "saveToSentItems": True,
    }
    if SENDER:
        mail["message"]["from"] = {"emailAddress": {"address": SENDER}}

    resp = requests.post(
        "https://graph.microsoft.com/v1.0/me/sendMail",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        data=json.dumps(mail),
        timeout=15,
    )

    if resp.status_code != 202:
        raise RuntimeError(
            f"Graph sendMail failed ({resp.status_code}): {resp.text[:200]}"
        )

    logging.info("✓ Outlook mail queued -> %s", ", ".join(details["to"]))
    return resp.headers.get("Content-ID", "<no-id>")

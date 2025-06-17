# services/intent_api/email_agent.py
from __future__ import annotations

import os, logging, requests
from typing import List, Dict, Any

from openai import OpenAI

from common.graph_auth import get_access_token
from common.memory_helpers import MemoryHelper


class EmailAgent:
    """
    Specialised agent that composes (if needed) and sends Outlook e-mails.
    Usage:
        EmailAgent(MemoryHelper).execute(chat_id, email_details)
    """

    def __init__(self, memory: MemoryHelper) -> None:
        self.mem    = memory
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.log    = logging.getLogger("EmailAgent")

    # ────────────────────────────────────────────────────────────────────
    def execute(self, chat_id: str, details: Dict[str, Any]) -> None:
        """
        details must contain:  to · subject · body
        If body is empty, it will be drafted automatically from chat context.
        Raises ValueError / RuntimeError on problems (handled by caller).
        """

        # 1️⃣ Validation
        missing = [k for k in ("to", "subject", "body")
                   if not details.get(k)]
        if missing:
            raise ValueError(f"Missing fields: {', '.join(missing)}")

        # 2️⃣ Auto-draft body (optional)
        if not details["body"].strip():
            details["body"] = self._draft_body(chat_id, details)

        # 3️⃣ Send via Graph
        status, msg = self._send_mail(details["to"],
                                      details["subject"],
                                      details["body"])
        if status != 202:
            raise RuntimeError(f"Graph API returned {status}: {msg}")

        # 4️⃣ Log succinct success note to memory
        self.mem.save(chat_id, "assistant",
                      f"📧 Email sent to {', '.join(details['to'])}")
        self.log.info("✓ Email sent to %s", details["to"])

    # ────────────────────────────────────────────────────────────────────
    # internal helpers
    # ────────────────────────────────────────────────────────────────────
    def _draft_body(self, chat_id: str, details: Dict[str, Any]) -> str:
        """
        Create a concise, polite e-mail body using GPT-4o
        and the last ~20 messages for context.
        """
        history = self.mem.last_messages(chat_id, limit=20)

        msgs = [{"role": "system",
                 "content": "Write a concise, professional Outlook e-mail body."}]
        for row in history:
            msgs.append({"role": row["sender"], "content": row["content"]})

        msgs.append(
            {"role": "user",
             "content": (f'Draft an e-mail to {", ".join(details["to"])} '
                         f'about "{details["subject"]}".')}
        )

        body = (self.client.chat.completions.create(
                    model="gpt-4o",
                    messages=msgs,
                    temperature=0.3,
                    max_tokens=400
                ).choices[0].message.content.strip())

        return body

    def _send_mail(self, to_emails: List[str],
                   subject: str, body: str) -> tuple[int, str]:
        """
        Low-level Graph call. Returns (status_code, response_text).
        """
        access_token, _ = get_access_token()
        resp = requests.post(
            "https://graph.microsoft.com/v1.0/me/sendMail",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type":  "application/json",
            },
            json={
                "message": {
                    "subject": subject,
                    "body": {
                        "contentType": "Text",
                        "content": body,
                    },
                    "toRecipients": [
                        {"emailAddress": {"address": e}} for e in to_emails
                    ],
                },
                "saveToSentItems": True
            },
            timeout=10
        )
        return resp.status_code, resp.text

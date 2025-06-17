# services/intent_api/email_agent.py
import requests
from typing import List, Dict, Tuple
from common.graph_auth import get_access_token

def send_outlook_email(to_emails: List[str], subject: str, body: str) -> Tuple[int, str]:
    access_token, _ = get_access_token()
    response = requests.post(
        "https://graph.microsoft.com/v1.0/me/sendMail",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "Text",
                    "content": body,
                },
                "toRecipients": [{"emailAddress": {"address": e}} for e in to_emails],
            },
            "saveToSentItems": True
        },
        timeout=10
    )
    return response.status_code, response.text

def process_email_request(email_details: Dict[str, str | List[str]]) -> Tuple[str, bool]:
    """
    email_details: {
        "to": ["bob@example.com", "alice@example.com"],
        "subject": "Meeting Update",
        "body": "Hi all, our meeting is moved to 2 PM."
    }
    Returns (response_message, success)
    """
    missing_fields = [k for k in ["to", "subject", "body"] if k not in email_details or not email_details[k]]
    if missing_fields:
        return f"Missing fields: {', '.join(missing_fields)}. Please provide those.", False

    status, msg = send_outlook_email(email_details["to"], email_details["subject"], email_details["body"])
    if status == 202:
        return "✅ Email sent successfully!", True
    else:
        return f"❌ Failed to send email. Reason: {msg}", False

"""
Very small helper – just enough to POST a message into a chat.
"""
import time
import httpx
from config.credentials import settings

_TOKEN_URL = (
    f"https://login.microsoftonline.com/{settings.MS_TENANT_ID}/oauth2/v2.0/token"
)
_SCOPE      = "https://graph.microsoft.com/.default"
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class _TokenCache:
    token: str | None = None
    exp:   float = 0.0

    @classmethod
    def get(cls) -> str:
        if cls.token and time.time() < cls.exp - 60:
            return cls.token
        data = {
            "client_id":     settings.MS_CLIENT_ID.get_secret_value(),
            "client_secret": settings.MS_CLIENT_SECRET.get_secret_value(),
            "grant_type":    "client_credentials",
            "scope":         _SCOPE,
        }
        r = httpx.post(_TOKEN_URL, data=data, timeout=10)
        r.raise_for_status()
        body = r.json()
        cls.token = body["access_token"]
        cls.exp   = time.time() + body["expires_in"]
        return cls.token


async def post_chat(chat_id: str, text: str):
    url = f"{_GRAPH_BASE}/chats/{chat_id}/messages"
    headers = {
        "Authorization": f"Bearer {_TokenCache.get()}",
        "Content-Type":  "application/json",
    }
    payload = {"body": {"contentType": "text", "content": text}}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, json=payload, headers=headers)
    r.raise_for_status()
    return r.json()

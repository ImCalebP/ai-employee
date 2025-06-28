"""
common.teams_client
===================

Very small helper – just enough to POST a message into a Teams chat
using the **delegated** Graph access token that common.graph_auth
already refreshes and caches.

Dependencies
------------
* common.graph_auth.get_access_token()  → returns (access_token, ttl)
* httpx (async client)
"""

import httpx
from common import graph_auth

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


async def post_chat(chat_id: str, text: str) -> dict:
    """
    Send `text` into the chat identified by `chat_id`.

    Parameters
    ----------
    chat_id : str
        The Teams conversation ID (e.g. 19:abc123…@thread.v2)
    text : str
        Plain-text message to post.

    Returns
    -------
    dict
        The JSON response from Microsoft Graph (created chatMessage).
    """
    access_token, _ = graph_auth.get_access_token()  # delegated token

    url = f"{_GRAPH_BASE}/chats/{chat_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "body": {
            "contentType": "text",
            "content": text,
        }
    }

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json=payload, headers=headers)
    resp.raise_for_status()
    return resp.json()

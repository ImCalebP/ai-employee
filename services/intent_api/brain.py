"""
services.intent_api.brain
=========================

FastAPI entry-point for the Teams ↔ OpenAI agent.

Routes
------
GET  /                  → health-check
GET  /auth/login        → start Microsoft OAuth (PKCE)
GET  /auth/callback     → finish OAuth, save refresh-token (manual token exchange)
POST /webhook           → Power Automate sends {conversationId, messageId}
                          • fetch message, ask OpenAI, reply in Teams
"""

from fastapi import FastAPI, APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel
from msal import ConfidentialClientApplication
from openai import OpenAI
import os, asyncio, logging, httpx

# ──────────────────────────────────────────────────────────────
# 1.  Helpers in common/
# ──────────────────────────────────────────────────────────────
from common import graph_auth
from common.graph_auth import _save_refresh_token          # store RT
from common.teams_client import post_chat                  # send reply to Teams

# ──────────────────────────────────────────────────────────────
# 2.  OpenAI client (new ≥1.x SDK)
# ──────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY env var missing")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

async def ask_openai(prompt: str, model: str = "gpt-4o") -> str:
    loop = asyncio.get_event_loop()

    def _call():
        resp = openai_client.chat.completions.create(
            model=model,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content

    return await loop.run_in_executor(None, _call)

# ──────────────────────────────────────────────────────────────
# 3.  FastAPI app & router
# ──────────────────────────────────────────────────────────────
app    = FastAPI(title="AI-Employee • Teams × OpenAI")
router = APIRouter()
logging.basicConfig(level=logging.INFO)

# OAuth / Graph settings
CLIENT_ID     = os.getenv("MS_CLIENT_ID")
CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET")
TENANT_ID     = os.getenv("MS_TENANT_ID")
AUTHORITY     = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES        = ["Chat.ReadWrite", "Mail.Send"]
REDIRECT_URI  = os.getenv(
    "REDIRECT_URI",
    "https://ai-employee-28l9.onrender.com/auth/callback",
)

_flow_cache: dict[str, dict] = {}     # state → full MSAL flow

def msal_app() -> ConfidentialClientApplication:
    return ConfidentialClientApplication(
        client_id=CLIENT_ID,
        client_credential=CLIENT_SECRET,
        authority=AUTHORITY,
    )

# ───────────  AUTH ENDPOINTS  ───────────
@router.get("/auth/login")
def auth_login():
    flow = msal_app().initiate_auth_code_flow(
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )
    _flow_cache[flow["state"]] = flow            # keep verifier + everything
    return RedirectResponse(flow["auth_uri"])


@router.get("/auth/callback")
def auth_callback(request: Request):
    """Manual token exchange with PKCE (avoids msal bug)."""
    code  = request.query_params.get("code")
    state = request.query_params.get("state")

    if not code or not state or state not in _flow_cache:
        return HTMLResponse("<h3>Invalid or expired login session.</h3>", status_code=400)

    flow          = _flow_cache.pop(state)
    code_verifier = flow.get("code_verifier")

    token_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  REDIRECT_URI,
        "scope":         " ".join(SCOPES),
        "code_verifier": code_verifier,
    }

    with httpx.Client(timeout=10) as client:
        resp = client.post(token_url, data=data)

    if resp.status_code != 200:
        return HTMLResponse(
            f"<h3>Token request failed:</h3><pre>{resp.text}</pre>",
            status_code=resp.status_code,
        )

    tok = resp.json()
    if "refresh_token" in tok:
        _save_refresh_token(tok["refresh_token"])
        return HTMLResponse("<h2>✅ Login successful – you may close this tab.</h2>")

    return HTMLResponse(f"<pre>{tok}</pre>", status_code=400)


# ───────────  TEAMS WEBHOOK  ───────────
class TeamsWebhookPayload(BaseModel):
    messageId:      str
    conversationId: str

@router.post("/webhook")
async def webhook(payload: TeamsWebhookPayload):
    chat_id = payload.conversationId
    msg_id  = payload.messageId
    logging.info("→ webhook chat=%s msg=%s", chat_id, msg_id)

    # 1️⃣ Graph token
    try:
        access_token, _ = graph_auth.get_access_token()
    except RuntimeError as e:
        raise HTTPException(401, f"{e} – visit /auth/login once.") from e

    # 2️⃣ Get Teams message
    url = f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages/{msg_id}"
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, headers=headers)
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text)

    body   = r.json()
    text   = (body.get("body") or {}).get("content", "").strip()
    sender = (body.get("from") or {}).get("user", {}).get("displayName", "_")

    if not text or sender.lower().startswith("ai-employee"):
        return {"status": "ignored"}

    # 3️⃣ Ask OpenAI
    reply = await ask_openai(text)

    # 4️⃣ Post reply
    await post_chat(chat_id, reply)

    return {"status": "replied", "reply": reply}


# ───────────  HEALTH CHECK  ───────────
@router.get("/")
def root():
    return {"ok": True, "msg": "AI-Employee running"}

app.include_router(router)

# ───────────  For local runs  ───────────
if __name__ == "__main__":
    import uvicorn, sys
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("services.intent_api.brain:app",
                host="0.0.0.0", port=port,
                reload="--reload" in sys.argv)

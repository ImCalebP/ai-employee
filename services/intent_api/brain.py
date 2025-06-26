"""
services.intent_api.brain
=========================

FastAPI entry-point for the Teams ↔ OpenAI agent.

Routes
------
GET  /                  Health-check
GET  /auth/login        Start Microsoft OAuth
GET  /auth/callback     Finish OAuth, store refresh-token in Supabase
POST /webhook           Power Automate sends {conversationId, messageId}
                        → fetch message, ask OpenAI, reply in Teams
"""

from fastapi import FastAPI, APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel
from msal import ConfidentialClientApplication
import openai, os, asyncio, logging, httpx

# ──────────────────────────────────────────────────────────────
# 1.  Helpers already in your repo
# ──────────────────────────────────────────────────────────────
from common import graph_auth                      # token cache utils
from common.graph_auth import _save_refresh_token  # refresh-token saver
from common.teams_client import post_chat                 # minimal send helper

# ──────────────────────────────────────────────────────────────
# 2.  OpenAI wrapper
# ──────────────────────────────────────────────────────────────
openai.api_key = os.getenv("OPENAI_API_KEY") or ""
if not openai.api_key:
    raise RuntimeError("OPENAI_API_KEY env var missing")

async def ask_openai(prompt: str, model: str = "gpt-4o") -> str:
    """Non-blocking OpenAI call."""
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(
        None,
        lambda: openai.ChatCompletion.create(
            model=model,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        ),
    )
    return res["choices"][0]["message"]["content"]


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

_flow_cache: dict[str, dict] = {}   # state → full flow data

def msal_app() -> ConfidentialClientApplication:
    return ConfidentialClientApplication(
        client_id=CLIENT_ID,
        client_credential=CLIENT_SECRET,
        authority=AUTHORITY,
    )

# ─────────── Auth endpoints ───────────
@router.get("/auth/login")
def auth_login():
    flow = msal_app().initiate_auth_code_flow(
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )
    _flow_cache[flow["state"]] = flow        # store the entire flow (contains code_verifier)
    return RedirectResponse(flow["auth_uri"])


@router.get("/auth/callback")
def auth_callback(request: Request):
    code  = request.query_params.get("code")
    state = request.query_params.get("state")

    if not code or not state or state not in _flow_cache:
        return HTMLResponse("<h3>Invalid or expired login session.</h3>", status_code=400)

    flow = _flow_cache.pop(state)            # retrieve saved flow → has code_verifier
    result = msal_app().acquire_token_by_authorization_code(
        code,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
        code_verifier=flow.get("code_verifier"),   # ← PKCE fix
    )

    if "refresh_token" in result:
        _save_refresh_token(result["refresh_token"])
        return HTMLResponse("<h2>✅ Login successful – you can close this tab.</h2>")

    return HTMLResponse(f"<pre>{result}</pre>", status_code=400)


# ─────────── Teams webhook ───────────
class TeamsWebhookPayload(BaseModel):
    messageId:      str
    conversationId: str

@router.post("/webhook")
async def webhook(payload: TeamsWebhookPayload):
    chat_id = payload.conversationId
    msg_id  = payload.messageId
    logging.info("→ webhook chat=%s msg=%s", chat_id, msg_id)

    # 1️⃣  Graph access-token
    try:
        access_token, _ = graph_auth.get_access_token()
    except RuntimeError as e:
        raise HTTPException(401, f"{e} – visit /auth/login once.") from e

    # 2️⃣  Fetch original Teams message
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

    # 3️⃣  Ask OpenAI
    reply = await ask_openai(text)

    # 4️⃣  Send reply
    await post_chat(chat_id, reply)

    return {"status": "replied", "reply": reply}


# ─────────── Health-check ───────────
@router.get("/")
def root():
    return {"ok": True, "msg": "AI-Employee running"}

app.include_router(router)

# ─────────── For local `python brain.py` runs ───────────
if __name__ == "__main__":
    import uvicorn, sys
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("services.intent_api.brain:app",
                host="0.0.0.0", port=port,
                reload="--reload" in sys.argv)

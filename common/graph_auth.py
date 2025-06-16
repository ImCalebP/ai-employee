import os, json, base64, time
from typing import Tuple
from cryptography.fernet import Fernet
from msal import ConfidentialClientApplication

CLIENT_ID     = os.getenv("MS_CLIENT_ID")
CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET")
TENANT_ID     = os.getenv("MS_TENANT_ID")
AUTHORITY     = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES        = ["https://graph.microsoft.com/.default", "offline_access", "Chat.ReadWrite"]

FERNET = Fernet(base64.urlsafe_b64encode(os.getenv("TOKEN_ENCRYPT_KEY").encode().ljust(32)[:32]))

def _save_refresh_token(rt: str):
    enc = FERNET.encrypt(rt.encode()).decode()
    with open("/tmp/rt.enc", "w") as f:  # ‼️ simple file; swap for Supabase if you like
        f.write(enc)

def _load_refresh_token() -> str | None:
    try:
        enc = open("/tmp/rt.enc").read()
        return FERNET.decrypt(enc.encode()).decode()
    except FileNotFoundError:
        return None

def get_msal_app() -> ConfidentialClientApplication:
    return ConfidentialClientApplication(
        client_id=CLIENT_ID,
        client_credential=CLIENT_SECRET,
        authority=AUTHORITY,
    )

def exchange_code_for_tokens(code: str, redirect_uri: str):
    app = get_msal_app()
    result = app.acquire_token_by_authorization_code(code, scopes=SCOPES, redirect_uri=redirect_uri)
    if "refresh_token" in result:
        _save_refresh_token(result["refresh_token"])

def get_access_token() -> Tuple[str, int]:
    """Return access_token + expiry epoch seconds."""
    app = get_msal_app()

    # 1) try cached refresh token
    rt = _load_refresh_token()
    if rt:
        token = app.acquire_token_by_refresh_token(rt, scopes=SCOPES)
        if "access_token" in token:
            return token["access_token"], token["expires_in"]

    # 2) no token yet → caller must redirect user to /auth/login
    raise RuntimeError("No refresh token stored – complete interactive login first.")

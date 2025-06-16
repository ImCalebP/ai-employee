"""
Delegated Microsoft Graph auth helper
=====================================
• Stores an encrypted refresh-token in /tmp/rt.enc (swap for Supabase if desired)
• Automatically refreshes access tokens for the Chat.ReadWrite scope
"""

import os, base64
from typing import Tuple
from cryptography.fernet import Fernet
from msal import ConfidentialClientApplication

# ───── Environment secrets ───────────────────────────────────────────────
CLIENT_ID        = os.getenv("MS_CLIENT_ID")
CLIENT_SECRET    = os.getenv("MS_CLIENT_SECRET")
TENANT_ID        = os.getenv("MS_TENANT_ID")
TOKEN_ENCRYPT_KEY = os.getenv("TOKEN_ENCRYPT_KEY")  # 32-char random string

# ───── Authority & scopes ────────────────────────────────────────────────
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"

# 👉 **ONLY** delegated Graph scopes here.
#     MSAL will implicitly add "openid profile offline_access" in the auth-code flow.
SCOPES = ["Chat.ReadWrite"]

# ───── Encrypt / decrypt refresh token ───────────────────────────────────
FERNET = Fernet(
    base64.urlsafe_b64encode(TOKEN_ENCRYPT_KEY.encode().ljust(32)[:32])
)

def _save_refresh_token(rt: str):
    with open("/tmp/rt.enc", "w") as f:
        f.write(FERNET.encrypt(rt.encode()).decode())


def _load_refresh_token() -> str | None:
    try:
        enc = open("/tmp/rt.enc").read()
        return FERNET.decrypt(enc.encode()).decode()
    except FileNotFoundError:
        return None


# ───── MSAL app factory ──────────────────────────────────────────────────
def get_msal_app() -> ConfidentialClientApplication:
    return ConfidentialClientApplication(
        client_id=CLIENT_ID,
        client_credential=CLIENT_SECRET,
        authority=AUTHORITY,
    )


# ───── Exchange auth-code for tokens (called from /auth/callback) ────────
def exchange_code_for_tokens(code: str, redirect_uri: str):
    app = get_msal_app()
    result = app.acquire_token_by_authorization_code(
        code,
        scopes=SCOPES,          # MSAL auto-adds openid profile offline_access
        redirect_uri=redirect_uri,
    )
    if "refresh_token" in result:
        _save_refresh_token(result["refresh_token"])
    else:
        raise RuntimeError(f"Auth-code exchange failed: {result.get('error_description')}")


# ───── Get fresh access token on demand ───────────────────────────────────
def get_access_token() -> Tuple[str, int]:
    """
    Returns (access_token, expires_in_seconds).
    Raises RuntimeError if the user has never completed /auth/login.
    """
    rt = _load_refresh_token()
    if not rt:
        raise RuntimeError("No refresh token stored – complete interactive login first.")

    app = get_msal_app()
    token = app.acquire_token_by_refresh_token(rt, scopes=SCOPES)

    if "access_token" in token:
        return token["access_token"], token["expires_in"]

    # Refresh token expired / revoked – delete cached file and fail hard
    try:
        os.remove("/tmp/rt.enc")
    except FileNotFoundError:
        pass
    raise RuntimeError(f"Failed to refresh token: {token.get('error_description')}")

"""
Delegated Microsoft Graph auth helper test
=====================================
• Stores a refresh-token in Supabase under 'tokens' table
• Automatically refreshes access tokens for the Chat.ReadWrite scope
"""

import os
from typing import Tuple
from msal import ConfidentialClientApplication
from supabase import create_client

# ───── Environment variables ─────────────────────────────────────────────
CLIENT_ID     = os.getenv("MS_CLIENT_ID")
CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET")
TENANT_ID     = os.getenv("MS_TENANT_ID")
SUPABASE_URL  = os.getenv("SUPABASE_URL")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY")

# ───── Supabase client ───────────────────────────────────────────────────
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ───── MS Graph scopes and authority ─────────────────────────────────────
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = ["Chat.ReadWrite", "Mail.Send"]

# ───── Supabase helpers for refresh token ────────────────────────────────
def _save_refresh_token(rt: str):
    existing = supabase.table("tokens").select("id").eq("name", "teams").execute()
    if existing.data:
        supabase.table("tokens").update({"refresh_token": rt}).eq("name", "teams").execute()
    else:
        supabase.table("tokens").insert({"name": "teams", "refresh_token": rt}).execute()


def _load_refresh_token() -> str | None:
    result = supabase.table("tokens").select("refresh_token").eq("name", "teams").limit(1).execute()
    if result.data and result.data[0].get("refresh_token"):
        return result.data[0]["refresh_token"]
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
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )
    if "refresh_token" in result:
        _save_refresh_token(result["refresh_token"])
    else:
        raise RuntimeError(f"Auth-code exchange failed: {result.get('error_description')}")

# ───── Get fresh access token on demand ──────────────────────────────────
def get_access_token() -> Tuple[str, int]:
    """
    Returns (access_token, expires_in_seconds).
    Raises RuntimeError if no refresh token is stored.
    """
    rt = _load_refresh_token()
    if not rt:
        raise RuntimeError("No refresh token stored – complete interactive login first.")

    app = get_msal_app()
    result = app.acquire_token_by_refresh_token(rt, scopes=SCOPES)

    if "access_token" in result:
        new_rt = result.get("refresh_token")
        if new_rt and new_rt != rt:
            _save_refresh_token(new_rt)
        return result["access_token"], result["expires_in"]

    raise RuntimeError(f"Failed to refresh token: {result.get('error_description')}")

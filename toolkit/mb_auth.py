"""Shared Metabase auth helper. Reads mode/credentials from environment (.env)."""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

MB_BASE_URL = os.environ["MB_BASE_URL"].rstrip("/")
AUTH_MODE = os.environ.get("MB_AUTH_MODE", "session_cookie")


def get_headers(cookie_override=None):
    """cookie_override lets a caller (e.g. a --cookie CLI flag) supply the
    session token directly for this one run, without touching .env."""
    if AUTH_MODE == "session_cookie":
        token = (cookie_override or os.environ.get("MB_SESSION_TOKEN", "")).strip()
        if not token:
            raise RuntimeError(
                "No session cookie provided — pass --cookie '<value>' or set "
                "MB_SESSION_TOKEN in .env. See .env.example for how to get it."
            )
        return {"X-Metabase-Session": token}

    if AUTH_MODE == "api_key":
        key = os.environ.get("MB_API_KEY", "").strip()
        if not key:
            raise RuntimeError("MB_API_KEY is empty.")
        return {"X-Api-Key": key}

    if AUTH_MODE == "password":
        email = os.environ.get("MB_EMAIL", "").strip()
        password = os.environ.get("MB_PASSWORD", "").strip()
        if not email or not password:
            raise RuntimeError("MB_EMAIL / MB_PASSWORD are empty.")
        r = requests.post(
            f"{MB_BASE_URL}/api/session",
            json={"username": email, "password": password},
            timeout=30,
        )
        r.raise_for_status()
        return {"X-Metabase-Session": r.json()["id"]}

    raise ValueError(f"Unknown MB_AUTH_MODE: {AUTH_MODE!r} (expected session_cookie | api_key | password)")

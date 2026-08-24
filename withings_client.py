"""Withings Public API client for Body Comp measurements."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = os.getenv("WITHINGS_API_BASE", "https://wbsapi.withings.net").rstrip("/")
TOKEN_FILE = Path(os.getenv("WITHINGS_TOKEN_FILE", "withings_tokens.json"))

BODY_COMP_MEASURE_TYPES = [1, 5, 6, 8, 76, 77, 88, 91]


def _client_id():
    value = os.getenv("WITHINGS_CLIENT_ID")
    if not value:
        raise ValueError("Set WITHINGS_CLIENT_ID in .env")
    return value


def _client_secret():
    value = os.getenv("WITHINGS_CLIENT_SECRET")
    if not value:
        raise ValueError("Set WITHINGS_CLIENT_SECRET in .env")
    return value


def _redirect_uri():
    value = os.getenv("WITHINGS_REDIRECT_URI")
    if not value:
        raise ValueError("Set WITHINGS_REDIRECT_URI in .env")
    return value


def sign(params: dict) -> str:
    parts = []
    for key in sorted(k for k in ("action", "client_id", "nonce", "timestamp") if k in params):
        parts.append(str(params[key]))
    payload = ",".join(parts).encode("utf-8")
    return hmac.new(_client_secret().encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _post(path: str, data: dict, access_token: str = None) -> dict:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    response = requests.post(f"{API_BASE}{path}", data=data, headers=headers, timeout=30)
    response.raise_for_status()
    payload = response.json()
    status = payload.get("status")
    if status not in (0, "0", None):
        raise RuntimeError(f"Withings API error status={status}: {payload}")
    return payload


def get_nonce() -> str:
    timestamp = int(time.time())
    params = {
        "action": "getnonce",
        "client_id": _client_id(),
        "timestamp": timestamp,
    }
    params["signature"] = sign(params)
    payload = _post("/v2/signature", params)
    return payload["body"]["nonce"]


def request_tokens_with_code(code: str) -> dict:
    nonce = get_nonce()
    params = {
        "action": "requesttoken",
        "client_id": _client_id(),
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(),
        "nonce": nonce,
    }
    params["signature"] = sign(params)
    payload = _post("/v2/oauth2", params)
    return payload["body"]


def load_tokens() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    return json.loads(TOKEN_FILE.read_text())


def save_tokens(tokens: dict):
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2))
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass


def refresh_tokens() -> dict:
    tokens = load_tokens()
    if not tokens or not tokens.get("refresh_token"):
        raise ValueError("No Withings refresh token found. Run python3 withings_auth.py")
    nonce = get_nonce()
    params = {
        "action": "requesttoken",
        "client_id": _client_id(),
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "nonce": nonce,
    }
    params["signature"] = sign(params)
    payload = _post("/v2/oauth2", params)
    new_tokens = payload["body"]
    save_tokens(new_tokens)
    return new_tokens


def get_valid_token() -> str:
    # Access tokens are short-lived and Withings rotates refresh tokens.
    # Refresh every client run and persist the returned refresh token.
    return refresh_tokens()["access_token"]


class WithingsClient:
    def __init__(self):
        self.access_token = get_valid_token()

    def get_measurements(self, lastupdate: int = None, meastypes: list[int] = None) -> dict:
        data = {
            "action": "getmeas",
            "category": 1,
        }
        if lastupdate is not None:
            data["lastupdate"] = int(lastupdate)
        if meastypes:
            data["meastypes"] = ",".join(str(t) for t in meastypes)

        measuregrps = []
        offset = None
        while True:
            page = dict(data)
            if offset is not None:
                page["offset"] = offset
            payload = _post("/measure", page, access_token=self.access_token)
            body = payload.get("body", {})
            measuregrps.extend(body.get("measuregrps", []))
            if not body.get("more"):
                return {"measuregrps": measuregrps}
            offset = body.get("offset")
            if offset is None:
                return {"measuregrps": measuregrps}

    def get_body_comp_measurements(self, lastupdate: int = None) -> dict:
        return self.get_measurements(lastupdate=lastupdate, meastypes=BODY_COMP_MEASURE_TYPES)

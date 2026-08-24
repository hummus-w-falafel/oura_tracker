"""Withings OAuth2 flow for local personal use.

Run this once after setting WITHINGS_CLIENT_ID, WITHINGS_CLIENT_SECRET, and
WITHINGS_REDIRECT_URI in .env. The callback server listens on port 8081 and is
intended to sit behind Tailscale Funnel.
"""

from __future__ import annotations

import os
import secrets
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv

from withings_client import TOKEN_FILE, request_tokens_with_code, save_tokens

load_dotenv()

AUTH_URL = "https://account.withings.com/oauth2_user/authorize2"
DEFAULT_SCOPES = "user.info,user.metrics"
CALLBACK_HOST = "0.0.0.0"
CALLBACK_PORT = 8081

auth_code = None
auth_state = None
server_done = threading.Event()


def build_authorization_url(state: str):
    client_id = os.getenv("WITHINGS_CLIENT_ID")
    redirect_uri = os.getenv("WITHINGS_REDIRECT_URI")
    scopes = os.getenv("WITHINGS_SCOPES", DEFAULT_SCOPES)
    if not client_id or not redirect_uri:
        raise ValueError("Set WITHINGS_CLIENT_ID and WITHINGS_REDIRECT_URI in .env")
    params = {
        "response_type": "code",
        "client_id": client_id,
        "scope": scopes,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


class CallbackHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(parsed.query)
        returned_state = params.get("state", [""])[0]
        if returned_state != auth_state:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"State mismatch. Authorization rejected.")
            server_done.set()
            return

        if "code" not in params:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Authorization failed: missing code.")
            server_done.set()
            return

        auth_code = params["code"][0]
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Withings authorization received. You can close this tab.</h2>")
        server_done.set()

    def log_message(self, format, *args):
        pass


def run():
    global auth_code, auth_state
    auth_code = None
    auth_state = secrets.token_urlsafe(24)
    server_done.clear()
    url = build_authorization_url(auth_state)
    print("Opening browser for Withings authorization...")
    print(f"If browser does not open, visit:\n{url}\n")
    webbrowser.open(url)

    server = HTTPServer((CALLBACK_HOST, CALLBACK_PORT), CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print(f"Waiting for callback on port {CALLBACK_PORT}...")
    server_done.wait(timeout=120)
    server.shutdown()

    if not auth_code:
        print("Authorization timed out or failed.")
        return

    print("Exchanging authorization code for tokens...")
    tokens = request_tokens_with_code(auth_code)
    save_tokens(tokens)
    print(f"Tokens saved to {TOKEN_FILE}")
    print("Withings authentication complete.")


if __name__ == "__main__":
    run()

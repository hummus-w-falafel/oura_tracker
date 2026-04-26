"""
Oura OAuth2 authentication flow.
Run this once to get your access + refresh tokens saved to tokens.json.
"""

import json
import os
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("OURA_CLIENT_ID")
CLIENT_SECRET = os.getenv("OURA_CLIENT_SECRET")
REDIRECT_URI = os.getenv("OURA_REDIRECT_URI", "http://localhost:8080/callback")
TOKEN_FILE = "tokens.json"

SCOPES = "personal email daily heartrate workout tag session spo2 heart_health stress"

AUTH_URL = "https://cloud.ouraring.com/oauth/authorize"
TOKEN_URL = "https://api.ouraring.com/oauth/token"

auth_code = None
server_done = threading.Event()


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Authorization successful! You can close this tab.</h2>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<h2>Authorization failed.</h2>")
        server_done.set()

    def log_message(self, format, *args):
        pass  # suppress server logs


def get_authorization_url():
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": "h_tracker",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_code_for_tokens(code):
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        auth=(CLIENT_ID, CLIENT_SECRET),
    )
    response.raise_for_status()
    return response.json()


def refresh_access_token():
    tokens = load_tokens()
    if not tokens or "refresh_token" not in tokens:
        raise ValueError("No refresh token found. Run auth.py to re-authenticate.")
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
        },
        auth=(CLIENT_ID, CLIENT_SECRET),
    )
    response.raise_for_status()
    new_tokens = response.json()
    save_tokens(new_tokens)
    return new_tokens


def save_tokens(tokens):
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)
    print(f"Tokens saved to {TOKEN_FILE}")


def load_tokens():
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE) as f:
        return json.load(f)


def get_valid_token():
    """Returns a valid access token, refreshing if needed."""
    tokens = load_tokens()
    if not tokens:
        raise ValueError("Not authenticated. Run: python auth.py")
    # Try refreshing to ensure token is fresh
    try:
        new_tokens = refresh_access_token()
        return new_tokens["access_token"]
    except Exception:
        return tokens["access_token"]


def run():
    url = get_authorization_url()
    print(f"Opening browser for Oura authorization...")
    print(f"If browser doesn't open, visit:\n{url}\n")
    webbrowser.open(url)

    server = HTTPServer(("localhost", 8080), CallbackHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    print("Waiting for authorization callback...")
    server_done.wait(timeout=120)
    server.shutdown()

    if not auth_code:
        print("Authorization timed out or failed.")
        return

    print("Exchanging code for tokens...")
    tokens = exchange_code_for_tokens(auth_code)
    save_tokens(tokens)
    print("Authentication complete.")


if __name__ == "__main__":
    run()

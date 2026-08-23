"""One-time LOCAL helper -- run this on your own machine, never in the cloud routine --
to obtain a TikTok OAuth refresh token for the video.upload (draft-inbox) scope.

Prerequisites (see README.md's TikTok section for the full walkthrough):
  - A TikTok developer app with the Content Posting API product added and the
    video.upload scope requested.
  - That app's "Redirect URI" setting must include exactly:
      http://localhost:8080/callback
    UNCONFIRMED: TikTok's real requirements here weren't reachable when this was
    written (see pipeline/tiktok_upload.py's docstring) -- some OAuth providers
    reject a plain http:// loopback redirect and require https. If TikTok rejects
    this URI when you try to add it, or the browser step below never reaches this
    script, use the manual fallback: register whatever https:// placeholder TikTok
    will accept, open the authorize URL yourself, and when the browser lands on a
    "can't be reached" page, copy the `code=...` value straight out of the address
    bar and pass it to this script with --code instead of waiting for the callback.

Usage:
  python scripts/get_tiktok_refresh_token.py <client_key> <client_secret>

Opens a browser for you to sign in and grant access, catches the redirect on
localhost, exchanges the code for tokens, and prints the refresh token. Put it
straight into TIKTOK_REFRESH_TOKEN (secrets.env locally, or the repo secret in the
cloud) -- it is not saved anywhere by this script.
"""
import json
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

REDIRECT_URI = "http://localhost:8080/callback"
AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
SCOPE = "video.upload"


def _authorize_url(client_key, state):
    params = {
        "client_key": client_key,
        "scope": SCOPE,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _catch_redirect_code():
    """Runs a tiny local HTTP server just long enough to catch TikTok's redirect and
    pull the `code` query param out of it -- same shape as the YouTube helper's
    run_local_server(), just hand-rolled since TikTok has no equivalent official
    Python SDK to lean on."""
    caught = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            caught["code"] = params.get("code", [None])[0]
            caught["error"] = params.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Done -- you can close this tab and return to the terminal.")

        def log_message(self, *args):
            pass  # silence the default per-request stderr logging

    server = HTTPServer(("localhost", 8080), Handler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    thread.join(timeout=180)
    server.server_close()
    return caught.get("code"), caught.get("error")


def _exchange_code(client_key, client_secret, code):
    body = urllib.parse.urlencode({
        "client_key": client_key,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def main():
    if len(sys.argv) not in (3, 4) or (len(sys.argv) == 4 and sys.argv[3] != "--code"):
        print("usage: python scripts/get_tiktok_refresh_token.py <client_key> <client_secret> [--code <code>]")
        sys.exit(1)

    client_key, client_secret = sys.argv[1], sys.argv[2]

    if len(sys.argv) == 4:
        # Manual fallback -- see module docstring's UNCONFIRMED note.
        code = input("Paste the `code` value from the redirect URL: ").strip()
    else:
        url = _authorize_url(client_key, state="loopedin-setup")
        print(f"Opening your browser to approve access. If it doesn't open, go to:\n{url}\n")
        webbrowser.open(url)
        code, error = _catch_redirect_code()
        if error:
            print(f"TikTok returned an error: {error}")
            sys.exit(1)
        if not code:
            print(
                "No redirect caught within 3 minutes. If TikTok rejected "
                f"{REDIRECT_URI} as a registered redirect URI, see this script's "
                "module docstring for the manual --code fallback."
            )
            sys.exit(1)

    result = _exchange_code(client_key, client_secret, code)
    if "refresh_token" not in result:
        print(f"Token exchange did not return a refresh_token: {result}")
        sys.exit(1)

    print("\nRefresh token (put this in TIKTOK_REFRESH_TOKEN):\n")
    print(result["refresh_token"])


if __name__ == "__main__":
    main()

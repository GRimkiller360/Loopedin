"""One-time LOCAL helper -- run this on your own machine, never in the cloud routine --
to obtain a YouTube OAuth refresh token.

Prerequisites:
  - A Desktop-app OAuth client (client_id + client_secret) from Google Cloud Console.
  - The OAuth consent screen already pushed to "In production" (see README.md) --
    otherwise the refresh token this prints will expire after 7 days.

Usage:
  pip install google-auth-oauthlib
  python scripts/get_refresh_token.py <client_id> <client_secret>

Opens a browser for you to sign in and grant access, then prints the refresh token.
Put it straight into YOUTUBE_REFRESH_TOKEN (secrets.env locally, or the routine's
environment variable in the cloud) -- it is not saved anywhere by this script.
"""
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube",  # full manage -- needed for playlists.insert
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def main():
    if len(sys.argv) != 3:
        print("usage: python scripts/get_refresh_token.py <client_id> <client_secret>")
        sys.exit(1)

    client_id, client_secret = sys.argv[1], sys.argv[2]
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0)
    print("\nRefresh token (put this in YOUTUBE_REFRESH_TOKEN):\n")
    print(creds.refresh_token)


if __name__ == "__main__":
    main()

"""
One-time Google Calendar OAuth2 setup for Sunday.

Usage:
  python3 setup_gcal_auth.py

This opens a browser where you log in with REPLACE_ME and grant
Calendar access. The resulting token is saved to calendar_token.json in the
same directory. Copy that file to the Pi:

  scp calendar_token.json djpi@djpi.local:/home/djpi/sunday/

Also copy the client_secret.json file:

  scp calendar_client_secret.json djpi@djpi.local:/home/djpi/sunday/

Requirements:
  pip3 install google-auth google-auth-oauthlib google-api-python-client
"""

import json
import os
import sys
from pathlib import Path

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
HERE = Path(__file__).parent
TOKEN_FILE = HERE / "calendar_token.json"
SECRET_FILE = HERE / "calendar_client_secret.json"

# ── Check dependencies ────────────────────────────────────────────────────────
try:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
except ImportError:
    print("Missing dependencies. Install with:")
    print("  pip3 install google-auth google-auth-oauthlib google-api-python-client")
    sys.exit(1)

# ── Check for existing valid token ────────────────────────────────────────────
if TOKEN_FILE.exists():
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), [CALENDAR_SCOPE])
    if creds.valid:
        print(f"Token already valid at {TOKEN_FILE}")
        # Quick test
        service = build("calendar", "v3", credentials=creds)
        cal = service.calendars().get(calendarId="primary").execute()
        print(f"Calendar access confirmed: {cal.get('summary', 'primary')}")
        sys.exit(0)
    elif creds.expired and creds.refresh_token:
        print("Refreshing expired token...")
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
        print(f"Token refreshed at {TOKEN_FILE}")
        sys.exit(0)

# ── Need full OAuth flow ───────────────────────────────────────────────────────
if not SECRET_FILE.exists():
    print(f"\nERROR: {SECRET_FILE} not found.\n")
    print("Create OAuth2 credentials in Google Cloud Console:")
    print("  1. Go to: https://console.cloud.google.com/apis/credentials?project=gmail-meet")
    print("  2. Click 'Create Credentials' → 'OAuth client ID'")
    print("  3. Application type: Desktop app — Name: Sunday Calendar")
    print("  4. Download JSON → save as:", SECRET_FILE)
    print("\nThen re-run this script.")
    sys.exit(1)

print("Opening browser for Google authorization...")
print("Log in as REPLACE_ME and grant Calendar access.\n")

flow = InstalledAppFlow.from_client_secrets_file(str(SECRET_FILE), [CALENDAR_SCOPE])
creds = flow.run_local_server(port=0)

TOKEN_FILE.write_text(creds.to_json())
print(f"\nToken saved to {TOKEN_FILE}")

# Quick test
try:
    service = build("calendar", "v3", credentials=creds)
    cal = service.calendars().get(calendarId="primary").execute()
    print(f"Calendar access confirmed: {cal.get('summary', 'primary')}")
    print("\nNext step — copy token to the Pi:")
    print(f"  scp {TOKEN_FILE} djpi@djpi.local:/home/djpi/sunday/")
except Exception as e:
    print(f"Warning: quick test failed: {e}")
    print("Token saved — try copying to Pi anyway.")

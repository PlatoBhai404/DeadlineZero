import os
import base64
import json
from email import message_from_bytes
from dotenv import load_dotenv

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

CLIENT_CONFIG = {
    "web": {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [os.getenv("REDIRECT_URI")],
    }
}


def get_authorization_url(session: dict) -> str:
    """
    Creates the Google OAuth authorization URL and stores the state
    in the Flask session. Returns the URL to redirect the user to.
    """
    flow = Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri=os.getenv("REDIRECT_URI")
    )
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent"
    )
    session["oauth_state"] = state
    return authorization_url


def exchange_code_for_tokens(session: dict, authorization_response: str) -> dict:
    """
    Exchanges the OAuth authorization code for access and refresh tokens.
    Stores tokens in the Flask session and returns them as a dict.
    """
    flow = Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        state=session.get("oauth_state"),
        redirect_uri=os.getenv("REDIRECT_URI")
    )
    flow.fetch_token(authorization_response=authorization_response)
    credentials = flow.credentials
    tokens = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }
    session["gmail_tokens"] = tokens
    return tokens


def get_gmail_service(tokens: dict):
    """
    Builds and returns an authenticated Gmail API service object
    using stored tokens.
    """
    credentials = Credentials(
        token=tokens["token"],
        refresh_token=tokens.get("refresh_token"),
        token_uri=tokens["token_uri"],
        client_id=tokens["client_id"],
        client_secret=tokens["client_secret"],
        scopes=tokens["scopes"],
    )
    service = build("gmail", "v1", credentials=credentials)
    return service


def fetch_recent_emails(tokens: dict, max_results: int = 50) -> list:
    """
    Fetches the most recent emails from the user's inbox.
    Returns a list of dicts with subject, sender, date, and body.
    """
    service = get_gmail_service(tokens)
    results = service.users().messages().list(
        userId="me",
        maxResults=max_results,
        labelIds=["INBOX"]
    ).execute()

    messages = results.get("messages", [])
    emails = []

    for msg in messages:
        try:
            full_msg = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="full"
            ).execute()

            headers = {
                h["name"]: h["value"]
                for h in full_msg.get("payload", {}).get("headers", [])
            }

            subject = headers.get("Subject", "(No subject)")
            sender = headers.get("From", "(Unknown sender)")
            date = headers.get("Date", "")
            body = _extract_body(full_msg.get("payload", {}))

            if body.strip():
                emails.append({
                    "subject": subject,
                    "sender": sender,
                    "date": date,
                    "body": body[:2000]  # cap at 2000 chars per email
                })
        except Exception:
            continue

    return emails


def build_email_text_block(emails: list) -> str:
    """
    Converts a list of email dicts into a single text block
    to send to Gemini for task extraction.
    """
    blocks = []
    for i, email in enumerate(emails, 1):
        blocks.append(
            f"EMAIL {i}\n"
            f"From: {email['sender']}\n"
            f"Date: {email['date']}\n"
            f"Subject: {email['subject']}\n"
            f"Body: {email['body']}\n"
        )
    return "\n---\n".join(blocks)


def _extract_body(payload: dict) -> str:
    """
    Recursively extracts plain text body from a Gmail message payload.
    Handles both simple and multipart messages.
    """
    body = ""

    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    body += _decode_base64(data)
            elif "parts" in part:
                body += _extract_body(part)
    else:
        data = payload.get("body", {}).get("data", "")
        if data:
            body += _decode_base64(data)

    return body


def _decode_base64(data: str) -> str:
    """
    Decodes a base64url-encoded string as used by the Gmail API.
    Returns decoded text or empty string on failure.
    """
    try:
        decoded_bytes = base64.urlsafe_b64decode(data + "==")
        return decoded_bytes.decode("utf-8", errors="ignore")
    except Exception:
        return ""
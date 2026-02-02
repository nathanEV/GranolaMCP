#!/usr/bin/env python3
"""
Granola Meeting Email Automation

Polls for completed meetings and sends transcripts via AWS SES.
Designed to run via launchd every 5 minutes.

Usage:
    python email_on_complete.py              # Normal run
    python email_on_complete.py --dry-run    # List meetings without sending
    python email_on_complete.py --force ID   # Force send a specific meeting
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from granola_mcp.core.parser import GranolaParser
from granola_mcp.core.meeting import Meeting
from granola_mcp.cli.formatters.markdown import export_meeting_to_markdown
from granola_mcp.utils.config import load_config, get_cache_path

# Configuration
STATE_FILE = Path.home() / ".granola_email_state.json"
LOOKBACK_MINUTES = 30
TIMEZONE = ZoneInfo("America/Chicago")


def load_state() -> dict:
    """Load the state file tracking emailed meetings."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"emailed_ids": [], "last_run": None}


def save_state(state: dict) -> None:
    """Save the state file."""
    state["last_run"] = datetime.now(TIMEZONE).isoformat()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def get_email_config() -> dict:
    """Load email configuration from environment or .env file."""
    config = load_config()

    # Check environment variables first, then .env config
    return {
        "enabled": os.getenv("EMAIL_ENABLED", config.get("EMAIL_ENABLED", "false")).lower() == "true",
        "to": os.getenv("EMAIL_TO", config.get("EMAIL_TO", "")),
        "from": os.getenv("EMAIL_FROM", config.get("EMAIL_FROM", "")),
        "region": os.getenv("AWS_REGION", config.get("AWS_REGION", "us-east-1")),
    }


def should_email_meeting(meeting: Meeting, emailed_ids: list, cutoff_time: datetime) -> bool:
    """
    Determine if a meeting should be emailed.

    Criteria:
    - Has an end time in the past
    - End time is within the lookback window
    - Has transcript data
    - Not already emailed
    """
    if meeting.id in emailed_ids:
        return False

    if not meeting.has_transcript():
        return False

    end_time = meeting.end_time
    if end_time is None:
        return False

    # Ensure end_time is timezone-aware for comparison
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=TIMEZONE)

    now = datetime.now(TIMEZONE)

    # Meeting must have ended
    if end_time > now:
        return False

    # Meeting must have ended after cutoff (within lookback window)
    if end_time < cutoff_time:
        return False

    return True


def format_email_subject(meeting: Meeting) -> str:
    """Format the email subject line."""
    title = meeting.title or "Untitled Meeting"
    date_str = ""
    if meeting.start_time:
        date_str = meeting.start_time.strftime("%Y-%m-%d")
    return f"Granola Meeting: {title} - {date_str}"


def send_email_ses(to_addr: str, from_addr: str, subject: str, body: str, region: str) -> bool:
    """Send email via AWS SES."""
    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        print("ERROR: boto3 not installed. Run: pip install boto3", file=sys.stderr)
        return False

    try:
        client = boto3.client("ses", region_name=region)

        response = client.send_email(
            Source=from_addr,
            Destination={"ToAddresses": [to_addr]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
            },
        )

        message_id = response.get("MessageId", "unknown")
        print(f"  Email sent successfully (MessageId: {message_id})")
        return True

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_msg = e.response.get("Error", {}).get("Message", str(e))
        print(f"  ERROR: SES send failed ({error_code}): {error_msg}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  ERROR: Failed to send email: {e}", file=sys.stderr)
        return False


def process_meetings(dry_run: bool = False, force_id: str = None) -> int:
    """
    Main processing logic.

    Returns:
        Number of emails sent (or would be sent in dry-run)
    """
    # Load configuration
    config = load_config()
    email_config = get_email_config()

    if not dry_run and not email_config["enabled"]:
        print("Email automation is disabled. Set EMAIL_ENABLED=true in .env")
        return 0

    if not dry_run:
        if not email_config["to"]:
            print("ERROR: EMAIL_TO not configured", file=sys.stderr)
            return 0
        if not email_config["from"]:
            print("ERROR: EMAIL_FROM not configured", file=sys.stderr)
            return 0

    # Load state
    state = load_state()
    emailed_ids = set(state.get("emailed_ids", []))

    # Calculate cutoff time
    now = datetime.now(TIMEZONE)
    cutoff_time = now - timedelta(minutes=LOOKBACK_MINUTES)

    # Load meetings
    try:
        cache_path = get_cache_path(config)
        parser = GranolaParser(cache_path)
        meetings_data = parser.get_meetings()
    except Exception as e:
        print(f"ERROR: Failed to load Granola cache: {e}", file=sys.stderr)
        return 0

    # Convert to Meeting objects
    meetings = [Meeting(m) for m in meetings_data]

    # Find meetings to email
    to_email = []

    if force_id:
        # Force mode: find specific meeting
        for meeting in meetings:
            if meeting.id and meeting.id.startswith(force_id):
                to_email.append(meeting)
                break
        if not to_email:
            print(f"ERROR: Meeting not found: {force_id}", file=sys.stderr)
            return 0
    else:
        # Normal mode: find recently completed meetings
        for meeting in meetings:
            if should_email_meeting(meeting, emailed_ids, cutoff_time):
                to_email.append(meeting)

    if not to_email:
        print(f"No new meetings to email (checked {len(meetings)} meetings)")
        return 0

    print(f"Found {len(to_email)} meeting(s) to email:")

    sent_count = 0
    newly_emailed = []

    for meeting in to_email:
        title = meeting.title or "Untitled"
        meeting_id = meeting.id or "unknown"

        print(f"\n  [{meeting_id[:8]}] {title}")

        if dry_run:
            end_str = meeting.end_time.strftime("%H:%M") if meeting.end_time else "unknown"
            print(f"    End time: {end_str}")
            print(f"    Has transcript: {meeting.has_transcript()}")
            print(f"    Subject: {format_email_subject(meeting)}")
            sent_count += 1
        else:
            # Generate email content
            subject = format_email_subject(meeting)
            body = export_meeting_to_markdown(meeting)

            # Send email
            success = send_email_ses(
                to_addr=email_config["to"],
                from_addr=email_config["from"],
                subject=subject,
                body=body,
                region=email_config["region"],
            )

            if success:
                sent_count += 1
                newly_emailed.append(meeting_id)

    # Update state
    if newly_emailed and not dry_run:
        state["emailed_ids"] = list(emailed_ids | set(newly_emailed))
        save_state(state)
        print(f"\nState updated: {len(newly_emailed)} meeting(s) marked as emailed")

    return sent_count


def main():
    parser = argparse.ArgumentParser(
        description="Email Granola meeting transcripts automatically"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List meetings that would be emailed without sending",
    )
    parser.add_argument(
        "--force",
        metavar="MEETING_ID",
        help="Force send a specific meeting (partial ID match)",
    )

    args = parser.parse_args()

    print(f"Granola Email Automation - {datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 60)

    count = process_meetings(dry_run=args.dry_run, force_id=args.force)

    if args.dry_run:
        print(f"\n[DRY RUN] Would email {count} meeting(s)")
    else:
        print(f"\nEmailed {count} meeting(s)")

    return 0 if count >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())

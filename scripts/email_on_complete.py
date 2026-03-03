#!/usr/bin/env python3
"""
Granola Meeting Automation

Polls for completed meetings and:
1. Exports transcripts as markdown to ~/Documents/03-Knowledge-Base/meetings/
2. Sends transcripts via AWS SES (if EMAIL_ENABLED=true)

Designed to run via launchd every 5 minutes.

Usage:
    python email_on_complete.py              # Normal run
    python email_on_complete.py --dry-run    # List meetings without processing
    python email_on_complete.py --force ID   # Force process a specific meeting
"""

import argparse
import json
import os
import re
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
EXPORT_DIR = Path.home() / "Documents/03-Knowledge-Base/meetings"
LOOKBACK_MINUTES = 30
TIMEZONE = ZoneInfo("America/Chicago")


def load_state() -> dict:
    """Load the state file tracking emailed and exported meetings."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
                # Ensure exported_ids exists for backwards compatibility
                if "exported_ids" not in state:
                    state["exported_ids"] = []
                return state
        except (json.JSONDecodeError, IOError):
            pass
    return {"emailed_ids": [], "exported_ids": [], "last_run": None}


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


def sanitize_filename(name: str) -> str:
    """Convert meeting title to filesystem-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[-\s]+', '-', slug)
    return slug[:50]  # Limit length


def save_transcript_to_file(meeting: Meeting) -> bool:
    """Save meeting transcript as markdown file to knowledge base."""
    try:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)

        # Filename: YYYY-MM-DD-meeting-title.md
        date_str = meeting.start_time.strftime("%Y-%m-%d") if meeting.start_time else "unknown"
        title_slug = sanitize_filename(meeting.title or "untitled")
        filename = f"{date_str}-{title_slug}.md"

        filepath = EXPORT_DIR / filename
        content = export_meeting_to_markdown(meeting)

        filepath.write_text(content, encoding="utf-8")
        print(f"  Saved to: {filepath}")
        return True
    except Exception as e:
        print(f"  ERROR: Failed to save transcript: {e}", file=sys.stderr)
        return False


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
        Number of meetings processed (emailed + exported)
    """
    # Load configuration
    config = load_config()
    email_config = get_email_config()

    # Load state
    state = load_state()
    emailed_ids = set(state.get("emailed_ids", []))
    exported_ids = set(state.get("exported_ids", []))

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

    # Find meetings to process (either export or email)
    to_process = []

    if force_id:
        # Force mode: find specific meeting
        for meeting in meetings:
            if meeting.id and meeting.id.startswith(force_id):
                to_process.append(meeting)
                break
        if not to_process:
            print(f"ERROR: Meeting not found: {force_id}", file=sys.stderr)
            return 0
    else:
        # Normal mode: find recently completed meetings not yet processed
        processed_ids = emailed_ids & exported_ids  # Both done
        for meeting in meetings:
            if should_email_meeting(meeting, processed_ids, cutoff_time):
                to_process.append(meeting)

    if not to_process:
        print(f"No new meetings to process (checked {len(meetings)} meetings)")
        return 0

    print(f"Found {len(to_process)} meeting(s) to process:")

    sent_count = 0
    export_count = 0
    newly_emailed = []
    newly_exported = []

    for meeting in to_process:
        title = meeting.title or "Untitled"
        meeting_id = meeting.id or "unknown"

        print(f"\n  [{meeting_id[:8]}] {title}")

        if dry_run:
            end_str = meeting.end_time.strftime("%H:%M") if meeting.end_time else "unknown"
            print(f"    End time: {end_str}")
            print(f"    Has transcript: {meeting.has_transcript()}")
            print(f"    Would export to: {EXPORT_DIR}")
            if email_config["enabled"]:
                print(f"    Would email: {format_email_subject(meeting)}")
            sent_count += 1
            export_count += 1
        else:
            # Export transcript to file (always)
            if meeting_id not in exported_ids:
                if save_transcript_to_file(meeting):
                    export_count += 1
                    newly_exported.append(meeting_id)

            # Send email (if enabled and configured)
            if email_config["enabled"] and meeting_id not in emailed_ids:
                if not email_config["to"] or not email_config["from"]:
                    print("  Skipping email: EMAIL_TO or EMAIL_FROM not configured")
                else:
                    subject = format_email_subject(meeting)
                    body = export_meeting_to_markdown(meeting)

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
    if (newly_emailed or newly_exported) and not dry_run:
        if newly_emailed:
            state["emailed_ids"] = list(emailed_ids | set(newly_emailed))
        if newly_exported:
            state["exported_ids"] = list(exported_ids | set(newly_exported))
        save_state(state)
        print(f"\nState updated: {len(newly_exported)} exported, {len(newly_emailed)} emailed")

    return export_count + sent_count


def main():
    parser = argparse.ArgumentParser(
        description="Export and email Granola meeting transcripts automatically"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List meetings that would be processed without sending/exporting",
    )
    parser.add_argument(
        "--force",
        metavar="MEETING_ID",
        help="Force process a specific meeting (partial ID match)",
    )

    args = parser.parse_args()

    print(f"Granola Meeting Automation - {datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 60)

    count = process_meetings(dry_run=args.dry_run, force_id=args.force)

    if args.dry_run:
        print(f"\n[DRY RUN] Would process {count} action(s)")
    else:
        print(f"\nProcessed {count} action(s)")

    return 0 if count >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())

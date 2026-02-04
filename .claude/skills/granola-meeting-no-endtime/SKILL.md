---
name: granola-meeting-no-endtime
description: |
  Fix for Granola.ai meetings never having end_time set in cache data. Use when:
  (1) Building automation that needs to detect "completed" meetings,
  (2) Meeting.end_time is always None despite meetings being finished,
  (3) All meetings appear "still running" in GranolaMCP,
  (4) Polling scripts never find recently ended meetings.
  Covers using updated_at field as proxy for meeting completion detection.
author: Claude Code
version: 1.0.0
date: 2026-02-02
---

# Granola Meeting End Time Missing

## Problem
Granola.ai's cache data model doesn't set `end_time` on meetings when they finish recording. All meetings remain in a perpetual "open" state with `end_time: None`, making it impossible to detect recently completed meetings using standard logic.

## Context / Trigger Conditions
- Building automation to process completed Granola meetings
- `Meeting.end_time` is always `None` for all meetings
- Scripts checking for "meetings ended in last X minutes" find nothing
- GranolaMCP shows all meetings as "Still running"
- Need to detect when a meeting has finished for post-processing

## Solution

Instead of using `end_time`, use the `updated_at` field as a proxy for when the meeting was last active:

```python
def should_process_meeting(meeting, cutoff_time):
    # Don't use meeting.end_time - it's always None

    # Use updated_at as proxy for meeting completion
    last_updated = meeting.raw_data.get('updated_at')
    if not last_updated:
        return False

    # Parse ISO format timestamp
    from datetime import datetime
    update_time = datetime.fromisoformat(last_updated.replace('Z', '+00:00'))

    # Check if updated within your time window
    return update_time >= cutoff_time
```

Alternative fields to consider:
- `updated_at`: Last time the meeting data was modified
- Transcript segment timestamps: Last segment time indicates approximate meeting end
- `lastModified`: Another potential timestamp field (if present)

## Verification

Check your Granola cache to confirm the behavior:
```python
from granola_mcp.core.parser import GranolaParser
from granola_mcp.core.meeting import Meeting

parser = GranolaParser()
meetings = [Meeting(m) for m in parser.get_meetings()]

# This will show 0 meetings with end_time
with_end = sum(1 for m in meetings if m.end_time is not None)
print(f"Meetings with end_time: {with_end}/{len(meetings)}")
```

## Example

Email automation script detecting recently updated meetings:
```python
def get_recent_meetings(minutes_back=30):
    from datetime import datetime, timedelta

    cutoff = datetime.now() - timedelta(minutes=minutes_back)
    recent = []

    for meeting in meetings:
        # Check updated_at, not end_time
        updated = meeting.raw_data.get('updated_at')
        if updated:
            update_time = datetime.fromisoformat(updated.replace('Z', '+00:00'))
            if update_time >= cutoff:
                recent.append(meeting)

    return recent
```

## Notes

- This affects ALL Granola meetings - it's not a bug but their data model design
- The `updated_at` field updates when transcript segments are added or meeting data changes
- For real-time detection, you may need to track previously seen transcript lengths
- Consider using a state file to track which meetings have been processed rather than relying solely on time windows
- Granola may change this behavior in future versions, so check if `end_time` starts appearing

## References
- GranolaMCP source: https://github.com/pedramamini/GranolaMCP
- Granola.ai cache location: `~/Library/Application Support/Granola/cache-v3.json`
# Granola Email Automation

Automatically email meeting transcripts after meetings complete.

## Features

- 🔄 Polls every 5 minutes for completed meetings
- 📧 Sends full meeting export via AWS SES
- 🚫 Prevents duplicate emails with state tracking
- ⏱️ 5-minute quiet period ensures meetings are finished
- 🔁 Survives system restarts via launchd

## Quick Start

### 1. Install Dependencies
```bash
pip install boto3
```

### 2. Configure AWS
```bash
aws configure
# Enter your AWS credentials
```

### 3. Configure Email Settings
Edit `.env` file:
```env
# Required settings
EMAIL_ENABLED=true
EMAIL_TO=your@email.com
EMAIL_FROM=verified@yourdomain.com
AWS_REGION=us-east-1

# Path to Granola cache
GRANOLA_CACHE_PATH=/Users/YOUR_USERNAME/Library/Application Support/Granola/cache-v3.json
```

### 4. Verify Email Addresses (if in AWS SES Sandbox)
```bash
# Verify sender (required)
aws ses verify-email-identity --email-address verified@yourdomain.com --region us-east-1

# Verify recipient (only if in sandbox)
aws ses verify-email-identity --email-address your@email.com --region us-east-1
```

### 5. Install Automation
```bash
./scripts/install_email_automation.sh
```

## How It Works

1. **Detection**: Checks for meetings with recent `updated_at` timestamps
2. **Quiet Period**: Waits 5 minutes after last update (ensures meeting is complete)
3. **Export**: Generates full markdown export with transcript
4. **Email**: Sends via AWS SES
5. **State Tracking**: Saves emailed meeting IDs to `~/.granola_email_state.json`

## Manual Controls

### Test (Dry Run)
```bash
python scripts/email_on_complete.py --dry-run
```

### Force Email Specific Meeting
```bash
python scripts/email_on_complete.py --force MEETING_ID
```

### View Logs
```bash
tail -f ~/Library/Logs/granola-email.log
```

### Stop Automation
```bash
launchctl unload ~/Library/LaunchAgents/com.granola.email-automation.plist
```

### Start Automation
```bash
launchctl load ~/Library/LaunchAgents/com.granola.email-automation.plist
```

### Check Status
```bash
launchctl list | grep granola
```

## Configuration

### Timing Settings (in script)
```python
LOOKBACK_MINUTES = 30        # Check meetings from last 30 min
QUIET_PERIOD_MINUTES = 5     # Wait 5 min of inactivity
```

### Email Content
- **Subject**: "Granola Meeting: {title} - {date}"
- **Body**: Full markdown export with:
  - Meeting metadata
  - Participants
  - Human notes
  - AI summary
  - Full transcript

## Troubleshooting

### No Emails Being Sent
1. Check logs: `tail -f ~/Library/Logs/granola-email.log`
2. Verify meeting has been quiet for 5+ minutes
3. Check state file: `cat ~/.granola_email_state.json`
4. Test manually: `python scripts/email_on_complete.py --dry-run`

### AWS SES Errors
- **"Email address is not verified"**: Verify both sender and recipient in SES
- **"Domain contains control or whitespace"**: Check .env file for inline comments
- **"Access denied"**: Check AWS credentials and IAM permissions

### Meeting Detection Issues
- Granola doesn't set `end_time` on meetings
- Script uses `updated_at` field instead
- Meetings need 5 min quiet period to be considered "complete"

## Important Notes

⚠️ **Granola Data Model**: Meetings never get `end_time` set - they remain perpetually "open". The script uses `updated_at` timestamps to detect recently active meetings.

⚠️ **AWS SES Sandbox**: By default, AWS SES is in sandbox mode. You must verify BOTH sender and recipient email addresses. Request production access to send to any email.

⚠️ **Persistence**: The launchd job persists across system restarts. The automation will resume automatically after reboot.

## Files

- `scripts/email_on_complete.py` - Main automation script
- `scripts/com.granola.email-automation.plist` - launchd configuration
- `scripts/install_email_automation.sh` - Installation script
- `~/.granola_email_state.json` - Tracks emailed meetings
- `~/Library/Logs/granola-email.log` - Automation logs

## Uninstall

To completely remove the automation:

```bash
# Stop and unload
launchctl unload ~/Library/LaunchAgents/com.granola.email-automation.plist

# Remove files
rm ~/Library/LaunchAgents/com.granola.email-automation.plist
rm ~/.granola_email_state.json
rm ~/Library/Logs/granola-email.log

# Uninstall boto3 if not needed
pip uninstall boto3
```
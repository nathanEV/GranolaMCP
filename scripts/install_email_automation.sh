#!/bin/bash
#
# Install Granola Email Automation
#
# This script:
# 1. Checks prerequisites (Python, boto3)
# 2. Configures the launchd plist with correct paths
# 3. Installs and loads the launchd job
#
# Usage: ./install_email_automation.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_NAME="com.granola.email-automation.plist"
PLIST_SRC="$SCRIPT_DIR/$PLIST_NAME"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"
LOG_DIR="$HOME/Library/Logs"

echo "Granola Email Automation Installer"
echo "==================================="
echo ""

# Check Python
echo "Checking prerequisites..."
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found"
    exit 1
fi
PYTHON_PATH=$(which python3)
echo "  Python: $PYTHON_PATH"

# Check boto3
if ! python3 -c "import boto3" 2>/dev/null; then
    echo ""
    echo "WARNING: boto3 not installed"
    echo "  Run: pip install boto3"
    echo ""
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    echo "  boto3: installed"
fi

# Check .env configuration
ENV_FILE="$REPO_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo ""
    echo "WARNING: .env file not found"
    echo "  Copy .env.example to .env and configure EMAIL_* settings"
fi

# Create log directory
mkdir -p "$LOG_DIR"

# Create configured plist
echo ""
echo "Configuring launchd plist..."

CONFIGURED_PLIST=$(cat "$PLIST_SRC" | \
    sed "s|/Users/YOUR_USERNAME/path/to/GranolaMCP/scripts/email_on_complete.py|$SCRIPT_DIR/email_on_complete.py|g" | \
    sed "s|/Users/YOUR_USERNAME/path/to/GranolaMCP|$REPO_DIR|g" | \
    sed "s|/Users/YOUR_USERNAME/Library/Logs|$LOG_DIR|g" | \
    sed "s|/usr/bin/python3|$PYTHON_PATH|g")

# Unload existing job if present
if launchctl list | grep -q "com.granola.email-automation"; then
    echo "Unloading existing job..."
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
fi

# Write configured plist
echo "$CONFIGURED_PLIST" > "$PLIST_DEST"
echo "  Wrote: $PLIST_DEST"

# Load the job
echo ""
echo "Loading launchd job..."
launchctl load "$PLIST_DEST"

# Verify
if launchctl list | grep -q "com.granola.email-automation"; then
    echo "  SUCCESS: Job loaded"
else
    echo "  WARNING: Job may not have loaded correctly"
fi

echo ""
echo "==================================="
echo "Installation complete!"
echo ""
echo "Next steps:"
echo "  1. Configure .env with EMAIL_TO, EMAIL_FROM, AWS_REGION"
echo "  2. Set EMAIL_ENABLED=true"
echo "  3. Ensure AWS credentials are configured (~/.aws/credentials)"
echo "  4. Test with: python3 $SCRIPT_DIR/email_on_complete.py --dry-run"
echo ""
echo "Useful commands:"
echo "  View logs:    tail -f $LOG_DIR/granola-email.log"
echo "  Stop job:     launchctl unload $PLIST_DEST"
echo "  Start job:    launchctl load $PLIST_DEST"
echo "  Run now:      python3 $SCRIPT_DIR/email_on_complete.py"
echo ""

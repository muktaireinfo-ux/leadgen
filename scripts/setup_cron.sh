#!/bin/bash
# Sets up a daily 9am cron job to find leads automatically.
# Edit INDUSTRY and COUNTRY below before running.

PYTHON=$(which python3)
SCRIPT="$HOME/leadgen/scripts/find_leads.py"
INDUSTRY="restaurant"
COUNTRY="us"
LOG="$HOME/leadgen/cron.log"

CRON_CMD="0 9 * * * $PYTHON $SCRIPT --industry \"$INDUSTRY\" --country $COUNTRY --fast >> $LOG 2>&1"

# Add to crontab if not already present
(crontab -l 2>/dev/null | grep -qF "$SCRIPT") \
    && echo "Cron job already exists." \
    || (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -

echo "Cron job set: daily at 9am for '$INDUSTRY' in '$COUNTRY'"
echo "Logs will be written to: $LOG"
echo ""
echo "To edit: run 'crontab -e'"
echo "To remove: run 'crontab -l | grep -v find_leads | crontab -'"

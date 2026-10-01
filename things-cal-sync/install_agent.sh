#!/bin/bash
# Install (or repair) the hourly sync agent.
#
# Safe to run as often as you like: it replaces whatever is there now.
# Uses bootstrap/enable rather than the legacy `launchctl load`, because
# `load` leaves no record that the job should be running -- which is why it
# kept disappearing after a restart.

set -uo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LABEL="com.user.things-cal-sync"
PLIST_SRC="$SCRIPT_DIR/$LABEL.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

PYTHON="$(command -v python3)"
if [ -z "$PYTHON" ]; then
    echo "FOUT: python3 niet gevonden."
    exit 1
fi

echo "Python      : $PYTHON"
echo "Scriptmap   : $SCRIPT_DIR"
echo

if [ ! -f "$PLIST_SRC" ]; then
    echo "FOUT: $PLIST_SRC ontbreekt."
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$HOME/.local/share/things-sync/logs"

echo "1. Oude versie opruimen..."
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null
launchctl unload "$PLIST_DEST" 2>/dev/null
echo "   klaar"

echo "2. Instellingen schrijven..."
sed -e "s|REPLACE_WITH_PYTHON|$PYTHON|g" \
    -e "s|REPLACE_WITH_FULL_PATH|$SCRIPT_DIR|g" \
    -e "s|REPLACE_WITH_HOME|$HOME|g" \
    "$PLIST_SRC" > "$PLIST_DEST"
plutil -lint "$PLIST_DEST" >/dev/null || { echo "   FOUT: ongeldig plist"; exit 1; }
echo "   $PLIST_DEST"

echo "3. Blokkade opheffen..."
# macOS remembers an earlier `unload` as "this must not start" and that flag
# survives reboots. This is the most likely reason it kept falling off.
launchctl enable "$DOMAIN/$LABEL" 2>/dev/null
echo "   klaar"

echo "4. Activeren..."
if launchctl bootstrap "$DOMAIN" "$PLIST_DEST" 2>/dev/null; then
    echo "   via bootstrap"
elif launchctl load "$PLIST_DEST" 2>/dev/null; then
    echo "   via load (oudere macOS)"
else
    echo "   FOUT: activeren mislukt"
    exit 1
fi

echo
echo "5. Controle:"
sleep 2
if launchctl list | grep -q "$LABEL"; then
    launchctl list | grep "$LABEL" | awk '{print "   PID:", $1, " laatste afloop:", $2}'
else
    echo "   NIET ACTIEF -- zie hieronder"
fi

echo
if launchctl print-disabled "$DOMAIN" 2>/dev/null | grep -q "\"$LABEL\" => disabled"; then
    echo "LET OP: macOS heeft de taak nog steeds geblokkeerd."
    echo "Zet hem aan in Systeeminstellingen > Algemeen > Inloggen en extensies."
else
    echo "Staat niet geblokkeerd. Hij draait nu elk uur op het hele uur,"
    echo "en haalt een gemist uur in zodra je je Mac openklapt."
fi

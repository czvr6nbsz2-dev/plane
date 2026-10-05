#!/bin/bash
# Build "Things Cal Sync.app" and register it as a login item.
#
# Replaces the LaunchAgent, which kept vanishing: after a restart launchd
# reported "Could not find service" even though the plist was in place and
# the job was not in the disabled list. A login item is visible in System
# Settings, stays where the user puts it, and needs no launchd registration.
#
# The app has no window and no Dock icon (LSUIElement). It simply runs the
# sync once an hour for as long as you are logged in.

set -uo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
APP_NAME="Things Cal Sync"
APP_DIR="$HOME/Applications/$APP_NAME.app"
LOG_DIR="$HOME/.local/share/things-sync/logs"
OLD_PLIST="$HOME/Library/LaunchAgents/com.user.things-cal-sync.plist"

PYTHON="$(command -v python3)"
if [ -z "$PYTHON" ]; then
    echo "FOUT: python3 niet gevonden."
    exit 1
fi
if [ ! -f "$SCRIPT_DIR/sync.py" ]; then
    echo "FOUT: sync.py niet gevonden in $SCRIPT_DIR"
    exit 1
fi

echo "Python     : $PYTHON"
echo "Sync-script: $SCRIPT_DIR/sync.py"
echo "Programma  : $APP_DIR"
echo

# --- 1. oude LaunchAgent uitzetten -----------------------------------------
echo "1. Oude uurtaak uitschakelen..."
launchctl bootout "gui/$(id -u)/com.user.things-cal-sync" 2>/dev/null
launchctl unload "$OLD_PLIST" 2>/dev/null
if [ -f "$OLD_PLIST" ]; then
    # Bewaren in plaats van weggooien, voor het geval je terug wil.
    mv "$OLD_PLIST" "$OLD_PLIST.uit" 2>/dev/null \
        && echo "   plist bewaard als $(basename "$OLD_PLIST").uit"
else
    echo "   stond al uit"
fi

# --- 2. programma bouwen ----------------------------------------------------
echo "2. Programma bouwen..."
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$LOG_DIR"

cat > "$APP_DIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>$APP_NAME</string>
    <key>CFBundleDisplayName</key><string>$APP_NAME</string>
    <key>CFBundleIdentifier</key><string>com.user.things-cal-sync.app</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleExecutable</key><string>ThingsCalSync</string>
    <key>LSUIElement</key><true/>
    <key>LSMinimumSystemVersion</key><string>10.13</string>
</dict>
</plist>
PLIST

cat > "$APP_DIR/Contents/MacOS/ThingsCalSync" <<RUNNER
#!/bin/bash
# Runs the sync once an hour for as long as this login session lasts.

PYTHON="$PYTHON"
SYNC="$SCRIPT_DIR/sync.py"
LOG_DIR="$LOG_DIR"
INTERVAL=3600

mkdir -p "\$LOG_DIR"

# A second copy would be harmless -- sync.py holds its own lock file and a
# duplicate run exits straight away -- so no extra locking here.

LAST=0
while true; do
    NOW=\$(date +%s)
    # Poll every minute rather than sleeping a whole hour: after the Mac
    # wakes this notices a missed slot straight away instead of finishing
    # out the old timer.
    if [ \$((NOW - LAST)) -ge \$INTERVAL ]; then
        # sync.py writes its own log file; keep stdout out of it to avoid
        # every line landing twice.
        "\$PYTHON" "\$SYNC" >/dev/null 2>>"\$LOG_DIR/sync.error.log"
        LAST=\$(date +%s)
    fi
    sleep 60
done
RUNNER

chmod +x "$APP_DIR/Contents/MacOS/ThingsCalSync"
echo "   klaar"

# --- 3. als inlogitem registreren -------------------------------------------
echo "3. Als inlogitem instellen..."
osascript <<OSA 2>/dev/null
tell application "System Events"
    try
        delete (every login item whose name is "$APP_NAME")
    end try
    make login item at end with properties {path:"$APP_DIR", hidden:true}
end tell
OSA
if [ $? -eq 0 ]; then
    echo "   toegevoegd"
else
    echo "   NIET GELUKT -- voeg hem handmatig toe (zie onderaan)"
fi

# --- 4. nu starten -----------------------------------------------------------
echo "4. Nu starten..."
open "$APP_DIR"
sleep 3
if pgrep -f "ThingsCalSync" >/dev/null; then
    echo "   draait (PID $(pgrep -f ThingsCalSync | head -1))"
else
    echo "   lijkt niet te draaien -- controleer met: pgrep -fl ThingsCalSync"
fi

echo
echo "Klaar. De eerste sync loopt nu; daarna elk uur."
echo
echo "Controleren of hij draait:   pgrep -fl ThingsCalSync"
echo "Stoppen:                     pkill -f ThingsCalSync"
echo "Zichtbaar in:                Systeeminstellingen > Algemeen >"
echo "                             Inloggen en extensies > Openen bij inloggen"
echo
echo "Staat hij daar niet? Klik op + en kies:"
echo "  $APP_DIR"

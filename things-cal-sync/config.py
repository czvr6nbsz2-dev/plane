"""Constants for the Things 3 <-> Calendar.app sync."""
from pathlib import Path

# Calendar configuration
CALENDAR_NAME = "Things Tasks"
START_HOUR = 8
SLOT_MINUTES = 30
LOOKBACK_DAYS = 7
LOOKAHEAD_DAYS = 730

# AppleScript marker stored inside event description
THINGS_ID_MARKER = "x-things-id:"

# Filesystem layout (resolved at runtime against $HOME)
HOME = Path.home()
DATA_DIR = HOME / ".local" / "share" / "things-sync"
DB_PATH = DATA_DIR / "things_sync.db"
LOG_DIR = DATA_DIR / "logs"
LOG_PATH = LOG_DIR / "sync.log"
LOCK_PATH = DATA_DIR / "sync.lock"

# Lock file is considered stale after this many seconds
LOCK_STALE_SECONDS = 10 * 60

# Log retention
LOG_RETENTION_DAYS = 7

# AppleScript subprocess timeout
OSASCRIPT_TIMEOUT = 30

"""Constants for the Things 3 <-> Calendar.app sync."""
from pathlib import Path

# Calendar configuration
CALENDAR_NAME = "Things Tasks"
START_HOUR = 8
END_HOUR = 18
SLOT_MINUTES = 30
LOOKBACK_DAYS = 7
LOOKAHEAD_DAYS = 730

# Calendars whose events mean "Arthur is busy, do not put a task here".
# Matched by name, so all three accounts exposing a calendar called "Agenda"
# count. Everything left out (birthdays, Dutch holidays, F1, other people's
# calendars) is ignored when placing tasks.
BUSY_CALENDARS = [
    "Agenda",
    "Reistijd",
    "Planning",
    "Thuis",
    "Thuis-agenda",
    "Sport",
]

# All-day events (holidays, birthdays) would otherwise block a whole day and
# push every task into the evening.
IGNORE_ALLDAY_EVENTS = True

# Finding recurring series means scanning every event in the busy calendars,
# which is far too slow to repeat hourly. They change rarely, so the result is
# cached in the database and refreshed this often.
RECURRING_CACHE_HOURS = 24

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

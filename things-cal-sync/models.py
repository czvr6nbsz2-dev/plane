"""Plain dataclasses passed between modules."""
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional


@dataclass
class ThingsTask:
    things_id: str
    title: str
    when_date: Optional[date]
    deadline_date: Optional[date]
    modification_date: Optional[datetime]
    creation_date: Optional[datetime]
    in_today: bool

    @property
    def effective_date(self) -> Optional[date]:
        if self.when_date is not None:
            return self.when_date
        if self.in_today:
            return date.today()
        if self.deadline_date is not None:
            return self.deadline_date
        return None


@dataclass
class CalEvent:
    cal_uid: str
    title: str
    start_dt: datetime
    end_dt: datetime
    description: str
    things_id: Optional[str] = None


@dataclass
class SyncRecord:
    things_id: str
    cal_uid: Optional[str]
    things_title: str
    synced_date: str
    synced_start_time: str
    synced_end_time: str
    user_pinned: bool
    pinned_start_time: Optional[str]
    pinned_end_time: Optional[str]
    last_synced_at: str
    things_mod_at: Optional[str]
    creation_date: Optional[str]


@dataclass
class SyncSummary:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    restacked: int = 0
    things_updated: int = 0
    pinned: int = 0
    skipped: int = 0
    errors: int = 0
    affected_dates: set = field(default_factory=set)

    def as_text(self) -> str:
        return (
            f"created={self.created} updated={self.updated} deleted={self.deleted} "
            f"restacked={self.restacked} things_updated={self.things_updated} "
            f"pinned={self.pinned} skipped={self.skipped} errors={self.errors}"
        )

"""Tests for size tags and Today-order priority. Run: python3 -m unittest test_plan"""
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import db
import things_bridge
from models import ThingsTask
from stacking import assign_slots

DAY = date(2026, 11, 20)


def at(h, m=0):
    return datetime(DAY.year, DAY.month, DAY.day, h, m)


def task(tid, minutes=30):
    return ThingsTask(tid, tid, DAY, None, None, None, False, duration_minutes=minutes)


class StackingTests(unittest.TestCase):
    def test_default_tasks_stack_in_30_minute_slots(self):
        out = assign_slots(DAY, [task("a"), task("b")], [], {})
        self.assertEqual(out["a"], (at(8), at(8, 30)))
        self.assertEqual(out["b"], (at(8, 30), at(9)))

    def test_durations_are_respected_and_back_to_back(self):
        out = assign_slots(DAY, [task("a", 60), task("b", 15), task("c", 120), task("d")], [], {})
        self.assertEqual(out["a"], (at(8), at(9)))
        self.assertEqual(out["b"], (at(9), at(9, 15)))
        self.assertEqual(out["c"], (at(9, 15), at(11, 15)))
        self.assertEqual(out["d"], (at(11, 15), at(11, 45)))

    def test_long_task_skips_a_meeting_it_would_overlap(self):
        meeting = [(at(9), at(10))]
        out = assign_slots(DAY, [task("a", 120)], meeting, {})
        self.assertEqual(out["a"], (at(10), at(12)))

    def test_order_given_is_priority(self):
        out = assign_slots(DAY, [task("urgent"), task("later")], [], {})
        self.assertLess(out["urgent"][0], out["later"][0])

    def test_overflow_stays_on_the_same_day(self):
        tasks = [task(f"t{i}", 120) for i in range(7)]
        out = assign_slots(DAY, tasks, [], {})
        for start, end in out.values():
            self.assertEqual(start.date(), DAY)
            self.assertEqual(end.date() if end.time() != datetime.min.time() else DAY, DAY)


class SizeTagTests(unittest.TestCase):
    def test_no_tag_is_default(self):
        self.assertEqual(things_bridge.duration_from_tags([]), 30)
        self.assertEqual(things_bridge.duration_from_tags(["thuis"]), 30)

    def test_size_tags_case_insensitive(self):
        self.assertEqual(things_bridge.duration_from_tags(["@15m"]), 15)
        self.assertEqual(things_bridge.duration_from_tags(["@1U"]), 60)
        self.assertEqual(things_bridge.duration_from_tags(["x", "@2u"]), 120)

    def test_largest_wins(self):
        self.assertEqual(things_bridge.duration_from_tags(["@15m", "@2u"]), 120)


class ParserTests(unittest.TestCase):
    def test_rank_follows_today_order_and_sizes_parse(self):
        rows = [
            "A\tEerst\t2026-11-20\t\t\t\t1\t@1u",
            "B\tWacht\t2026-11-20\t\t\t\t1\t@w",
            "C\tTweede\t2026-11-20\t\t\t\t1\t",
            "D\tLater\t2026-11-25\t\t\t\t0\t@15m",
        ]
        things_bridge._run_osascript = lambda s: "\n".join(rows)
        got = {t.things_id: t for t in things_bridge.list_active_tasks()}
        self.assertNotIn("B", got)
        self.assertEqual((got["A"].today_rank, got["A"].duration_minutes), (0, 60))
        self.assertEqual((got["C"].today_rank, got["C"].duration_minutes), (1, 30))
        self.assertEqual((got["D"].today_rank, got["D"].duration_minutes), (None, 15))


class MigrationTests(unittest.TestCase):
    def test_old_database_gets_new_columns_and_keeps_rows(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "old.db"
            con = sqlite3.connect(path)
            con.execute(
                """CREATE TABLE task_map (
                    things_id TEXT PRIMARY KEY, cal_uid TEXT UNIQUE, things_title TEXT NOT NULL,
                    synced_date TEXT NOT NULL, synced_start_time TEXT NOT NULL,
                    synced_end_time TEXT NOT NULL, user_pinned INTEGER NOT NULL DEFAULT 0,
                    pinned_start_time TEXT, pinned_end_time TEXT, last_synced_at TEXT NOT NULL,
                    things_mod_at TEXT, creation_date TEXT)"""
            )
            con.execute(
                "INSERT INTO task_map VALUES ('x','u','t','2026-11-20','s','e',0,NULL,NULL,'n',NULL,NULL)"
            )
            con.commit()
            con.close()
            db.init_db(path)
            with db.connect(path) as conn:
                rec = db.get_record(conn, "x")
                self.assertEqual((rec.duration_minutes, rec.priority_rank), (30, None))
                db.update_plan(conn, "x", 60, 3)
                rec = db.get_record(conn, "x")
                self.assertEqual((rec.duration_minutes, rec.priority_rank), (60, 3))
                # rewriting the record (as Phase 3/4 do) must not reset the plan
                db.upsert_record(conn, rec.__class__(**{**rec.__dict__, "duration_minutes": 30, "priority_rank": None}))
                rec = db.get_record(conn, "x")
                self.assertEqual((rec.duration_minutes, rec.priority_rank), (60, 3))


if __name__ == "__main__":
    unittest.main()

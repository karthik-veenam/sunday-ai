"""Restart-safe timers/alarms. Delivery is through HA, never local playback."""
import asyncio
from db_schema import ensure_v1
import json
import sqlite3
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


class Jobs:
    def __init__(self, path, deliver):
        self.db = sqlite3.connect(path)
        self.deliver = deliver
        try:
            ensure_v1(self.db, ("CREATE TABLE jobs (id TEXT PRIMARY KEY, due REAL, kind TEXT, payload TEXT, status TEXT DEFAULT 'pending', error TEXT)",), 'Jobs')
        except BaseException:
            self.db.close()
            raise
        # An interrupted delivery must be reviewed, never blindly replayed.
        self.db.execute("UPDATE jobs SET status='unknown', error='Runtime stopped during delivery' WHERE status='delivering'")
        self.db.commit()

    def add(self, due, kind, message):
        job_id = uuid.uuid4().hex
        self.db.execute("INSERT INTO jobs(id,due,kind,payload) VALUES (?,?,?,?)",
                        (job_id, due, kind, json.dumps({"message": message})))
        self.db.commit()
        return job_id

    def timer(self, seconds):
        seconds = int(seconds)
        if not 1 <= seconds <= 604800:
            return "Timer must be between one second and seven days."
        self.add(time.time() + seconds, "timer", "Your timer is done.")
        return f"Timer set for {seconds} seconds."

    def alarm(self, clock):
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        for pattern in ("%I:%M %p", "%H:%M"):
            try:
                value = datetime.strptime(clock.strip().upper(), pattern)
                break
            except ValueError:
                continue
        else:
            return "Alarm time must be HH:MM or HH:MM AM/PM."
        due = now.replace(hour=value.hour, minute=value.minute, second=0, microsecond=0)
        if due <= now:
            due += timedelta(days=1)
        self.add(due.timestamp(), "alarm", "It's time to wake up.")
        return f"Alarm scheduled for {due.strftime('%I:%M %p')} IST in Sunday 2.0."

    def list(self):
        return [dict(zip(("id", "due", "kind", "payload", "status", "error"), row))
                for row in self.db.execute("SELECT * FROM jobs ORDER BY due DESC LIMIT 100")]

    async def loop(self):
        while True:
            for job_id, kind, payload in self.db.execute(
                    "SELECT id,kind,payload FROM jobs WHERE status='pending' AND due<=?", (time.time(),)).fetchall():
                self.db.execute("UPDATE jobs SET status='delivering' WHERE id=? AND status='pending'", (job_id,))
                self.db.commit()
                try:
                    result = await self.deliver(json.loads(payload)["message"], priority=kind)
                    status = "delivered" if result == "ok" else "deferred"
                    error = None if result == "ok" else result
                except Exception as exc:
                    status, error = "unknown", type(exc).__name__
                self.db.execute("UPDATE jobs SET status=?,error=? WHERE id=?", (status, error, job_id))
                self.db.commit()
            await asyncio.sleep(1)

    def close(self):
        self.db.close()

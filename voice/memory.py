import json
import sqlite3
import threading
from datetime import datetime

DB_PATH = "/home/djpi/sunday/sunday.db"


class Memory:
    def __init__(self, db_path: str = DB_PATH):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init()

    def _init(self) -> None:
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS action_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    NOT NULL,
                    tool_name   TEXT    NOT NULL,
                    inputs      TEXT,
                    result      TEXT,
                    user_text   TEXT
                );
                CREATE TABLE IF NOT EXISTS conversation_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    NOT NULL,
                    role        TEXT    NOT NULL,
                    content     TEXT    NOT NULL
                );
                CREATE TABLE IF NOT EXISTS patterns (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    NOT NULL,
                    insight     TEXT    NOT NULL,
                    category    TEXT
                );
                CREATE TABLE IF NOT EXISTS presence_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    NOT NULL,
                    state       TEXT    NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    NOT NULL,
                    type        TEXT    NOT NULL,
                    payload     TEXT,
                    processed   INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS my_works (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT NOT NULL,
                    type        TEXT NOT NULL DEFAULT 'feedback',
                    app         TEXT NOT NULL,
                    user        TEXT NOT NULL,
                    chat_id     TEXT NOT NULL,
                    title       TEXT NOT NULL,
                    notes       TEXT,
                    state       TEXT NOT NULL DEFAULT 'new',
                    notified    INTEGER DEFAULT 0
                );
            """)
            self._conn.commit()

    def log_action(self, tool_name: str, inputs: dict, result: str, user_text: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO action_log (timestamp, tool_name, inputs, result, user_text) VALUES (?,?,?,?,?)",
                (datetime.now().isoformat(), tool_name, json.dumps(inputs), result, user_text),
            )
            self._conn.commit()

    def log_message(self, role: str, content: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversation_log (timestamp, role, content) VALUES (?,?,?)",
                (datetime.now().isoformat(), role, content),
            )
            self._conn.commit()

    def recent_messages(self, limit: int = 10) -> list[dict]:
        """Return recent Telegram conversation messages, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, timestamp FROM conversation_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def recent_actions(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM action_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def actions_since(self, since_iso: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM action_log WHERE timestamp >= ? ORDER BY id ASC", (since_iso,)
            ).fetchall()
        return [dict(r) for r in rows]

    def last_interaction_time(self) -> str | None:
        """ISO timestamp of most recent non-reflection action, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT timestamp FROM action_log WHERE tool_name != 'reflection' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row["timestamp"] if row else None

    def last_actions_per_device(self) -> list[dict]:
        """Most recent action for each unique device (by inputs JSON)."""
        with self._lock:
            rows = self._conn.execute("""
                SELECT tool_name, inputs, result, timestamp
                FROM action_log
                WHERE tool_name IN ('control_device', 'send_google_assistant_command')
                  AND id IN (
                    SELECT MAX(id) FROM action_log
                    WHERE tool_name IN ('control_device', 'send_google_assistant_command')
                    GROUP BY inputs
                  )
                ORDER BY timestamp DESC
                LIMIT 30
            """).fetchall()
        return [dict(r) for r in rows]


    def emit_event(self, type: str, payload: dict) -> None:
        """Emit a discrete event for the event-driven reflection loop."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (timestamp, type, payload) VALUES (?,?,?)",
                (datetime.now().isoformat(), type, json.dumps(payload)),
            )
            self._conn.commit()

    def pending_events(self, since_id: int = 0) -> list[dict]:
        """Return unprocessed events with id > since_id, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE processed=0 AND id > ? ORDER BY id ASC", (since_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_processed(self, up_to_id: int) -> None:
        """Mark all events up to and including up_to_id as processed."""
        with self._lock:
            self._conn.execute(
                "UPDATE events SET processed=1 WHERE id <= ?", (up_to_id,)
            )
            self._conn.commit()

    def log_presence(self, state: str) -> None:
        """Log a presence state change: 'home' or 'away'."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO presence_log (timestamp, state) VALUES (?,?)",
                (datetime.now().isoformat(), state),
            )
            self._conn.commit()

    def recent_presence(self, limit: int = 20) -> list[dict]:
        """Return recent presence state changes, newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM presence_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def recent_events(self, limit: int = 50) -> list[dict]:
        """Return recent events (processed or not), newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def last_seen_home(self) -> str | None:
        """ISO timestamp of most recent 'home' entry, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT timestamp FROM presence_log WHERE state='home' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row["timestamp"] if row else None


# Global singleton
    def add_work(self, type: str, app: str, user: str, chat_id: str, title: str, notes: str | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO my_works (timestamp, type, app, user, chat_id, title, notes, state, notified) VALUES (?,?,?,?,?,?,?,?,?)",
                (datetime.now().isoformat(), type, app, user, chat_id, title, notes, "new", 0),
            )
            self._conn.commit()
            return cur.lastrowid

    def update_work_state(self, work_id: int, state: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE my_works SET state=? WHERE id=?", (state, work_id))
            self._conn.commit()

    def delete_work(self, work_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM my_works WHERE id=?", (work_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def get_work_by_id(self, work_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM my_works WHERE id=?", (work_id,)).fetchone()
        return dict(row) if row else None

    def get_pending_notifications(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM my_works WHERE notified=0 ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def mark_notified(self, work_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE my_works SET notified=1 WHERE id=?", (work_id,))
            self._conn.commit()

    def get_works(self, state: str | None = "new", type: str | None = None) -> list[dict]:
        clauses, params = [], []
        if state and state != "all":
            clauses.append("state=?"); params.append(state)
        if type and type != "all":
            clauses.append("type=?"); params.append(type)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM my_works {where} ORDER BY timestamp DESC", params
            ).fetchall()
        return [dict(r) for r in rows]


_memory: Memory | None = None


def init(db_path: str = DB_PATH) -> None:
    global _memory
    _memory = Memory(db_path)


def get() -> Memory:
    if _memory is None:
        raise RuntimeError("Memory not initialised — call memory.init() first")
    return _memory

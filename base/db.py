"""SQLite log of every message in and out. It's the record of the rescue, so
nothing is ever updated or deleted. Bad lines are kept too (type 'BAD') for debugging."""
import json
import sqlite3


class EventDB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS events (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            ts   REAL NOT NULL,          -- unix time the base received/sent it
            node INTEGER,                -- NULL for unparseable lines
            type TEXT NOT NULL,          -- HB / ST / EV / CALL / MSG / ASK / BAD
            dir  TEXT NOT NULL,          -- 'in' (from node) or 'out' (to node)
            raw  TEXT NOT NULL)""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS events_node_ts ON events(node, ts)")
        self.conn.commit()

    def add(self, ts, node, type_, direction, raw):
        if not isinstance(raw, str):
            raw = json.dumps(raw, separators=(",", ":"))
        cur = self.conn.execute(
            "INSERT INTO events (ts, node, type, dir, raw) VALUES (?, ?, ?, ?, ?)",
            (ts, node, type_, direction, raw))
        self.conn.commit()
        return cur.lastrowid

    def recent(self, node=None, limit=200, since=None, include_bad=False):
        """Newest first."""
        sql, args = "SELECT id, ts, node, type, dir, raw FROM events WHERE 1=1", []
        if node is not None:
            sql += " AND node = ?"
            args.append(node)
        if since is not None:
            sql += " AND ts >= ?"
            args.append(since)
        if not include_bad:
            sql += " AND type != 'BAD'"
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return self.conn.execute(sql, args).fetchall()

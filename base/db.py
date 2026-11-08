"""SQLite storage. Nothing is ever deleted: it's the record of the rescue.

  events     every message in and out (bad lines too, type 'BAD'), plus system notes
  kv         incident-wide settings: incident name, base location
  node_meta  per node: assigned team, notes, site tag
  alerts     UNRESPONSIVE / CONFIRMED / DANGER alerts and who acknowledged them, when
"""
import json
import sqlite3


class EventDB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                ts   REAL NOT NULL,          -- unix time the base received/sent it
                node INTEGER,                -- NULL for unparseable lines
                type TEXT NOT NULL,          -- HB / ST / EV / SN / LOC / CALL / MSG / ... / BAD / NOTE
                dir  TEXT NOT NULL,          -- 'in' (from node), 'out' (to node), 'sys' (base)
                raw  TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_node_ts ON events(node, ts);
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS node_meta (
                node  INTEGER PRIMARY KEY,
                team  TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                tag   TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS alerts (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       REAL NOT NULL,
                node     INTEGER NOT NULL,
                kind     TEXT NOT NULL,      -- UNRESPONSIVE / CONFIRMED / DANGER
                text     TEXT NOT NULL,
                acked_by TEXT,
                acked_at REAL);
        """)
        self.conn.commit()

    # ---- events ----
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

    # ---- incident settings ----
    def get_kv(self):
        return {k: json.loads(v) for k, v in self.conn.execute("SELECT key, value FROM kv")}

    def set_kv(self, key, value):
        self.conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, json.dumps(value)))
        self.conn.commit()

    # ---- per-node notes ----
    def get_node_meta(self):
        rows = self.conn.execute("SELECT node, team, notes, tag FROM node_meta")
        return {n: {"team": team, "notes": notes, "tag": tag} for n, team, notes, tag in rows}

    def set_node_meta(self, node, team, notes, tag):
        self.conn.execute("INSERT OR REPLACE INTO node_meta (node, team, notes, tag) VALUES (?, ?, ?, ?)",
                          (node, team, notes, tag))
        self.conn.commit()

    # ---- alerts ----
    def add_alert(self, ts, node, kind, text):
        cur = self.conn.execute("INSERT INTO alerts (ts, node, kind, text) VALUES (?, ?, ?, ?)",
                                (ts, node, kind, text))
        self.conn.commit()
        return cur.lastrowid

    def ack_alert(self, alert_id, by, ts):
        cur = self.conn.execute("UPDATE alerts SET acked_by = ?, acked_at = ? WHERE id = ? AND acked_at IS NULL",
                                (by, ts, alert_id))
        self.conn.commit()
        return cur.rowcount > 0

    def alerts(self, limit=100):
        """All unacknowledged alerts + the most recent acknowledged ones, newest first."""
        rows = self.conn.execute(
            "SELECT id, ts, node, kind, text, acked_by, acked_at FROM alerts "
            "WHERE acked_at IS NULL OR id IN (SELECT id FROM alerts ORDER BY id DESC LIMIT ?) "
            "ORDER BY id DESC", (limit,)).fetchall()
        return [{"id": i, "ts": ts, "n": n, "kind": k, "text": t, "acked_by": by, "acked_at": at}
                for i, ts, n, k, t, by, at in rows]

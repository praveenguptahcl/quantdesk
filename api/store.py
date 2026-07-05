"""SQLite store for QuantDesk. Stdlib only — no ORM.

Documents (ideas, notes, alerts, spine symbols) are stored as JSON blobs so the
GUI's object shapes are the single source of truth. Append-only tables (feed,
audit) are structured. Seed data comes from api/seed.json, which is extracted
from the GUI's embedded mock API object — the two can never drift apart.
"""
import json
import os
import sqlite3
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
SEED_PATH = os.path.join(_HERE, "seed.json")

_lock = threading.Lock()


class Store:
    def __init__(self, db_path=None):
        # Default DB lives in ~/.quantdesk/ — outside any synced/mounted project
        # folder (sqlite locking is unreliable on network mounts, and you don't
        # want the DB in cloud sync anyway). Override with QD_DB.
        default = os.path.join(os.path.expanduser("~"), ".quantdesk", "quantdesk.db")
        self.db_path = db_path or os.environ.get("QD_DB", default)
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        try:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._init_schema()
        except sqlite3.OperationalError:
            # unwritable location (network mount etc.) — fall back to memory
            print(f"warning: {self.db_path} not writable, using in-memory DB (state won't persist)")
            self.db_path = ":memory:"
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._init_schema()
        if self._empty():
            self.reseed()

    # ---------- schema / seed ----------
    def _init_schema(self):
        with _lock, self._conn as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS docs(
                    kind TEXT, id TEXT, data TEXT,
                    PRIMARY KEY(kind, id));
                CREATE TABLE IF NOT EXISTS feed(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT, level TEXT, msg TEXT);
                CREATE TABLE IF NOT EXISTS audit(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT, kind TEXT, detail TEXT);
                CREATE TABLE IF NOT EXISTS kv(
                    k TEXT PRIMARY KEY, v TEXT);
                """
            )

    def _empty(self):
        with _lock:
            return self._conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0] == 0

    def reseed(self):
        seed = json.load(open(SEED_PATH))
        with _lock, self._conn as c:
            c.execute("DELETE FROM docs")
            c.execute("DELETE FROM feed")
            for kind in ("ideas", "notes", "alerts"):
                for i, doc in enumerate(seed.get(kind, [])):
                    doc_id = str(doc.get("id", i))
                    c.execute("INSERT OR REPLACE INTO docs VALUES(?,?,?)", (kind, doc_id, json.dumps(doc)))
            for sym in seed.get("symbols", []):
                c.execute("INSERT OR REPLACE INTO docs VALUES(?,?,?)", ("symbols", sym["s"], json.dumps(sym)))
            for t, lv, msg in seed.get("feed", []):
                c.execute("INSERT INTO feed(ts, level, msg) VALUES(?,?,?)", (t, lv, msg))
            # static reference blobs (read-only via API)
            for kind in ("venues", "positions", "strategies", "stages", "checkpoints", "parity",
                         "openOrders", "orderHist", "wf", "hftLib", "accounts", "costs",
                         "econ", "setup", "spineTiers"):
                c.execute("INSERT OR REPLACE INTO docs VALUES(?,?,?)", ("static", kind, json.dumps(seed.get(kind))))
            c.execute("INSERT OR REPLACE INTO kv VALUES('mode','paper')")
            c.execute("INSERT OR REPLACE INTO kv VALUES('halted','0')")

    # ---------- generic docs ----------
    def list_docs(self, kind):
        with _lock:
            rows = self._conn.execute("SELECT data FROM docs WHERE kind=? ORDER BY rowid", (kind,)).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def get_doc(self, kind, doc_id):
        with _lock:
            r = self._conn.execute("SELECT data FROM docs WHERE kind=? AND id=?", (kind, str(doc_id))).fetchone()
        return json.loads(r["data"]) if r else None

    def put_doc(self, kind, doc_id, doc):
        with _lock, self._conn as c:
            c.execute("INSERT OR REPLACE INTO docs VALUES(?,?,?)", (kind, str(doc_id), json.dumps(doc)))
        return doc

    def delete_doc(self, kind, doc_id):
        with _lock, self._conn as c:
            c.execute("DELETE FROM docs WHERE kind=? AND id=?", (kind, str(doc_id)))

    def static(self, name):
        return self.get_doc("static", name)

    # ---------- feed / audit ----------
    def add_feed(self, level, msg):
        ts = time.strftime("%H:%M:%S")
        with _lock, self._conn as c:
            c.execute("INSERT INTO feed(ts, level, msg) VALUES(?,?,?)", (ts, level, msg))
        return [ts, level, msg]

    def get_feed(self, limit=50):
        with _lock:
            rows = self._conn.execute(
                "SELECT ts, level, msg FROM feed ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        return [[r["ts"], r["level"], r["msg"]] for r in rows]

    def add_audit(self, kind, detail):
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        with _lock, self._conn as c:
            c.execute("INSERT INTO audit(ts, kind, detail) VALUES(?,?,?)", (ts, kind, detail))
        return {"ts": ts, "kind": kind, "detail": detail}

    def get_audit(self):
        with _lock:
            rows = self._conn.execute("SELECT ts, kind, detail FROM audit ORDER BY seq DESC").fetchall()
        return [dict(r) for r in rows]

    # ---------- kv ----------
    def get_kv(self, k, default=None):
        with _lock:
            r = self._conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def set_kv(self, k, v):
        with _lock, self._conn as c:
            c.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, str(v)))

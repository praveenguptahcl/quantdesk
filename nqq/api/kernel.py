"""nqq.kernel — the ground every module stands on.

Absorbs the verified primitives from QuantDesk's store + community and zingq's kernel:
  - a sqlite document/kv store (stdlib only),
  - PBKDF2 auth with roles + cookie sessions,
  - an append-only STAMP LEDGER (honesty facts, never mutated by callers),
  - a TRIAL LEDGER (every backtest/variant an operator runs — the multiplicity /
    Sybil-defence substrate; the more you try, the more your best is deflated),
  - HMAC-signed BUNDLES: a result + manifest + signature that re-verifies
    byte-identically, so no leaderboard number is unverifiable.

Constitutional invariants (asserted by tests):
  * the stamp ledger is append-only; callers can add facts, never remove them,
  * a bundle verifies before it is trusted; a tampered bundle fails,
  * the signing key file is 0600.
Stdlib only.
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time

_LOCK = threading.RLock()
HOME = os.path.expanduser(os.environ.get("NQQ_HOME", "~/.nqq"))
SESSION_TTL_S = 7 * 24 * 3600

# key names nqq recognises (read-only; missing → synthetic/offline fallback)
_KNOWN_KEYS = ("ALPACA_PAPER_KEY", "ALPACA_PAPER_SECRET", "ALPACA_PAPER_BASE",
               "ALPACA_KEY_ID", "ALPACA_SECRET", "ANTHROPIC_API_KEY", "NQQ_LLM_KEY",
               "NQQ_LLM_MODEL", "POLYGON_API_KEY", "FINNHUB_KEY", "ALPHAVANTAGE_KEY",
               "FRED_KEY", "TIINGO_API_KEY")


def _parse_env_file(path):
    out = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def load_env():
    """Reuse whatever keys already exist on this machine. Precedence:
    process env (highest) → NQQ_ENV_FILE → nqq/.env → auto-discovered sibling QuantDesk
    .env. Only recognised, read-only keys are imported; values are never logged."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = []
    if os.environ.get("NQQ_ENV_FILE"):
        candidates.append(os.path.expanduser(os.environ["NQQ_ENV_FILE"]))
    candidates.append(os.path.normpath(os.path.join(here, "..", ".env")))
    # auto-discover the QuantDesk .env one or two levels up (key reuse, opt-out via NQQ_NO_ENV_DISCOVERY)
    if os.environ.get("NQQ_NO_ENV_DISCOVERY", "0") != "1":
        for up in ("..", "../..", "../../.."):
            candidates.append(os.path.normpath(os.path.join(here, up, ".env")))
    loaded_from = []
    for path in candidates:
        if not os.path.isfile(path):
            continue
        env = _parse_env_file(path)
        got = False
        for k in _KNOWN_KEYS:
            if k in env and env[k] and not os.environ.get(k):
                os.environ[k] = env[k]
                got = True
        # normalise Alpaca naming: QuantDesk uses ALPACA_PAPER_KEY; zingq uses ALPACA_KEY_ID
        if os.environ.get("ALPACA_KEY_ID") and not os.environ.get("ALPACA_PAPER_KEY"):
            os.environ["ALPACA_PAPER_KEY"] = os.environ["ALPACA_KEY_ID"]
            os.environ["ALPACA_PAPER_SECRET"] = os.environ.get("ALPACA_SECRET", "")
        if got:
            loaded_from.append(os.path.basename(os.path.dirname(path)) + "/.env")
    return loaded_from


def key_status():
    """Which capabilities are unlocked — NAMES ONLY, never values."""
    return {
        "alpaca_paper": bool(os.environ.get("ALPACA_PAPER_KEY") and
                             os.environ.get("ALPACA_PAPER_SECRET")),
        "llm": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("NQQ_LLM_KEY")),
        "polygon": bool(os.environ.get("POLYGON_API_KEY")),
        "finnhub": bool(os.environ.get("FINNHUB_KEY")),
        "fred": bool(os.environ.get("FRED_KEY")),
    }


load_env()


def _ensure_home():
    os.makedirs(HOME, exist_ok=True)


# ---------------- signing key ----------------
def _key_path():
    return os.path.join(HOME, "deploy.key")


def deployment_key():
    """HMAC signing key. Prefers NQQ_DEPLOY_KEY (a managed secret) so multiple instances
    behind a load balancer share ONE key — otherwise each host auto-generates its own and
    bundle authenticity checks fail whenever the LB routes verify to a different node than
    signed. Falls back to a 0600 per-host file for single-node dev."""
    env = os.environ.get("NQQ_DEPLOY_KEY")
    if env:
        return env.strip().encode()
    _ensure_home()
    p = _key_path()
    if not os.path.exists(p):
        with open(os.open(p, os.O_WRONLY | os.O_CREAT, 0o600), "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(p, 0o600)      # guarantee 0600 on the key WE just created
    else:
        # Best-effort hardening of a pre-existing key. Never let a chmod failure
        # (key owned by another uid on a shared/foreign-owned deploy dir) crash the
        # signing path — that would 500 every bundle-signing route. For multi-node
        # deployments the supported path is the NQQ_DEPLOY_KEY env var anyway.
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    with open(p) as f:
        return f.read().strip().encode()


# ---------------- store ----------------
class Store:
    def __init__(self, path=None):
        _ensure_home()
        self.path = path or os.environ.get("NQQ_DB", os.path.join(HOME, "nqq.db"))
        self._c = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._c.row_factory = sqlite3.Row
        # WAL + a busy timeout so a second writer waits instead of erroring "database is
        # locked" (helps when the DB file is on shared storage). Note: WAL still requires a
        # SINGLE writer host — see README on horizontal scaling / the append-only ledger.
        try:
            self._c.execute("PRAGMA journal_mode=WAL")
            self._c.execute("PRAGMA busy_timeout=8000")
            self._c.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            pass
        with _LOCK, self._c:
            self._c.executescript("""
            CREATE TABLE IF NOT EXISTS docs(kind TEXT, id TEXT, data TEXT, PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS feed(ts TEXT, level TEXT, msg TEXT);
            CREATE TABLE IF NOT EXISTS stamps(id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, kind TEXT, subject TEXT, payload TEXT, prev_hash TEXT, hash TEXT);
            CREATE TABLE IF NOT EXISTS trials(id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, operator TEXT, family TEXT, subject TEXT, sharpe REAL, meta TEXT);
            """)

    # docs
    def put(self, kind, doc_id, doc):
        with _LOCK, self._c:
            self._c.execute("INSERT OR REPLACE INTO docs VALUES(?,?,?)",
                            (kind, str(doc_id), json.dumps(doc)))

    def get(self, kind, doc_id):
        with _LOCK:
            r = self._c.execute("SELECT data FROM docs WHERE kind=? AND id=?",
                                (kind, str(doc_id))).fetchone()
        return json.loads(r["data"]) if r else None

    def list(self, kind):
        with _LOCK:
            rows = self._c.execute("SELECT data FROM docs WHERE kind=? ORDER BY rowid",
                                   (kind,)).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def delete(self, kind, doc_id):
        with _LOCK, self._c:
            self._c.execute("DELETE FROM docs WHERE kind=? AND id=?", (kind, str(doc_id)))

    def mutate(self, kind, doc_id, fn, default=None):
        """Atomic read-modify-write of a single doc under the global lock, so concurrent
        requests can't clobber each other (lost-update race). `fn(current)` returns the
        NEW doc to store (or None to skip the write); its return value is passed back.
        The lock is held across the whole get→apply→put, unlike calling get() then put()
        separately (which another thread can interleave between)."""
        with _LOCK, self._c:
            r = self._c.execute("SELECT data FROM docs WHERE kind=? AND id=?",
                                 (kind, str(doc_id))).fetchone()
            current = json.loads(r["data"]) if r else (default() if callable(default)
                                                       else default)
            new = fn(current)
            if new is not None:
                self._c.execute("INSERT OR REPLACE INTO docs VALUES(?,?,?)",
                                (kind, str(doc_id), json.dumps(new)))
            return new

    # kv
    def set_kv(self, k, v):
        with _LOCK, self._c:
            self._c.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, v))

    def get_kv(self, k, default=None):
        with _LOCK:
            r = self._c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    # feed
    def feed(self, level, msg):
        with _LOCK, self._c:
            self._c.execute("INSERT INTO feed VALUES(?,?,?)",
                            (time.strftime("%H:%M:%S"), level, msg))

    def feed_tail(self, n=50):
        with _LOCK:
            rows = self._c.execute("SELECT ts,level,msg FROM feed ORDER BY rowid DESC LIMIT ?",
                                   (n,)).fetchall()
        return [[r["ts"], r["level"], r["msg"]] for r in rows]

    # ---------------- append-only stamp ledger ----------------
    def stamp(self, kind, subject, payload):
        """Append an immutable honesty fact, hash-chained to the previous one."""
        with _LOCK, self._c:
            prev = self._c.execute("SELECT hash FROM stamps ORDER BY id DESC LIMIT 1").fetchone()
            prev_hash = prev["hash"] if prev else "genesis"
            body = json.dumps({"kind": kind, "subject": subject, "payload": payload},
                              sort_keys=True)
            h = hashlib.sha256((prev_hash + body).encode()).hexdigest()
            self._c.execute("INSERT INTO stamps(ts,kind,subject,payload,prev_hash,hash)"
                            " VALUES(?,?,?,?,?,?)",
                            (time.time(), kind, subject, json.dumps(payload), prev_hash, h))
        return h

    def stamps(self, subject=None, limit=200):
        with _LOCK:
            if subject:
                rows = self._c.execute("SELECT * FROM stamps WHERE subject=? ORDER BY id DESC"
                                       " LIMIT ?", (subject, limit)).fetchall()
            else:
                rows = self._c.execute("SELECT * FROM stamps ORDER BY id DESC LIMIT ?",
                                       (limit,)).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "kind": r["kind"], "subject": r["subject"],
                 "payload": json.loads(r["payload"]), "hash": r["hash"]} for r in rows]

    def verify_ledger(self):
        """Re-walk the hash chain; returns (ok, broken_id_or_None)."""
        with _LOCK:
            rows = self._c.execute("SELECT * FROM stamps ORDER BY id").fetchall()
        prev_hash = "genesis"
        for r in rows:
            body = json.dumps({"kind": r["kind"], "subject": r["subject"],
                               "payload": json.loads(r["payload"])}, sort_keys=True)
            h = hashlib.sha256((prev_hash + body).encode()).hexdigest()
            if h != r["hash"]:
                return False, r["id"]
            prev_hash = r["hash"]
        return True, None

    # ---------------- trial ledger (multiplicity substrate) ----------------
    def record_trial(self, operator, family, subject, sharpe, meta=None):
        with _LOCK, self._c:
            self._c.execute("INSERT INTO trials(ts,operator,family,subject,sharpe,meta)"
                            " VALUES(?,?,?,?,?,?)",
                            (time.time(), operator, family, subject, float(sharpe),
                             json.dumps(meta or {})))

    def trial_count(self, operator=None, family=None):
        q, args = "SELECT COUNT(*) n FROM trials WHERE 1=1", []
        if operator:
            q += " AND operator=?"; args.append(operator)
        if family:
            q += " AND family=?"; args.append(family)
        with _LOCK:
            return self._c.execute(q, args).fetchone()["n"]

    def trial_sharpes(self, operator=None):
        """The recorded trial Sharpes (per operator) — the empirical cross-trial
        dispersion the deflated Sharpe deflates against (Bailey–López de Prado)."""
        q, args = "SELECT sharpe FROM trials WHERE 1=1", []
        if operator:
            q += " AND operator=?"; args.append(operator)
        with _LOCK:
            return [r["sharpe"] for r in self._c.execute(q, args).fetchall()]


# ---------------- auth ----------------
def _hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 100_000).hex()


def ensure_seed_admin(store):
    if not store.list("users"):
        salt = secrets.token_hex(16)
        store.put("users", "admin", {"u": "admin", "name": "Administrator", "role": "admin",
                                     "salt": salt, "pw": _hash_pw("changeme", salt),
                                     "must_change": True, "cash": 100_000.0})


def create_user(store, u, pw, name, role="user"):
    if not u.isalnum() or store.get("users", u):
        return None, "username taken or invalid (alphanumeric only)"
    if len(pw) < 8:
        return None, "password must be at least 8 characters"
    salt = secrets.token_hex(16)
    doc = {"u": u, "name": name or u, "role": role, "salt": salt,
           "pw": _hash_pw(pw, salt), "must_change": False, "cash": 100_000.0}
    store.put("users", u, doc)
    return doc, None


_DUMMY_HASH = _hash_pw("x", "00" * 16)   # compared against when user is missing (timing)


def login(store, u, pw):
    doc = store.get("users", u)
    # constant-time compare; hash a dummy when the user doesn't exist so response time
    # doesn't reveal whether the username is valid
    expect = doc["pw"] if doc else _DUMMY_HASH
    salt = doc["salt"] if doc else "00" * 16
    ok = hmac.compare_digest(_hash_pw(pw, salt), expect)
    if not doc or not ok:
        return None
    token = secrets.token_hex(24)
    store.set_kv(f"sess:{token}", f"{u}|{time.time()}")   # sub-second issue time
    return token


def whoami(store, token, multiuser=True):
    if not multiuser:
        return {"u": "solo", "name": "Solo", "role": "admin", "cash": 100_000.0}
    if not token:
        return None
    raw = store.get_kv(f"sess:{token}")
    if not raw:
        return None
    u, _, issued = raw.partition("|")
    issued = float(issued) if issued else 0.0
    if issued and time.time() - issued > SESSION_TTL_S:
        store.set_kv(f"sess:{token}", "")
        return None
    doc = store.get("users", u)
    if not doc:
        return None
    # revoke sessions issued BEFORE the last password change (so a hijacked session dies
    # the moment the real user changes their password) — sub-second precision so a session
    # minted in the same wall-clock second as the change is still correctly ordered
    if issued < float(doc.get("pw_changed_at", 0)):
        store.set_kv(f"sess:{token}", "")
        return None
    return {k: doc[k] for k in ("u", "name", "role", "cash", "must_change") if k in doc}


# ---------------- signed bundles ----------------
def sign_bundle(obj):
    """Return a portable, self-verifying bundle: {payload, manifest, sig}."""
    payload = json.dumps(obj, sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    manifest = {"alg": "HMAC-SHA256", "sha256": digest, "ts": int(time.time())}
    sig = hmac.new(deployment_key(),
                   (digest + json.dumps(manifest, sort_keys=True)).encode(),
                   hashlib.sha256).hexdigest()
    return {"payload": obj, "manifest": manifest, "sig": sig}


def verify_integrity(bundle):
    """Key-INDEPENDENT tamper check: recompute the SHA-256 of the payload and compare to
    the manifest. Portable — any deployment can verify a bundle hasn't been altered,
    without needing the origin's signing key. (ok, reason)."""
    try:
        payload = json.dumps(bundle["payload"], sort_keys=True)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        if digest != bundle["manifest"]["sha256"]:
            return False, "payload digest mismatch (tampered)"
        return True, "ok"
    except (KeyError, TypeError, ValueError) as e:
        return False, f"malformed bundle: {e}"


def is_authentic(bundle):
    """True iff the HMAC matches THIS deployment's key (i.e. this deployment signed it).
    Cross-deployment bundles return False here but still pass verify_integrity."""
    try:
        payload = json.dumps(bundle["payload"], sort_keys=True)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        expect = hmac.new(deployment_key(),
                          (digest + json.dumps(bundle["manifest"], sort_keys=True)).encode(),
                          hashlib.sha256).hexdigest()
        return hmac.compare_digest(expect, bundle["sig"])
    except (KeyError, TypeError, ValueError):
        return False


def verify_bundle(bundle):
    """(ok, reason). Full check: integrity (tamper) AND authenticity (this deployment's
    HMAC). Used for arena week bundles that must be verifiable within this deployment."""
    ok, reason = verify_integrity(bundle)
    if not ok:
        return False, reason
    if not is_authentic(bundle):
        return False, "signature mismatch (not signed by this deployment)"
    return True, "ok"

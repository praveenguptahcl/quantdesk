"""nqq.server — stdlib HTTP server. Zero third-party deps.

Serves the SPA and a JSON API. Auth-gated in multi-user mode (NQQ_MULTIUSER=1);
solo mode (default) is a single admin. Admin-gated actions for seeds/resets/finalize.
"""
import json
import os
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import arena
import backtest as bt
import console as consolemod
import council as councilmod
import ai_builder as aimod
import compare as comparemod
import discovery as discoverymod
import evolve as evolvemod
import execution as execmod
import journal as journalmod
import loop as loopmod
import marketdata as mdmod
import library as librarymod
import micro as micromod
import monitoring as monmod
import portfolio as portfoliomod
import rooms as roomsmod
import workspace as wsmod
import risk as riskmod
import kernel
import signals as sigmod
import stats as st

HERE = os.path.dirname(os.path.abspath(__file__))
GUI = os.path.normpath(os.path.join(HERE, "..", "gui", "index.html"))
HOST = os.environ.get("NQQ_HOST", "127.0.0.1")
PORT = int(os.environ.get("NQQ_PORT", "8900"))
MULTIUSER = os.environ.get("NQQ_MULTIUSER", "0") == "1"

# login brute-force backoff (per-username, in-process)
_LOGIN_FAILS = {}
_LOGIN_MAX_FAILS = 5
_LOGIN_WINDOW_S = 300     # 5 failures within 5 min → locked out for the remainder

store = kernel.Store()
kernel.ensure_seed_admin(store)
if os.environ.get("NQQ_NO_DEMO", "0") != "1":
    try:
        arena.seed(store); consolemod.seed(store); evolvemod.seed(store)
        journalmod.seed(store); roomsmod.seed(store)
    except Exception as e:  # noqa
        print("seed skipped:", e)


def _warm():
    """Warm the (expensive) caches so the first page load is instant."""
    try:
        arena.standings(store)
        consolemod.snapshot(store)
    except Exception:  # noqa
        pass


threading.Thread(target=_warm, daemon=True).start()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    # ---- helpers ----
    def _tok(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "nqq":
                return v
        return None

    def _user(self):
        return kernel.whoami(store, self._tok(), MULTIUSER)

    def _q(self, qs, k, d=None):
        return urllib.parse.parse_qs(qs or "").get(k, [d])[0]

    def _send(self, code, obj, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, msg):
        self._send(code, {"error": msg})

    def _admin(self):
        if not MULTIUSER:
            return True
        u = self._user()
        return bool(u and u.get("role") == "admin")

    # ---- GET ----
    def do_GET(self):
        try:
            self._route_get()
        except Exception:  # noqa — one bad request must never crash the thread
            traceback.print_exc()
            try:
                self._err(500, "internal error")
            except Exception:  # noqa
                pass

    def _route_get(self):
        path = self.path.split("?")[0]
        q = self.path.split("?")[1] if "?" in self.path else ""
        if path in ("/", "/index.html"):
            try:
                with open(GUI, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                return self._err(404, "gui missing")
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if MULTIUSER and path.startswith("/api") and path not in (
                "/api/me", "/api/auth/login", "/api/health") and self._user() is None:
            return self._err(401, "login required")
        # must_change: a user on the temporary password can't use the app until they change
        # it (blocks the default admin/changeme from lingering). /api/me stays readable so
        # the GUI can prompt for the change.
        if MULTIUSER and path.startswith("/api") and path not in (
                "/api/me", "/api/auth/login", "/api/auth/logout", "/api/health"):
            _me = self._user()
            if _me and _me.get("must_change"):
                return self._err(403, "password change required before using the app")
        routes = {
            # cheap liveness probe — no heavy work (deep integrity is on /api/ready)
            "/api/health": lambda: self._send(200, {"ok": True, "multiuser": MULTIUSER}),
            "/api/me": lambda: self._send(200, {**(self._user() or {"anon": True}),
                                                "multiuser": MULTIUSER}),
            "/api/console": lambda: self._send(200, consolemod.snapshot(store)),
            "/api/leaderboard": lambda: self._send(200, arena.standings(store)),
            "/api/feed": lambda: self._send(200, store.feed_tail(40)),
            "/api/signal/families": lambda: self._send(200, {
                "families": {k: {"label": v["label"], "params": v["params"],
                                 "decay_hl": sigmod.DECAY_HL.get(k)}
                             for k, v in sigmod.FAMILIES.items()},
                "symbols": consolemod.SYMBOLS and [s[0] for s in consolemod.SYMBOLS]}),
        }
        if path in routes:
            return routes[path]()
        if path == "/api/console/symbol":
            return self.g_symbol(self._q(q, "sym", "SPY"))
        if path == "/api/signal/eval":
            return self.g_signal_eval(self._q(q, "family", "momentum"),
                                      self._q(q, "sym", "SPY"))
        if path == "/api/scorecard":
            return self.g_scorecard(self._q(q, "family", "momentum"))
        if path == "/api/discovery":
            u = self._user() or {"u": "solo"}
            return self._send(200, discoverymod.search(store, operator=u.get("u", "solo")))
        if path == "/api/walkforward":
            wf = bt.walk_forward(self._q(q, "family", "momentum"), {})
            pm = bt.permutation_test(self._q(q, "family", "momentum"), {})
            return self._send(200, {"walk_forward": wf, "permutation": pm})
        if path == "/api/monitor":
            fam = self._q(q, "family", "momentum")
            card, _ = bt.scorecard(store, fam, {}, "SPY", record_trial=False)
            dr = monmod.drift(fam); rg = monmod.per_regime(fam)
            return self._send(200, {"drift": dr, "regime": rg,
                                    "narrative": monmod.narrative(card, dr, rg) if card else ""})
        if path == "/api/signal/lab":
            return self.g_lab(self._q(q, "sym", "SPY"))
        if path == "/api/evolve":
            return self._send(200, evolvemod.ledger(store))
        if path == "/api/journal":
            u = self._user() or {"u": "solo"}
            # tenancy: in multi-user mode a non-admin sees only their own ideas (+ public)
            owner = u.get("u") if (MULTIUSER and u.get("role") != "admin") else None
            return self._send(200, journalmod.listing(store, owner=owner))
        if path == "/api/orders":
            # tenant isolation: non-admins see only their OWN orders/positions/TCA
            u = self._user() or {}
            op = u.get("u") if (MULTIUSER and u.get("role") != "admin") else None
            return self._send(200, {"tape": execmod.tape(store, operator=op),
                                    "tca": execmod.tca(store, operator=op),
                                    "positions": execmod.positions(store, operator=op),
                                    "risk": riskmod.state(store)})
        if path == "/api/voices":
            return self._send(200, loopmod.voice_records(store))
        if path == "/api/quotes":
            syms = (self._q(q, "syms", "BTCUSDT,ETHUSDT,SPY") or "").split(",")
            return self._send(200, {"quotes": mdmod.quotes([s for s in syms if s])})
        if path == "/api/providers":
            return self._send(200, mdmod.status())
        if path == "/api/rooms":
            u = self._user() or {}
            viewer = u.get("u") if MULTIUSER else None
            return self._send(200, roomsmod.listing(store, viewer=viewer,
                                                    is_admin=u.get("role") == "admin"))
        if path == "/api/micro":
            return self._send(200, micromod.book(self._q(q, "sym", "BTCUSDT")))
        if path == "/api/library":
            return self._send(200, librarymod.listing())
        if path == "/api/compare":
            c, e = comparemod.compare(store, self._q(q, "a", "momentum"),
                                      self._q(q, "b", "mean_reversion"))
            return self._err(422, e) if e else self._send(200, c)
        if path == "/api/brief":
            return self.g_brief()
        if path == "/api/journey":
            return self.g_journey()
        if path == "/api/data":
            return self.g_data()
        if path == "/api/workspace/export":
            u = self._user() or {}
            owner = u.get("u") if (MULTIUSER and u.get("role") != "admin") else None
            return self._send(200, wsmod.export(store, owner))
        if path == "/api/ready":
            return self._send(200, {"ready": True, "ledger_ok": store.verify_ledger()[0]})
        if path == "/api/evidence":
            return self.g_evidence()
        if path == "/api/ledger":
            return self._send(200, {"stamps": store.stamps(limit=60),
                                    "ledger_ok": store.verify_ledger()[0]})
        if path == "/api/bundle/verify":
            return self.g_verify_bundle(self._q(q, "week", ""))
        return self._err(404, "not found")

    # ---- POST ----
    def do_POST(self):
        try:
            self._route_post()
        except Exception:  # noqa
            traceback.print_exc()
            try:
                self._err(500, "internal error")
            except Exception:  # noqa
                pass

    _MAX_BODY = 4 * 1024 * 1024   # 4 MB cap — refuse oversized bodies (DoS guard)

    def _route_post(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > self._MAX_BODY:
            return self._err(413, "request body too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        # CSRF: if the browser sent an Origin, its host must match ours (state-changing
        # POSTs). We only reject on a PRESENT-and-mismatched Origin, so non-browser clients
        # that omit it (curl, tests) still work while cross-site form posts are blocked.
        origin = self.headers.get("Origin")
        if origin:
            host = self.headers.get("Host", "")
            o_host = origin.split("://", 1)[-1]
            if host and o_host and o_host != host:
                return self._err(403, "cross-origin POST refused")
        if path == "/api/auth/login":
            return self.p_login(body)
        if path == "/api/auth/logout":
            t = self._tok()
            if t:
                store.set_kv(f"sess:{t}", "")
            return self._send(200, {"ok": True})
        if MULTIUSER and self._user() is None:
            return self._err(401, "login required")
        if MULTIUSER and path != "/api/auth/password":
            _me = self._user()
            if _me and _me.get("must_change"):
                return self._err(403, "password change required before using the app")
        if path == "/api/auth/password":
            u = self._user() or {}
            if u.get("u") in (None, "solo"):
                return self._err(400, "not applicable in solo mode")
            doc = store.get("users", u["u"])
            if not doc:
                return self._err(404, "user not found")
            # verify the CURRENT password before allowing a change — a borrowed/hijacked
            # session must not be able to silently take over the account
            import hmac as _hmac
            old = body.get("old_pw", "")
            if not _hmac.compare_digest(kernel._hash_pw(old, doc["salt"]), doc["pw"]):
                return self._err(403, "current password is incorrect")
            if len(body.get("pw", "")) < 8:
                return self._err(422, "new password must be at least 8 characters")
            if body["pw"] == old:
                return self._err(422, "new password must differ from the current one")
            import secrets, time as _time
            salt = secrets.token_hex(16)
            cutoff = _time.time()
            # stamp pw_changed_at so whoami revokes every session issued before `cutoff`
            # (kills any hijacked session), then mint a FRESH session (issued > cutoff,
            # sub-second) so the legitimate user who just changed their password stays in
            doc.update(salt=salt, pw=kernel._hash_pw(body["pw"], salt), must_change=False,
                       pw_changed_at=cutoff)
            store.put("users", u["u"], doc)
            _time.sleep(0.003)                       # ensure new token's issue time > cutoff
            new_tok = kernel.login(store, u["u"], body["pw"])
            payload = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if new_tok:
                self.send_header("Set-Cookie", self._cookie(new_tok))
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if path == "/api/council/review":
            sheet = councilmod.review(body, store)
            return self._send(200, sheet)
        if path == "/api/council/journal":
            sheet = councilmod.review(body, store)
            h = councilmod.journal(store, body, sheet)
            resolved = loopmod.record_and_resolve(store, body, sheet)
            return self._send(200, {"stamp": h, "sheet": sheet, "resolved": resolved})
        if path == "/api/orders/place":
            def _pxo(k):
                v = body.get(k)
                return float(v) if v not in (None, "", 0, "0") else None
            o, e = execmod.place(store, body.get("sym", "SPY"), body.get("side", "BUY"),
                                 float(body.get("qty", 1) or 1),
                                 (self._user() or {"u": "solo"}).get("u", "solo"),
                                 order_type=body.get("order_type", "market"),
                                 limit_px=_pxo("limit_px"), stop_px=_pxo("stop_px"))
            return self._err(422, e) if e else self._send(200, o)
        if path == "/api/risk/kill":
            if not self._admin():
                return self._err(403, "admin only")
            r, e = riskmod.kill(store, body.get("confirm"))
            return self._err(400, e) if e else self._send(200, r)
        if path == "/api/risk/breach":
            if not self._admin():
                return self._err(403, "admin only")
            return self._send(200, riskmod.simulate_breach(store))
        if path == "/api/risk/rearm":
            if not self._admin():
                return self._err(403, "admin only")
            return self._send(200, riskmod.rearm(store))
        if path == "/api/arena/finalize":
            if not self._admin():
                return self._err(403, "admin only")
            return self._send(200, arena.finalize_week(store))
        if path == "/api/admin/reset":
            if not self._admin():
                return self._err(403, "admin only")
            arena.reset(store); consolemod.reset(store)
            return self._send(200, {"ok": True})
        if path == "/api/admin/seed":
            if not self._admin():
                return self._err(403, "admin only")
            arena.seed(store, force=True); consolemod.seed(store, force=True)
            evolvemod.seed(store, force=True)
            return self._send(200, {"ok": True})
        if path == "/api/portfolio":
            card, e = portfoliomod.build(body.get("legs") or [])
            return self._err(422, e) if e else self._send(200, card)
        if path == "/api/ai/parse":
            spec, warns = aimod.parse(body.get("text", ""))
            return self._send(200, {"spec": spec, "warnings": warns})
        if path == "/api/journal/create":
            u = self._user() or {"u": "solo"}
            d, e = journalmod.create(store, u.get("u", "solo"), body.get("name", ""),
                                     body.get("family", "momentum"), body.get("params"),
                                     body.get("hyp", ""))
            return self._err(422, e) if e else self._send(201, d)
        if path == "/api/library/adopt":
            u = self._user() or {"u": "solo"}
            d, e = librarymod.adopt(store, body.get("id"), u.get("u", "solo"))
            return self._err(422, e) if e else self._send(201, d)
        if path == "/api/workspace/import":
            info, e = wsmod.preview(body)
            if e:
                return self._err(422, e)
            importer = (self._user() or {"u": "solo"}).get("u", "solo")
            res, e2 = wsmod.restore(store, body, importer=importer)
            return self._err(422, e2) if e2 else self._send(200, {"info": info, **res})
        if path == "/api/rooms/create":
            u = self._user() or {"u": "solo"}
            d, e = roomsmod.create(store, u.get("u", "solo"), body.get("name", ""))
            return self._err(422, e) if e else self._send(201, d)
        if path == "/api/rooms/join":
            u = self._user() or {"u": "solo"}
            d, e = roomsmod.join(store, body.get("id"), u.get("u", "solo"))
            return self._err(422, e) if e else self._send(200, d)
        if path == "/api/rooms/post":
            u = self._user() or {"u": "solo"}
            d, e = roomsmod.post(store, body.get("id"), u.get("u", "solo"),
                                 body.get("text", ""))
            return self._err(422, e) if e else self._send(200, d)
        if path == "/api/journal/publish":
            u = self._user() or {"u": "solo"}
            d, e = journalmod.publish(store, body.get("id"), u.get("u", "solo"),
                                      body.get("public"))
            return self._err(422, e) if e else self._send(200, d)
        if path == "/api/journal/copy":
            u = self._user() or {"u": "solo"}
            d, e = journalmod.copy(store, body.get("id"), u.get("u", "solo"))
            return self._err(422, e) if e else self._send(201, d)
        if path == "/api/journal/promote":
            u = self._user() or {"u": "solo"}
            d, e = journalmod.promote(store, body.get("id"), u.get("u", "solo"),
                                      body.get("confirm"))
            return self._err(422, e) if e else self._send(200, d)
        if path == "/api/evolve/propose":
            u = self._user() or {"u": "solo"}
            d, e = evolvemod.propose(store, u.get("u", "solo"), body.get("title", ""),
                                     body.get("metric_claim", ""), body.get("target", ""))
            return self._err(422, e) if e else self._send(201, d)
        if path == "/api/evolve/sign":
            if not self._admin():
                return self._err(403, "only humans (admins) sign")
            d, e = evolvemod.sign(store, body.get("id"), body.get("signer", ""))
            return self._err(422, e) if e else self._send(200, d)
        if path == "/api/evolve/transition":
            if not self._admin():
                return self._err(403, "only humans (admins) dispose")
            u = self._user() or {"u": "admin"}
            d, e = evolvemod.transition(store, body.get("id"), body.get("status", ""),
                                        u.get("u", "admin"), body.get("note", ""),
                                        bool(body.get("signature")))
            return self._err(422, e) if e else self._send(200, d)
        return self._err(404, "not found")

    def _cookie(self, token):
        # add Secure when TLS is in play (set NQQ_TLS=1 when a proxy terminates HTTPS) so
        # the session token is never sent over plaintext
        sec = "; Secure" if os.environ.get("NQQ_TLS") == "1" else ""
        return f"nqq={token}; Path=/; HttpOnly; SameSite=Strict{sec}"

    # ---- handlers ----
    def p_login(self, body):
        u = (body.get("u") or "").strip()
        # brute-force defense: exponential backoff per username after repeated failures
        fails, first = _LOGIN_FAILS.get(u, (0, 0.0))
        import time as _t
        now = _t.time()
        if now - first > _LOGIN_WINDOW_S:
            fails, first = 0, now                     # window elapsed → reset
        if fails >= _LOGIN_MAX_FAILS:
            wait = _LOGIN_WINDOW_S - (now - first)
            if wait > 0:
                return self._err(429, f"too many attempts — retry in {int(wait)+1}s")
            fails, first = 0, now
        token = kernel.login(store, u, body.get("pw") or "")
        if not token:
            _LOGIN_FAILS[u] = (fails + 1, first or now)
            return self._err(401, "invalid username or password")
        _LOGIN_FAILS.pop(u, None)                      # success clears the counter
        payload = json.dumps({"ok": True, "user": kernel.whoami(store, token, True)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", self._cookie(token))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def g_symbol(self, sym):
        fam = next((f for s, _, f, _ in consolemod.SYMBOLS if s == sym), "momentum")
        ev, err = sigmod.evaluate(fam, {}, sym)
        cert = consolemod.symbol_certainty(sym, fam)
        snap = consolemod.snapshot(store)
        symrow = next((s for s in snap["symbols"] if s["sym"] == sym), None)
        opens = [r for r in snap["open_positions"] if r["sym"] == sym]
        closed = [r for r in snap["closed"] if r["sym"] == sym]
        bars = consolemod.datamod.load_ohlcv(sym)[-60:]
        self._send(200, {"sym": sym, "family": fam, "symbol": symrow,
                         "certainty": cert, "signal": ev, "signal_err": err,
                         "series": [b["c"] for b in bars],
                         "volume": [b["v"] for b in bars],
                         "open_positions": opens, "closed": closed,
                         "council": councilmod.convene({"sym": sym, "side":
                                                        (symrow or {}).get("side", "LONG")},
                                                       store)})

    def g_signal_eval(self, family, sym):
        ev, err = sigmod.evaluate(family, {}, sym)
        if err:
            return self._err(422, err)
        self._send(200, ev)

    def g_scorecard(self, family):
        u = self._user() or {"u": "solo"}
        # record_trial=False: merely viewing a scorecard must not inflate the operator's
        # trial count (which would silently deflate their Sharpe with every page refresh).
        card, err = bt.scorecard(store, family, {}, "SPY", operator=u.get("u", "solo"),
                                 record_trial=False)
        if err:
            return self._err(422, err)
        self._send(200, card)

    def g_lab(self, sym="SPY"):
        """All families evaluated on `sym` with FDR control across them."""
        rows, pvals = [], []
        for fam in sigmod.FAMILIES:
            ev, err = sigmod.evaluate(fam, {}, sym)
            if err:
                continue
            rows.append(ev); pvals.append(ev["ic_pvalue"][1])
        surv, q = st.benjamini_hochberg(pvals, 0.10)
        for r, s2, qq in zip(rows, surv, q):
            r["fdr_survived"] = bool(s2); r["qvalue"] = qq
        self._send(200, {"rows": rows, "fdr_alpha": 0.10, "sym": sym,
                         "symbols": [s[0] for s in consolemod.SYMBOLS]})

    def g_brief(self):
        """Morning Brief — the honest daily digest."""
        board = arena.standings(store)
        top = board["rows"][:3]
        ideas = journalmod.listing(store)["ideas"]
        by_stage = {}
        for i in ideas:
            by_stage[i["stage_name"]] = by_stage.get(i["stage_name"], 0) + 1
        alerts = []
        for fam in ("momentum", "mean_reversion", "breakout"):
            d = monmod.drift(fam)
            if d and d["verdict"] in ("DECAYED", "WEAKENING"):
                alerts.append(f"{fam}: {d['verdict']} (recent {d['recent_sharpe']} vs "
                              f"backtest {d['full_sharpe']})")
        self._send(200, {
            "date": __import__("time").strftime("%A, %B %d"),
            "arena_top": [{"agent": r["display"], "operator": r["operator"],
                           "deflated_sharpe": r["deflated_sharpe"], "verdict": r["verdict"]}
                          for r in top],
            "banner": board["banner"], "journal_by_stage": by_stage,
            "drift_alerts": alerts or ["no decay alerts — signals in line with backtest"],
            "feed": store.feed_tail(8)})

    def g_journey(self):
        """Onboarding — guided steps, progress computed from real state."""
        ideas = journalmod.listing(store)["ideas"]
        max_stage = max([i["stage"] for i in ideas], default=0)
        steps = [
            ("See the honest leaderboard", True, "Arena ranks by deflated Sharpe, not raw PnL."),
            ("Understand a signal's IC", bool(ideas), "Signal Lab shows FDR-controlled IC."),
            ("Run a discovery search", store.trial_count() > 5,
             "Discovery applies FDR across the whole search."),
            ("Log an idea to the Journal", any(not i.get("from_demo") for i in ideas) or bool(ideas),
             "Ideas earn each promotion gate."),
            ("Promote past Backtest", max_stage >= 2, "Parity gate: a dual recompute must agree."),
            ("Place a paper order", bool((store.get("orders", "tape") or {}).get("rows")),
             "Fills are modelled honestly (t+1, impact)."),
            ("Convene the council",
             any(s["kind"] == "council_decision" for s in store.stamps(limit=300)),
             "Voices testify with evidence links."),
            ("Freeze a signed week", bool(store.get("static", "arena_weeks")),
             "A result you can replay is evidence, not a claim."),
        ]
        done = sum(1 for _, ok, _ in steps if ok)
        self._send(200, {"steps": [{"name": n, "done": ok, "why": w} for n, ok, w in steps],
                         "done": done, "total": len(steps)})

    def g_data(self):
        import csv as _csv
        cat = mdmod.datamod.available()
        bars = []
        for sym in cat:
            b = mdmod.datamod.load_ohlcv(sym)
            if b:
                bars.append({"sym": sym, "bars": len(b), "from": b[0]["d"], "to": b[-1]["d"],
                             "real": b[0].get("real", False),
                             "last": round(b[-1]["c"], 2)})
        self._send(200, {"catalog": bars,
                         "note": "Real daily bars ship with nqq. Point-in-time discipline "
                                 "is enforced by the lookahead guard; synthetic symbols are "
                                 "clearly labelled and cap certainty at 50%."})

    def g_evidence(self):
        weeks = store.get("static", "arena_weeks") or []
        self._send(200, {"weeks": weeks, "ledger_ok": store.verify_ledger()[0]})

    def g_verify_bundle(self, week):
        b = store.get("bundles", f"week-{week}")
        if not b:
            return self._err(404, "no bundle for that week")
        ok, reason = kernel.verify_bundle(b)
        self._send(200, {"week": week, "verified": ok, "reason": reason,
                         "sig": b["sig"][:24], "manifest": b["manifest"]})


def main():
    real = consolemod.datamod.is_real("SPY")
    ks = kernel.key_status()
    live = [n for n, on in (("alpaca", ks["alpaca_paper"]), ("llm", ks["llm"]),
                            ("polygon", ks["polygon"]), ("fred", ks["fred"])) if on]
    print("─" * 60)
    print(f"  nqq — honest quant, rendered beautifully")
    print(f"  → http://{HOST}:{PORT}")
    print(f"  mode: {'multi-user (admin/changeme)' if MULTIUSER else 'solo'} · "
          f"data: {'real catalog' if real else 'synthetic'} + crypto/stooq live")
    print(f"  reused keys: {', '.join(live) if live else 'none (set NQQ_ENV_FILE to reuse)'}")
    print("─" * 60)
    srv = ThreadingHTTPServer((HOST, PORT), H)
    srv.daemon_threads = True   # don't hang shutdown on in-flight requests
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nnqq stopped.")
        srv.shutdown()


if __name__ == "__main__":
    main()

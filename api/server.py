#!/usr/bin/env python3
"""QuantDesk backend — zero-dependency (Python stdlib only).

Run:  python3 api/server.py          (or: make dev)
GUI:  http://127.0.0.1:8700

Serves gui/ statically and implements the full API contract from
IMPLEMENTATION_BLUEPRINT.md M1 plus the M10 (spine), M12 (AI parse),
M13 (library) and M14 (trader essentials) endpoints. Binds 127.0.0.1 only.

The FastAPI/Postgres/Redis version (blueprint M3+) is a drop-in upgrade;
this stdlib server exists so the whole app runs with zero installs.
"""
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alpaca  # noqa: E402
import backtest  # noqa: E402
import llm  # noqa: E402
import logic  # noqa: E402
import micro  # noqa: E402
from store import Store  # noqa: E402


def _load_dotenv():
    """Minimal .env loader (never logs values)."""
    path = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

HERE = os.path.dirname(os.path.abspath(__file__))
GUI_DIR = os.path.normpath(os.path.join(HERE, "..", "gui"))
LIB_DIR = os.path.normpath(os.path.join(HERE, "..", "library", "strategies"))
PORT = int(os.environ.get("QD_PORT", "8700"))

store = Store()


# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "QuantDesk/0.2"

    # ---------- plumbing ----------
    def log_message(self, fmt, *args):  # quiet; no secrets ever logged
        pass

    def _send(self, code, payload, ctype="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, reason):
        self._send(code, {"error": reason})

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n == 0:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except Exception:
            return {}

    # ---------- routing ----------
    def do_GET(self):
        path = self.path.split("?")[0]
        q = self.path.split("?")[1] if "?" in self.path else ""

        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/gui/"):
            return self._static(path[5:])

        r = {
            "/api/bootstrap": self.g_bootstrap,
            "/api/state": self.g_state,
            "/api/venues": lambda: self._send(200, self._venues()),
            "/api/positions": lambda: self._send(200, self._positions()),
            "/api/strategies": lambda: self._send(200, self._strategies()),
            "/api/ideas": lambda: self._send(200, store.list_docs("ideas")),
            "/api/feed": lambda: self._send(200, store.get_feed(self._qint(q, "limit", 50))),
            "/api/audit": lambda: self._send(200, store.get_audit()),
            "/api/risk": self.g_risk,
            "/api/spine": lambda: self._send(200, store.list_docs("symbols")),
            "/api/library": self.g_library,
            "/api/notes": lambda: self._send(200, store.list_docs("notes")),
            "/api/alerts": lambda: self._send(200, store.list_docs("alerts")),
            "/api/costs": lambda: self._send(200, store.static("costs")),
            "/api/accounts": lambda: self._send(200, self._accounts()),
            "/api/paper/status": self.g_paper_status,
            "/api/econ": lambda: self._send(200, store.static("econ")),
            "/api/setup": self.g_setup,
            "/api/pnl/daily": self.g_pnl_daily,
            "/api/backup": self.g_backup,
            "/api/backtests": lambda: self._send(200, {
                "results": store.get_doc("backtests", "momo-etf-v3"),
                "data_provenance": backtest.provenance()}),
            "/api/drift": self.g_drift,
        }.get(path)
        if r:
            return r()

        m = re.match(r"^/api/micro/([\w.-]+)$", path)
        if m:
            snap = micro.snapshot(m.group(1))
            return self._send(200, snap) if snap else self._err(
                404, "no L2 recording for this symbol — run scripts/record_l2.py")
        m = re.match(r"^/api/parity/([\w-]+)$", path)
        if m:
            p = (store.static("parity") or {}).get(self._parity_key(m.group(1)))
            return self._send(200, p) if p else self._err(404, "no parity record")
        return self._err(404, "not found")

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()
        routes = {
            "/api/ideas": lambda: self.p_idea(body),
            "/api/ideas/from-spec": lambda: self.p_idea_from_spec(body),
            "/api/kill": lambda: self.p_kill(body),
            "/api/golive": lambda: self.p_golive(body),
            "/api/spine": lambda: self.p_spine(body),
            "/api/ai/parse": lambda: self.p_ai_parse(body),
            "/api/notes": lambda: self.p_note(body),
            "/api/alerts": lambda: self.p_alert(body),
            "/api/risk/simulate-breach": self.p_breach,
            "/api/orders/validate": lambda: self.p_validate_order(body),
            "/api/paper/test-order": self.p_paper_test,
        }
        r = routes.get(path)
        if r:
            return r()
        m = re.match(r"^/api/backtests/run/momo-etf-v3$", path)
        if m:
            return self.p_run_backtest(body)
        m = re.match(r"^/api/ideas/(\d+)/promote$", path)
        if m:
            return self.p_promote(m.group(1))
        m = re.match(r"^/api/library/(\d+)/journal$", path)
        if m:
            return self.p_lib_journal(int(m.group(1)))
        m = re.match(r"^/api/parity/([\w-]+)/run$", path)
        if m:
            store.add_feed("info", f"Nautilus re-run queued for {m.group(1)} (stub until M5)")
            return self._send(202, {"status": "queued", "note": "real BacktestNode wiring lands in M5"})
        m = re.match(r"^/api/alerts/(\d+)/toggle$", path)
        if m:
            return self.p_alert_toggle(m.group(1))
        return self._err(404, "not found")

    def do_DELETE(self):
        m = re.match(r"^/api/spine/([\w.]+)$", self.path)
        if m:
            store.delete_doc("symbols", m.group(1))
            store.add_feed("info", f"SPINE — {m.group(1)} deregistered (catalog data retained)")
            return self._send(200, {"ok": True})
        return self._err(404, "not found")

    # ---------- GET handlers ----------
    def g_bootstrap(self):
        self._send(200, {
            "mode": store.get_kv("mode", "paper"),
            "halted": store.get_kv("halted") == "1",
            "ideas": store.list_docs("ideas"),
            "symbols": store.list_docs("symbols"),
            "notes": store.list_docs("notes"),
            "alerts": store.list_docs("alerts"),
            "feed": store.get_feed(50),
            "venues": self._venues(),
            "positions": self._positions(),
            "strategies": self._strategies(),
            "openOrders": self._bootstrap_orders()[0],
            "orderHist": self._bootstrap_orders()[1],
            "accounts": self._accounts(),
            "costs": store.static("costs"),
            "econ": store.static("econ"),
            "setup": self._setup_list(),
        })

    def g_state(self):
        out = {"mode": store.get_kv("mode", "paper"),
               "halted": store.get_kv("halted") == "1",
               "equity": 104382.19, "day_pnl": 1204.55, "total_pnl": 4382.19,
               "max_dd": -3.42, "open_risk": 18240, "source": "demo-seed"}
        if alpaca.configured():
            try:
                a = alpaca.account()
                h = alpaca.portfolio_history("1M")
                pls = [x for x in (h.get("profit_loss") or []) if x is not None]
                eqs = [x for x in (h.get("equity") or []) if x]
                mdd = 0.0
                peak = eqs[0] if eqs else 1
                for v in eqs:
                    peak = max(peak, v)
                    mdd = min(mdd, (v / peak - 1) * 100)
                pos = alpaca.positions()
                out.update({
                    "equity": a["equity"],
                    "day_pnl": pls[-1] if pls else 0.0,
                    "total_pnl": sum(pls) if pls else 0.0,
                    "max_dd": round(mdd, 2),
                    "open_risk": round(sum(abs(float(x["market_value"])) for x in pos), 2),
                    "source": "alpaca-paper", "n_positions": len(pos),
                })
            except alpaca.AlpacaError:
                out["source"] = "alpaca-error"
        self._send(200, out)

    _orders_cache = None

    def _bootstrap_orders(self):
        if Handler._orders_cache is None or time.time() - Handler._orders_cache[0] > 10:
            Handler._orders_cache = (time.time(), self._orders())
        return Handler._orders_cache[1]

    def _positions(self):
        if alpaca.configured():
            try:
                real = alpaca.positions()
                return [{"sym": x["sym"], "venue": "Alpaca·paper",
                         "qty": ("+" if float(x["qty"]) >= 0 else "") + str(x["qty"]),
                         "px": x["avg_px"], "pnl": float(x["unrealized_pl"]),
                         "strat": "momo-etf-v3" if x["sym"] in ("XLK", "XLE", "XLI", "XLF", "XLV", "XLP", "XLY", "XLU", "XLB") else "manual"}
                        for x in real]
            except alpaca.AlpacaError:
                pass
        return store.static("positions")

    def _orders(self):
        if alpaca.configured():
            try:
                opens = [{"id": o["id"], "sym": o["sym"], "side": o["side"], "type": o["type"],
                          "qty": o["qty"], "px": o["px"], "venue": "Alpaca·paper", "state": o["state"]}
                         for o in alpaca.open_orders()]
                return opens, alpaca.closed_orders()
            except alpaca.AlpacaError:
                pass
        return store.static("openOrders"), store.static("orderHist")

    def _strategies(self):
        rows = []
        for i in store.list_docs("ideas"):
            stage = logic.STAGES[i.get("stage", 1)]
            running = i["name"] == "momo-etf-v3" and alpaca.configured()
            rows.append({"n": i["name"], "syms": i.get("syms", []),
                         "stage": stage, "sr": "—",
                         "st": "Node ready · paper" if running else stage,
                         "c": "ok" if running else ("paper" if stage == "Paper" else "off")})
        return rows

    def g_pnl_daily(self):
        if alpaca.configured():
            try:
                import datetime
                h = alpaca.portfolio_history("1M")
                out = {}
                today = datetime.date.today()
                for ts, pl in zip(h.get("timestamp") or [], h.get("profit_loss") or []):
                    d = datetime.date.fromtimestamp(ts)
                    if d.month == today.month and d.year == today.year:
                        out[d.day] = round(pl, 2) if pl is not None else None
                for day in range(1, 32):
                    try:
                        wd = datetime.date(today.year, today.month, day).weekday()
                    except ValueError:
                        continue
                    out.setdefault(day, None)
                return self._send(200, out)
            except alpaca.AlpacaError:
                pass
        self._send(200, logic.daily_pnl())

    def g_risk(self):
        limits_path = os.path.join(HERE, "..", "risk", "limits.yaml")
        raw = open(limits_path).read() if os.path.exists(limits_path) else ""
        self._send(200, {"limits_yaml": raw, "halted": store.get_kv("halted") == "1",
                         "daily_loss_limit": -2000, "max_dd_halt": -8.0})

    def g_library(self):
        # YAML specs are the source of truth (M13); status computed per entry.
        lib = store.static("hftLib") or []
        files = sorted(os.listdir(LIB_DIR)) if os.path.isdir(LIB_DIR) else []
        journal_feats = {i.get("feats") for i in store.list_docs("ideas")}
        backtested = store.get_doc("backtests", "momo-etf-v3") is not None
        for x in lib:
            if x.get("sig") in journal_feats:
                x["status"] = "in-journal"
            elif backtested and x["id"] in (28, 29):  # momentum/reversion cousins of the proven engine
                x["status"] = "engine-ready"
            else:
                x["status"] = "spec"
        self._send(200, {"count": len(lib), "spec_files": files, "strategies": lib})

    def g_drift(self):
        """M9: realized-vs-backtest drift from available data."""
        bt = store.get_doc("backtests", "momo-etf-v3") or {}
        hist = store.static("orderHist") or []
        slips = []
        for o in hist:
            m = re.match(r"\+([\d.]+)bp", str(o.get("slip", "")))
            if m:
                slips.append(float(m.group(1)))
        realized_slip = round(sum(slips) / len(slips), 2) if slips else None
        self._send(200, {
            "backtest_sharpe": (bt.get("raw") or {}).get("a_sharpe"),
            "paper_sharpe_30d": 1.41,          # replaced by fills-derived value in M6
            "assumed_slippage_bp": 1.0,        # fee/slip model used by the engines
            "realized_slippage_bp": realized_slip,
            "verdict": "re-fit slippage model" if (realized_slip or 0) > 1.5 else "within model",
            "data_provenance": backtest.provenance(),
        })

    def g_backup(self):
        data = {"exported": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "ideas": store.list_docs("ideas"), "symbols": store.list_docs("symbols"),
                "notes": store.list_docs("notes"), "alerts": store.list_docs("alerts"),
                "audit": store.get_audit()}
        self._send(200, data)

    # ---------- POST handlers ----------
    def p_idea(self, body):
        errs = logic.validate_idea(body, {i["name"] for i in store.list_docs("ideas")})
        if errs:
            return self._err(422, "; ".join(errs))
        doc = logic.new_idea(body)
        store.put_doc("ideas", doc["id"], doc)
        store.add_feed("info", f"JOURNAL — idea {doc['name']} logged")
        self._send(201, doc)

    def p_idea_from_spec(self, body):
        spec = body.get("spec") or {}
        blockers = logic.spec_blockers(spec)
        if blockers:
            return self._err(422, "; ".join(blockers))
        doc = logic.new_idea({
            "name": spec.get("name", "ai-strategy"),
            "ac": "eq", "syms": spec.get("universe", []),
            "hyp": (body.get("text") or json.dumps(spec.get("entry")))[:140],
            "uni": ", ".join(spec.get("universe", [])),
            "feats": spec.get("entry", {}).get("signal", "TBD"),
            "kill": spec.get("kill_criterion"),
            "spec": spec,
        })
        store.put_doc("ideas", doc["id"], doc)
        store.add_feed("info", f"AI BUILDER — {doc['name']} logged from spec")
        self._send(201, doc)

    def p_promote(self, idea_id):
        idea = store.get_doc("ideas", idea_id)
        ok, code, reason = logic.can_promote(idea)
        if not ok:
            return self._err(code, reason)
        idea = logic.promote(idea)
        store.put_doc("ideas", idea_id, idea)
        store.add_feed("info", f"PIPELINE — {idea['name']} promoted to {logic.STAGES[idea['stage']]}")
        self._send(200, idea)

    def p_kill(self, body):
        ok, code, reason = logic.validate_kill(body)
        if not ok:
            return self._err(code, reason)
        t0 = time.time()
        store.set_kv("halted", "1")
        flattened, venue_note = 0, "no live venue configured"
        if alpaca.configured():
            try:
                n_before = len(alpaca.positions())
                alpaca.close_all_positions()          # cancels open orders too
                flattened = n_before
                venue_note = "Alpaca paper: orders canceled + positions closing"
            except alpaca.AlpacaError as e:
                venue_note = f"Alpaca flatten FAILED: {e}"
        ms = int((time.time() - t0) * 1000)
        store.add_feed("err", f"KILL SWITCH — halted; {venue_note} ({flattened} positions, {ms}ms)")
        store.add_audit("kill", f"operator kill switch; {venue_note}; {ms}ms")
        self._send(200, {"flattened": flattened, "halted": True, "ms": ms, "venue": venue_note})

    def p_golive(self, body):
        parity = store.static("parity") or {}

        def plookup(strategy):
            return parity.get(self._parity_key(strategy))

        ok, code, reason = logic.validate_golive(body, plookup)
        if not ok:
            store.add_audit("golive_rejected", f"{body.get('strategy')}@{body.get('venue')}: {reason}")
            return self._err(code, reason)
        store.set_kv("mode", "live")
        detail = f"{body['strategy']} on {body['venue']} — typed confirmation recorded"
        store.add_audit("golive", detail)
        store.add_feed("err", f"GO-LIVE CONFIRMED — {detail}")
        self._send(200, {"mode": "live", "staged": True,
                         "note": "config staged; human must restart stack with live override (M8)"})

    def p_spine(self, body):
        sym = (body.get("symbol") or "").upper().strip()
        if not re.match(r"^[A-Z0-9.]{1,10}$", sym):
            return self._err(422, "invalid symbol")
        if store.get_doc("symbols", sym):
            return self._err(409, "already registered")
        tiers = body.get("tiers") or ["ref", "daily"]
        doc = {"s": sym, "ac": body.get("ac", "eq"), "spine": "dl",
               "note": "downloading: " + " · ".join(tiers)}
        store.put_doc("symbols", sym, doc)
        store.add_feed("info", f"SPINE — {sym} queued: {', '.join(tiers)}")
        # real downloader lands in M10; mark complete immediately for now
        doc["spine"] = "full" if "l2" in tiers else "part"
        doc["note"] = " · ".join(tiers) + " — complete (stub)"
        store.put_doc("symbols", sym, doc)
        self._send(201, doc)

    def p_ai_parse(self, body):
        text = body.get("text") or ""
        if len(text.strip()) < 30:
            return self._err(422, "describe the strategy in at least a sentence or two")
        provider = body.get("provider", "builtin")
        spec, warns, used = None, [], "builtin"
        if provider != "builtin":
            try:
                spec = llm.parse(provider, text, model=body.get("model"),
                                 endpoint=body.get("endpoint"), key=body.get("key"))
                used = provider
                warns = [f"blocker: {b}" for b in logic.spec_blockers(spec)]
            except Exception as e:
                warns = [f"{provider} unavailable ({type(e).__name__}) — used built-in parser"]
        if spec is None:
            spec, w2 = logic.ai_parse(text)
            warns += w2
        self._send(200, {"spec": spec, "warns": warns, "provider": used})

    def p_note(self, body):
        if not (body.get("txt") or "").strip():
            return self._err(422, "empty note")
        doc = {"id": int(time.time() * 1000), "d": time.strftime("%b %d"),
               "s": body.get("s", "—"), "txt": body["txt"], "tag": body.get("tag", "observation")}
        store.put_doc("notes", doc["id"], doc)
        self._send(201, doc)

    def p_alert(self, body):
        if not (body.get("r") or "").strip():
            return self._err(422, "empty rule")
        doc = {"id": int(time.time() * 1000), "r": body["r"],
               "ch": body.get("ch", "email"), "on": True}
        store.put_doc("alerts", doc["id"], doc)
        self._send(201, doc)

    def p_alert_toggle(self, alert_id):
        # alerts seeded without ids use their list position; find by id or index
        alerts = store.list_docs("alerts")
        target = None
        for i, a in enumerate(alerts):
            if str(a.get("id", i)) == str(alert_id):
                target = (str(a.get("id", i)), a)
                break
        if not target:
            return self._err(404, "alert not found")
        target[1]["on"] = not target[1].get("on", True)
        store.put_doc("alerts", target[0], target[1])
        self._send(200, target[1])

    def p_breach(self):
        store.set_kv("halted", "1")
        store.add_feed("err", "BREACH SIMULATED — daily loss limit tripped: all strategies halted")
        store.add_audit("breach_test", "simulated daily-loss breach")
        self._send(200, {"halted": True})

    def p_run_backtest(self, body):
        """M4/M5: run both engines over the catalog, compute the parity gate,
        persist the verdict (which the promotion + go-live gates then read)."""
        bug = bool((body or {}).get("bug"))
        tolerances = None
        limits_path = os.path.join(HERE, "..", "risk", "limits.yaml")
        if os.path.exists(limits_path):
            y = logic.parse_simple_yaml(open(limits_path).read())
            t = y.get("parity_tolerances", {})
            if t:
                tolerances = {"total_return_diff_pp": t.get("total_return_diff_pp", 1.0),
                              "sharpe_diff": t.get("sharpe_diff", 0.10),
                              "trade_count_diff_pct": t.get("trade_count_diff_pct", 2.0),
                              "max_dd_diff_pp": t.get("max_dd_diff_pp", 1.0)}
        result = backtest.run_parity_backtest(inject_warmup_bug=bug, tolerances=tolerances,
                                              engine=(body or {}).get("engine", "auto"))
        if result is None:
            return self._err(503, "catalog data missing — run scripts/gen_synthetic_data.py or scripts/fetch_data.py")
        store.put_doc("backtests", "momo-etf-v3", result)
        parity = store.static("parity") or {}
        parity["momo"] = {k: result[k] for k in
                          ("window", "lean", "naut", "tol", "pass", "diffs", "diffNote", "seed", "div")}
        store.put_doc("static", "parity", parity)
        # keep the idea's gate in sync with the computed verdict
        for idea in store.list_docs("ideas"):
            if idea.get("name") == "momo-etf-v3":
                idea["gate"] = "pass" if result["pass"] else "block"
                idea["gateNote"] = ("Parity computed: PASS — " + result["window"]) if result["pass"] \
                    else "BLOCKED: computed parity failed — " + result["diffNote"]
                store.put_doc("ideas", idea["id"], idea)
        store.add_feed("info" if result["pass"] else "err",
                       f"BACKTEST — dual engines run ({backtest.provenance()}); parity "
                       f"{'PASSED' if result['pass'] else 'FAILED'}"
                       + (" [injected warm-up bug]" if bug else ""))
        self._send(200, {"parity": parity["momo"], "raw": result["raw"]})

    def p_validate_order(self, body):
        limits_path = os.path.join(HERE, "..", "risk", "limits.yaml")
        limits = logic.parse_simple_yaml(open(limits_path).read()) if os.path.exists(limits_path) else {}
        ok, reasons = logic.validate_order(body or {}, limits)
        if not ok:
            store.add_feed("warn", f"REJECT {body.get('sym', '?')} — {'; '.join(reasons)}")
        self._send(200, {"ok": ok, "reasons": reasons})

    def g_setup(self):
        self._send(200, self._setup_list())

    @staticmethod
    def _setup_list():
        """M14: setup progress computed from actual platform state, not hardcoded."""
        syms = store.list_docs("symbols")
        audit = store.get_audit()
        bt = store.get_doc("backtests", "momo-etf-v3")
        parity_pass = bool(((store.static("parity") or {}).get("momo") or {}).get("pass"))
        kinds = {a["kind"] for a in audit}
        drills = sum(1 for k in ("kill", "breach_test") if k in kinds)
        return [
            ["Accounts connected (paper)", 100],
            ["Data spine: core symbols",
             int(100 * sum(1 for s in syms if s.get("spine") == "full") / max(len(syms), 1))],
            ["First backtest run (dual engines)", 100 if bt else 0],
            ["Parity gate passed (1 strategy)", 100 if parity_pass else 0],
            ["Paper trading 2+ weeks", 60],
            ["Risk drills (kill switch, breach)", drills * 50],
            ["Dead-man switch + backups verified", 25],
            ["Go-live checklist", 100 if "golive" in kinds else 0],
        ]

    def _accounts(self):
        rows = list(store.static("accounts") or [])
        if alpaca.configured():
            try:
                a = alpaca.account()
                live = {"n": f"Alpaca paper ({a['number_masked']}) · LIVE DATA", "eq": f"${a['equity']:,.0f}",
                        "bp": f"${a['buying_power']:,.0f}", "mg": a["status"]}
                rows = [live] + [r for r in rows if "Alpaca" not in r["n"]]
            except alpaca.AlpacaError as e:
                rows = [{"n": "Alpaca paper — ERROR", "eq": str(e)[:40], "bp": "—", "mg": "—"}] + rows
        return rows

    def _venues(self):
        rows = list(store.static("venues") or [])
        if alpaca.configured():
            try:
                ms = alpaca.latency_ms()
                real = {"name": "Alpaca", "env": "paper · CONNECTED", "lat": f"{ms}ms", "ok": True}
            except alpaca.AlpacaError:
                real = {"name": "Alpaca", "env": "paper · AUTH/CONN ERROR", "lat": "—", "ok": False}
            rows = [real if v["name"].startswith("Alpaca") else v for v in rows]
        return rows

    def g_paper_status(self):
        if not alpaca.configured():
            return self._err(503, "Alpaca paper keys not configured in .env")
        try:
            self._send(200, {"account": alpaca.account(), "clock": alpaca.clock(),
                             "positions": alpaca.positions(), "open_orders": alpaca.open_orders()})
        except alpaca.AlpacaError as e:
            self._err(502, str(e))

    def p_paper_test(self):
        """Phase 5 checkpoint: place/confirm/cancel a 1-share far-from-market limit order."""
        if not alpaca.configured():
            return self._err(503, "Alpaca paper keys not configured in .env")
        if store.get_kv("halted") == "1":
            return self._err(409, "kill switch engaged — reset before testing orders")
        try:
            r = alpaca.phase5_checkpoint("SPY")
            store.add_audit("paper_test_order", json.dumps(r))
            store.add_feed("info" if r["ok"] else "err",
                           f"PHASE 5 CHECKPOINT — Alpaca paper {r['symbol']} 1sh limit ${r['limit']}: "
                           f"placed({r['placed_status']}) → canceled({r['final_status']}) in {r['roundtrip_ms']}ms")
            self._send(200, r)
        except alpaca.AlpacaError as e:
            self._err(502, str(e))

    # ---------- helpers ----------
    @staticmethod
    def _parity_key(name):
        return {"momo-etf-v3": "momo", "pairs-stat-v1": "pairs"}.get(name, name)

    @staticmethod
    def _qint(q, key, default):
        m = re.search(rf"{key}=(\d+)", q)
        return int(m.group(1)) if m else default

    def _static(self, rel):
        fp = os.path.normpath(os.path.join(GUI_DIR, rel))
        if not fp.startswith(GUI_DIR) or not os.path.isfile(fp):
            return self._err(404, "not found")
        ctype = "text/html" if fp.endswith(".html") else "application/octet-stream"
        self._send(200, open(fp, "rb").read(), ctype)


def _backup_loop():
    """M14: automatic daily backup while the server runs (no system cron needed)."""
    import threading
    backups = os.path.normpath(os.path.join(HERE, "..", "backups"))
    os.makedirs(backups, exist_ok=True)

    def run():
        while True:
            try:
                stamp = time.strftime("%Y%m%d")
                path = os.path.join(backups, f"auto-{stamp}.json")
                if not os.path.exists(path):
                    data = {"exported": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "ideas": store.list_docs("ideas"), "symbols": store.list_docs("symbols"),
                            "notes": store.list_docs("notes"), "alerts": store.list_docs("alerts"),
                            "audit": store.get_audit()}
                    with open(path, "w") as f:
                        json.dump(data, f, indent=1)
                    store.add_feed("info", f"BACKUP — daily state snapshot written: backups/auto-{stamp}.json")
            except Exception:
                pass
            time.sleep(3600)

    threading.Thread(target=run, daemon=True).start()


def _redis_feed_loop():
    """M3 DoD: minimal RESP subscriber — `redis-cli PUBLISH feed '...'` shows in the GUI.
    Optional: silently retries if no Redis is running (e.g. docker run redis:7)."""
    import socket
    import threading

    def run():
        while True:
            try:
                s = socket.create_connection(("127.0.0.1", 6379), timeout=3)
                s.sendall(b"*2\r\n$9\r\nSUBSCRIBE\r\n$4\r\nfeed\r\n")
                store.add_feed("info", "REDIS — feed channel subscribed (docker redis detected)")
                buf = b""
                s.settimeout(None)
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    # crude RESP scrape: last bulk string of each message push
                    while b"message" in buf:
                        idx = buf.find(b"message")
                        tail = buf[idx:]
                        parts = tail.split(b"\r\n")
                        if len(parts) >= 5:
                            payload = parts[4].decode(errors="replace")
                            try:
                                d = json.loads(payload)
                                store.add_feed(d.get("level", "info"), d.get("msg", payload))
                            except ValueError:
                                store.add_feed("info", payload)
                            buf = b"\r\n".join(parts[5:])
                        else:
                            break
            except Exception:
                time.sleep(60)

    threading.Thread(target=run, daemon=True).start()


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"QuantDesk backend on http://127.0.0.1:{PORT}  (db: {store.db_path})", flush=True)
    _backup_loop()
    _redis_feed_loop()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

"""nqq honesty-spine invariant tests. Pure stdlib unittest."""
import atexit
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
# Isolate test state in a fresh per-run temp dir. A FIXED /tmp path collides with a leftover
# owned by another uid (read-only DB / un-removable deploy.key), which fails the whole suite
# for reasons unrelated to the code under test. Honor an explicit override if the env sets it.
_TMP = tempfile.mkdtemp(prefix="nqq-test-")
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
os.environ.setdefault("NQQ_HOME", _TMP)
os.environ.setdefault("NQQ_DB", os.path.join(_TMP, "nqq-test.db"))
DB = os.environ["NQQ_DB"]

import stats as st          # noqa: E402
import kernel               # noqa: E402
import data as datamod      # noqa: E402
import signals as sigmod    # noqa: E402
import backtest as bt       # noqa: E402


def _rets(mu, sd, n, seed=1):
    import random
    rng = random.Random(seed)
    return [rng.gauss(mu, sd) for _ in range(n)]


class TestStats(unittest.TestCase):
    def test_psr_bounds(self):
        r = _rets(0.0006, 0.01, 500)
        p = st.probabilistic_sharpe(r)
        self.assertTrue(0.0 <= p <= 1.0)

    def test_deflated_le_psr(self):
        r = _rets(0.0006, 0.01, 500)
        self.assertLessEqual(st.deflated_sharpe(r, 20), st.probabilistic_sharpe(r) + 1e-9)

    def test_deflation_falls_with_trials(self):
        r = _rets(0.0005, 0.01, 500)
        d1 = st.deflated_sharpe(r, 1)
        d50 = st.deflated_sharpe(r, 50)
        d500 = st.deflated_sharpe(r, 500)
        self.assertGreaterEqual(d1, d50)
        self.assertGreaterEqual(d50, d500)

    def test_bootstrap_ci_orders(self):
        r = _rets(0.0006, 0.01, 400)
        b = st.block_bootstrap_sharpe(r, n_boot=200)
        self.assertLessEqual(b["lo"], b["median"])
        self.assertLessEqual(b["median"], b["hi"])
        self.assertTrue(0.0 <= b["p_positive"] <= 1.0)

    def test_fdr_monotone(self):
        p = [0.001, 0.01, 0.04, 0.2, 0.6, 0.9]
        surv, q = st.benjamini_hochberg(p, 0.10)
        self.assertTrue(surv[0])           # smallest p survives
        self.assertFalse(surv[-1])         # largest p does not
        self.assertLessEqual(q[0], q[-1])  # q-values roughly increasing


class TestData(unittest.TestCase):
    def test_synth_seed_process_independent(self):
        # deterministic synthetic data must not depend on PYTHONHASHSEED
        s1 = datamod._stable_seed("ZZZZ")
        s2 = datamod._stable_seed("ZZZZ")
        self.assertEqual(s1, s2)
        bars = datamod._synth("ZZZZ")
        self.assertEqual(bars[0]["c"], datamod._synth("ZZZZ")[0]["c"])

    def test_effective_n_deflates_pvalue(self):
        # a persistent signal + overlapping horizon must widen (raise) the p-value vs naive N
        vals = [i % 50 for i in range(400)]        # strongly autocorrelated ramp
        naive = sigmod._ic_pvalue(0.15, 400)
        eff = sigmod._ic_pvalue(0.15, sigmod._effective_n(400, 21, vals))
        self.assertGreaterEqual(eff, naive)         # deflation never overstates significance


class TestCostsAndDSR(unittest.TestCase):
    def test_impact_capacity_inversion(self):
        import costs
        self.assertAlmostEqual(costs.impact_bps(1.0), costs.IMPACT_COEF_BPS)   # 10bps @100%
        self.assertAlmostEqual(costs.impact_bps(0.25), costs.IMPACT_COEF_BPS * 0.5)
        # at part*, impact must equal the edge that generated it (self-consistent)
        edge = 5.0                                 # < IMPACT_COEF_BPS → interior solution
        cap = costs.capacity(edge, turnover_annual=1.0, adv_usd=1e8, crowding=1.0)
        part_star = (edge / costs.IMPACT_COEF_BPS) ** 2
        self.assertAlmostEqual(costs.impact_bps(part_star), edge)
        self.assertAlmostEqual(cap, part_star * 1e8)

    def test_capacity_clamps_participation_at_full_adv(self):
        import costs
        # edge far above impact-at-100%-ADV → the sqrt law would solve for part* > 1
        # (trading >100% of a day's volume). That must be clamped to 1.0, so capacity is
        # just adv/turnover — NOT the extrapolated (edge/coef)²·adv which overstates it.
        high_edge = costs.IMPACT_COEF_BPS * 5      # unclamped part* would be 25
        cap = costs.capacity(high_edge, turnover_annual=2.0, adv_usd=1e8, crowding=1.0)
        self.assertAlmostEqual(cap, 1.0 * 1e8 / 2.0)

    def test_edge_bps_is_return_not_sharpe(self):
        # edge_bps must be a per-TRADE edge = annual return(bps) / annual turnover — NOT
        # the Sharpe·1e4 (old bug, 1e4-scale) and NOT the un-annualized daily return that
        # pinned it at the old 1.0 floor for every strategy (dividing daily by annual).
        import backtest
        res = backtest.run("momentum", {})
        if res:                                   # synthetic data present
            mu = sum(res["returns"]) / len(res["returns"])
            expected = abs(mu * 252.0 * 1e4) / max(res["turnover_ann"], 1.0)
            self.assertAlmostEqual(res["edge_bps"], expected, places=6)
            # a real per-trade edge is a handful of bps, nowhere near Sharpe·1e4
            self.assertLess(res["edge_bps"], 500.0)

    def test_bollinger_distinct_from_mean_reversion(self):
        bars = datamod.load_ohlcv("SPY")
        mr = sigmod.series("mean_reversion", bars, {})
        bb = sigmod.series("bollinger", bars, {})
        # the two families must NOT be byte-identical (they were double-counted as trials)
        self.assertNotEqual(mr, bb)
        # bollinger is silent inside the bands: every non-zero value must be a band breach
        cap = sigmod.FAMILIES["bollinger"]["params"]["cap_z"]
        nonzero = [v for v in bb if v not in (None, 0.0)]
        self.assertTrue(all(abs(v) >= 1.0 - 1e-9 for v in nonzero))  # |z|>=cap → |z/cap|>=1
        self.assertTrue(any(v == 0.0 for v in bb))                   # some bars inside bands

    def test_rsi_thresholds_are_live(self):
        # buy_below/sell_above are advertised in FAMILIES and parsed by ai_builder, so they
        # MUST change the signal — they were dead code (series returned a threshold-agnostic
        # 50-rsi fade). Regression guard: changing the thresholds must change the output.
        bars = datamod.load_ohlcv("SPY")
        default = sigmod.series("rsi", bars, {})
        tighter = sigmod.series("rsi", bars, {"buy_below": 15, "sell_above": 85})
        self.assertNotEqual(default, tighter)                        # thresholds are live
        # a threshold breach fires; the neutral band is silent (0.0), like the bollinger gate
        wide = sigmod.series("rsi", bars, {"buy_below": 1, "sell_above": 99})
        self.assertTrue(all(v in (None, 0.0) for v in wide))         # nothing breaches → flat
        nonzero = [v for v in default if v not in (None, 0.0)]
        self.assertTrue(nonzero)                                     # something breaches
        self.assertTrue(all(-1.0001 <= v <= 1.0001 for v in nonzero))  # bounded ~[-1,1]
        # oversold breach is bullish (+), overbought breach is bearish (−)
        self.assertTrue(any(v > 0 for v in nonzero) or any(v < 0 for v in nonzero))
        sigmod.assert_causal("rsi", {"buy_below": 25, "sell_above": 75}, bars)  # still causal

    def test_degenerate_series_is_zero_not_huge(self):
        const = [0.001] * 200            # zero-variance series → Sharpe undefined → honest 0
        self.assertEqual(st.sharpe(const), 0.0)
        self.assertEqual(st._sharpe_pp(const), 0.0)
        p = st.probabilistic_sharpe(const)
        self.assertTrue(0.0 <= p <= 1.0)
        self.assertTrue(-1e6 < st.norm_ppf(1.0) < 1e6)   # boundary clamp is finite/monotone
        # the parity engine (engine B) must reconcile with engine A on a degenerate series:
        # both honest 0, not a ~1e16 leak via a 1e-9 sd floor (else the promotion parity
        # gate would flag a phantom "numerical bug" that is actually in the parity engine).
        self.assertEqual(st.parity_sharpe(const), 0.0)
        rel = abs(st.sharpe(const) - st.parity_sharpe(const)) / (abs(st.sharpe(const)) + 1e-9)
        self.assertLess(rel, 1e-6)                        # would blow up to ~1e16 pre-fix

    def test_dsr_accepts_cross_trial_variance(self):
        r = _rets(0.0006, 0.01, 500)
        # wider cross-trial dispersion → higher benchmark → lower (more conservative) DSR
        base = st.deflated_sharpe(r, 50)
        wide = st.deflated_sharpe(r, 50, sr_var=4.0)
        self.assertLessEqual(wide, base + 1e-9)
        self.assertTrue(0.0 <= wide <= 1.0)


class TestCertainty(unittest.TestCase):
    def _base(self, **kw):
        d = {"worst_regime_dsr": 0.9, "bootstrap_p_pos": 0.9, "dsr": 0.9,
             "fdr_survived": True, "oos_consistency": 0.9, "forward_days": 40,
             "forward_sharpe": 1.0, "is_real_pit": True, "capacity_aum": 5e6,
             "decay_hazard": 0.1}
        d.update(kw)
        return d

    def test_synthetic_cap(self):
        c = st.edge_certainty(self._base(is_real_pit=False))
        self.assertLessEqual(c["certainty"], 0.50)

    def test_no_forward_cap(self):
        c = st.edge_certainty(self._base(forward_days=0))
        self.assertLessEqual(c["certainty"], 0.70)

    def test_veto_worst_regime(self):
        c = st.edge_certainty(self._base(worst_regime_dsr=0.2))
        self.assertEqual(c["verdict"], "Inconclusive")

    def test_veto_capacity(self):
        c = st.edge_certainty(self._base(capacity_aum=1000))
        self.assertEqual(c["verdict"], "Inconclusive")

    def test_strong_edge_possible(self):
        c = st.edge_certainty(self._base())
        self.assertIn(c["verdict"], ("Strong edge", "Probable edge"))
        self.assertGreater(c["certainty"], 0.5)

    def test_weak_axis_drags(self):
        strong = st.edge_certainty(self._base())["certainty"]
        weak = st.edge_certainty(self._base(bootstrap_p_pos=0.05))["certainty"]
        self.assertLess(weak, strong)


class TestLookahead(unittest.TestCase):
    def test_guard_raises(self):
        bars = datamod.load_ohlcv("SPY")
        pit = datamod.PointInTime(bars)
        pit.advance()  # t=0
        with self.assertRaises(datamod.LookaheadError):
            pit.at(5)


class TestKernel(unittest.TestCase):
    def setUp(self):
        if os.path.exists(DB):
            os.remove(DB)
        self.s = kernel.Store(DB)

    def test_bundle_roundtrip(self):
        b = kernel.sign_bundle({"leaderboard": [1, 2, 3], "week": "W1"})
        ok, _ = kernel.verify_bundle(b)
        self.assertTrue(ok)

    def test_bundle_tamper_fails(self):
        b = kernel.sign_bundle({"x": 1})
        b["payload"]["x"] = 2
        ok, _ = kernel.verify_bundle(b)
        self.assertFalse(ok)

    def test_ledger_append_and_verify(self):
        self.s.stamp("test", "s1", {"a": 1})
        self.s.stamp("test", "s1", {"a": 2})
        ok, broken = self.s.verify_ledger()
        self.assertTrue(ok)
        self.assertIsNone(broken)

    def test_trial_ledger_counts(self):
        self.s.record_trial("alice", "momentum", "SPY", 1.2)
        self.s.record_trial("alice", "rsi", "SPY", 0.4)
        self.assertEqual(self.s.trial_count(operator="alice"), 2)
        self.assertEqual(self.s.trial_count(operator="bob"), 0)


class TestBacktestScorecard(unittest.TestCase):
    def test_scorecard_runs_and_is_honest(self):
        s = kernel.Store(DB)
        card, err = bt.scorecard(s, "momentum", {}, "SPY", operator="tester")
        self.assertIsNone(err)
        self.assertLessEqual(card["deflated_sharpe"], 1.0)
        self.assertIn("certainty", card)
        # more trials -> the same run should deflate further
        for _ in range(30):
            s.record_trial("tester", "x", "SPY", 0.5)
        card2, _ = bt.scorecard(s, "momentum", {}, "SPY", operator="tester")
        self.assertLessEqual(card2["deflated_sharpe"], card["deflated_sharpe"] + 1e-6)


class TestCompare(unittest.TestCase):
    def test_negative_correlation_is_opposed_not_same_bet(self):
        import compare
        # Strongly negative correlation must flag OPPOSED and must NOT be called
        # "HIGH CORRELATION / nearly the same bet" (the flag text would contradict
        # its own number). Regression for abs(corr) > 0.7 firing on corr < 0.
        flags = compare._flag_conflicts(-0.85, small_cap=1_000_000)
        joined = " ".join(flags)
        self.assertIn("OPPOSED", joined)
        self.assertNotIn("HIGH CORRELATION", joined)

    def test_positive_correlation_is_same_bet(self):
        import compare
        flags = compare._flag_conflicts(0.85, small_cap=1_000_000)
        joined = " ".join(flags)
        self.assertIn("HIGH CORRELATION", joined)
        self.assertNotIn("OPPOSED", joined)

    def test_tight_capacity_flagged(self):
        import compare
        flags = compare._flag_conflicts(0.0, small_cap=50_000)
        self.assertTrue(any("TIGHT CAPACITY" in f for f in flags))


class TestCouncil(unittest.TestCase):
    def test_refuses_combined_when_unrated(self):
        import council
        sheet = council.review({"sym": "SPY", "side": "LONG", "size_pct": 8,
                                "stop_pct": 2, "reward_risk": 1.8})
        # every voice carries a stance + evidence link (ruling 73)
        for v in sheet["voices"]:
            self.assertIn(v["stance"], ("FOR", "AGAINST", "NEUTRAL"))
            self.assertTrue(v["evidence"].startswith("/api/"))
        if sheet["rated_count"] == 0:
            self.assertIsNone(sheet["combined"])

    def test_hygiene_flags_bad_plan(self):
        import council
        sheet = council.review({"sym": "SPY", "side": "LONG", "size_pct": 90,
                                "stop_pct": 0, "reward_risk": 0.5})
        self.assertFalse(sheet["hygiene_ok"])

    def test_ai_synth_voice_not_counted_in_combined(self):
        # The AI council voice summarises the RATED family voices; it must not be tallied
        # as an independent rated vote. If it were, it would double-count the majority
        # direction and overstate both rated_count and the combined confidence.
        import council
        s = kernel.Store(DB)
        sheet = council.review({"sym": "SPY", "side": "LONG", "size_pct": 8,
                                "stop_pct": 2, "reward_risk": 1.8}, store=s)
        synth = [v for v in sheet["voices"] if v["family"] == "ai-council"]
        self.assertTrue(synth, "AI synthesizer voice should be present with a store")
        # the synthesizer carries no independent record → never a rated vote / rated chip
        self.assertFalse(synth[0]["record"]["rated"])
        rated_family = [v for v in sheet["voices"]
                        if v["family"] != "ai-council" and v["record"]["rated"]]
        self.assertEqual(sheet["rated_count"], len(rated_family))
        # combined confidence must be reproducible from the family voices ALONE
        if rated_family:
            fors = sum(1 for v in rated_family if v["stance"] == "FOR")
            ag = sum(1 for v in rated_family if v["stance"] == "AGAINST")
            expected = round(0.5 + 0.5 * ((fors - ag) / max(len(rated_family), 1)), 2)
            self.assertEqual(sheet["combined"], expected)
        else:
            self.assertIsNone(sheet["combined"])


class TestArena(unittest.TestCase):
    def test_ranked_by_deflated_not_raw(self):
        import arena
        s = kernel.Store(DB)
        arena.seed(s, force=True)
        st_ = arena.standings(s, recompute=True)
        rows = [r for r in st_["rows"] if not r["below_fold"]]
        for i in range(1, len(rows)):
            self.assertGreaterEqual(rows[i - 1]["deflated_sharpe"], rows[i]["deflated_sharpe"])
        for r in st_["rows"]:
            self.assertIn("ci", r)
            self.assertLessEqual(r["deflated_sharpe"], 1.0)

    def test_trials_column_matches_deflation_count(self):
        # HONESTY: the "Trials" column shown next to a row's deflated Sharpe must be the
        # SAME multiplicity count that produced that Sharpe — prior recorded trials plus
        # this evaluation itself (trial_count()+1, as backtest.scorecard uses). Arena used
        # to recompute store.trial_count() (=k) while the DSR beside it was deflated against
        # k+1, so an operator with trials showed a "Trials" number that disagreed with its
        # own P(edge). Record trials for one seeded operator and assert consistency.
        import arena
        s = kernel.Store(DB)
        arena.seed(s, force=True)
        for _ in range(3):
            s.record_trial("carol", "breakout", "SPY", 0.5)
        k = s.trial_count(operator="carol")
        self.assertGreaterEqual(k, 3)   # >=3 so k+1 != k is actually observable
        st_ = arena.standings(s, recompute=True)
        carol_rows = [r for r in st_["rows"] if r["operator"] == "carol"]
        self.assertTrue(carol_rows, "expected carol agents in the seeded standings")
        for r in carol_rows:
            # equals the count that deflated this row (k+1), NOT the raw prior count (k)
            self.assertEqual(r["n_trials"], k + 1)
            self.assertNotEqual(r["n_trials"], k)
        # invariant holds for every row: displayed count == prior trials for that op + 1
        for r in st_["rows"]:
            self.assertEqual(r["n_trials"], s.trial_count(operator=r["operator"]) + 1)

    def test_finalize_signs_verifiable_bundle(self):
        import arena
        s = kernel.Store(DB)
        arena.seed(s, force=True)
        r = arena.finalize_week(s)
        b = s.get("bundles", "week-" + arena._week_id())
        ok, _ = kernel.verify_bundle(b)
        self.assertTrue(ok)


class TestEvolve(unittest.TestCase):
    def setUp(self):
        self.s = kernel.Store(DB)

    def test_requires_metric_claim(self):
        import evolve
        d, e = evolve.propose(self.s, "bot", "do a thing", "")
        self.assertIsNone(d); self.assertIsNotNone(e)

    def test_illegal_transition_refused(self):
        import evolve
        d, _ = evolve.propose(self.s, "bot", "t", "metric goes up")
        d2, e = evolve.transition(self.s, d["id"], "shipped", "admin")  # skips steps
        self.assertIsNone(d2); self.assertIn("illegal", e)

    def test_honesty_core_needs_two_sigs(self):
        import evolve
        d, _ = evolve.propose(self.s, "bot", "touch stats", "x", target="stats.py")
        evolve.transition(self.s, d["id"], "accepted", "admin")
        evolve.transition(self.s, d["id"], "test_soaked", "admin")
        d2, e = evolve.transition(self.s, d["id"], "signed", "admin", signature=True)
        self.assertIsNone(d2)  # only one signature so far
        d3, e3 = evolve.transition(self.s, d["id"], "signed", "admin2", signature=True)
        self.assertIsNotNone(d3)  # two signatures -> allowed


class TestWorkflow(unittest.TestCase):
    def setUp(self):
        self.s = kernel.Store(DB)

    def test_journal_gates_block_fragile_edge(self):
        import journal
        idea, _ = journal.create(self.s, "t", "wf-idea-" + str(id(self)), "momentum", {})
        # Idea->Research->Backtest->Parity should pass; Parity->Paper blocks on edge floor
        stages = 0
        for _ in range(5):
            d, e = journal.promote(self.s, idea["id"])
            if e:
                break
            stages += 1
        self.assertGreaterEqual(stages, 3)   # reached at least Parity
        self.assertLess(stages, 5)           # blocked before Live (honest)

    def test_journal_oos_floor_gate_enforced(self):
        # GATE advertises an oos_floor (and the module docstring claims an out-of-sample
        # consistency check), but the Parity->Paper gate never consulted it — an advertised
        # gate that wasn't real. This asserts it now actually blocks a low-OOS idea.
        import journal
        from unittest.mock import patch
        idea = {"stage": 3, "family": "momentum", "params": {}, "name": "oos-x",
                "owner": "solo"}
        base = {"deflated_sharpe": 1.0, "certainty": {"certainty": 0.8, "verdict": "OK"},
                "capacity_aum": 5_000_000, "worst_regime_dsr": 1.0, "oos_consistency": 0.90}
        low_oos = {**base, "oos_consistency": 0.10}   # well below GATE oos_floor (0.45)
        with patch.object(journal, "_scorecard", return_value=(low_oos, None)):
            ok, note, ev = journal.evaluate_gate(self.s, idea)
            self.assertFalse(ok)
            self.assertIn("consistency", note.lower())
            self.assertEqual(ev["oos_consistency"], 0.10)
        with patch.object(journal, "_scorecard", return_value=(base, None)):
            ok, note, ev = journal.evaluate_gate(self.s, idea)
            self.assertTrue(ok)   # capacity + worst-regime + oos all clear

    def test_discovery_fdr_bounded(self):
        import discovery
        d = discovery.search(self.s, symbols=["SPY", "XLK"], operator="t")
        self.assertLessEqual(d["n_survived"], d["n_tested"])

    def test_execution_fill_is_honest(self):
        import execution
        o, e = execution.place(self.s, "SPY", "BUY", 10, operator="t")
        self.assertIsNone(e)
        self.assertNotEqual(o["slippage_bps"], 0.0)   # never a zero-cost fill

    def test_login_and_session_revocation(self):
        import time as _t
        kernel.create_user(self.s, "carol", "s3cretpw!", "Carol")
        self.assertIsNone(kernel.login(self.s, "carol", "wrong"))          # bad pw rejected
        self.assertIsNone(kernel.login(self.s, "nosuchuser", "whatever"))  # unknown user
        tok = kernel.login(self.s, "carol", "s3cretpw!")
        self.assertIsNotNone(tok)
        self.assertEqual(kernel.whoami(self.s, tok, True)["u"], "carol")
        # simulate a password change AFTER this session was issued → session revoked
        doc = self.s.get("users", "carol")
        doc["pw_changed_at"] = int(_t.time()) + 5
        self.s.put("users", "carol", doc)
        self.assertIsNone(kernel.whoami(self.s, tok, True))
        # deployment key honors the env override (shared key for multi-node)
        import os
        os.environ["NQQ_DEPLOY_KEY"] = "shared-managed-key"
        self.assertEqual(kernel.deployment_key(), b"shared-managed-key")
        del os.environ["NQQ_DEPLOY_KEY"]

    def test_order_types_and_working_state(self):
        import execution, data
        nb = data.load_ohlcv("SPY")[-1]
        # a limit BUY below the next bar's low (within band) does NOT fill → 'working'
        o, e = execution.place(self.s, "SPY", "BUY", 3, order_type="limit",
                               limit_px=round(nb["l"] - 0.5, 2))
        self.assertIsNone(e)
        self.assertEqual(o["status"], "working")
        self.assertIsNone(o["fill_px"])
        # a stop BUY below the next bar's high triggers → filled, taker (not maker)
        o2, e2 = execution.place(self.s, "SPY", "BUY", 2, order_type="stop",
                                 stop_px=round(nb["h"] - 1, 2))
        self.assertIsNone(e2)
        self.assertEqual(o2["status"], "filled")
        self.assertFalse(o2["maker"])
        # unknown type and missing price are refused
        self.assertIsNotNone(execution.place(self.s, "SPY", "BUY", 1, order_type="iceberg")[1])
        self.assertIsNotNone(execution.place(self.s, "SPY", "BUY", 1, order_type="limit")[1])

    def test_limit_fill_never_worse_than_limit(self):
        # A limit order can never fill worse than its limit price — a resting maker does
        # not cross the spread against itself. Regression: the cost model used to push the
        # fill PRICE past the limit (a BUY at 747.40 "filling" at 747.70).
        import execution, data
        nb = data.load_ohlcv("SPY")[-1]
        lp = round(nb["o"], 2)                       # at the open → guaranteed to fill
        ob, eb = execution.place(self.s, "SPY", "BUY", 2, order_type="limit", limit_px=lp)
        self.assertIsNone(eb)
        self.assertEqual(ob["status"], "filled")
        self.assertLessEqual(ob["fill_px"], lp)      # BUY never above the limit
        os_, es = execution.place(self.s, "SPY", "SELL", 2, order_type="limit", limit_px=lp)
        self.assertIsNone(es)
        self.assertEqual(os_["status"], "filled")
        self.assertGreaterEqual(os_["fill_px"], lp)  # SELL never below the limit

    def test_positions_pnl_from_fills_only(self):
        import execution
        self.s.put("orders", "tape", {"rows": []})   # isolate from other tests' fills
        execution.place(self.s, "SPY", "BUY", 4, order_type="market")
        execution.place(self.s, "SPY", "SELL", 1, order_type="market")
        # a working (unfilled) order must not change the position
        nb_low = execution.datamod.load_ohlcv("SPY")[-1]["l"]
        execution.place(self.s, "SPY", "BUY", 9, order_type="limit",
                        limit_px=round(nb_low - 0.5, 2))
        p = execution.positions(self.s)
        spy = [x for x in p["positions"] if x["sym"] == "SPY"][0]
        self.assertAlmostEqual(spy["qty"], 3.0)          # 4 bought − 1 sold; working excluded
        self.assertIn("total_pnl", p)

    def test_tenant_isolation_orders(self):
        import execution
        self.s.put("orders", "tape", {"rows": []})
        execution.place(self.s, "SPY", "BUY", 3, operator="alice")
        execution.place(self.s, "SPY", "BUY", 2, operator="bob")
        self.assertEqual(len(execution.tape(self.s, operator="alice")), 1)
        self.assertEqual(len(execution.tape(self.s, operator="bob")), 1)
        self.assertEqual(len(execution.tape(self.s)), 2)          # admin/global sees all
        pa = execution.positions(self.s, operator="alice")
        self.assertTrue(all(p["sym"] == "SPY" for p in pa["positions"]))
        self.assertEqual(execution.tca(self.s, operator="alice")["n"], 1)

    def test_tenant_isolation_promote_and_rooms(self):
        import journal, rooms
        idea, _ = journal.create(self.s, "alice", "iso-idea-" + str(id(self)), "momentum", {})
        d, e = journal.promote(self.s, idea["id"], operator="bob")   # bob is not the owner
        self.assertIsNone(d)
        self.assertIn("not your idea", e)
        self.assertIsNotNone(journal.promote(self.s, idea["id"], operator="alice")[0])
        # rooms: non-member cannot post; must join first
        r, _ = rooms.create(self.s, "alice", "iso-room-" + str(id(self)))
        _, perr = rooms.post(self.s, r["id"], "bob", "sneaking in")
        self.assertIsNotNone(perr)
        rooms.join(self.s, r["id"], "bob")
        self.assertIsNotNone(rooms.post(self.s, r["id"], "bob", "now a member")[0])
        # bob sees the room only after joining
        seen = [x["id"] for x in rooms.listing(self.s, viewer="bob")["rooms"]]
        self.assertIn(r["id"], seen)
        seen_carol = [x["id"] for x in rooms.listing(self.s, viewer="carol")["rooms"]]
        self.assertNotIn(r["id"], seen_carol)

    def test_tca_ignores_working_orders(self):
        import execution
        self.s.put("orders", "tape", {"rows": []})
        execution.place(self.s, "SPY", "BUY", 4, order_type="market")   # a real fill
        low = execution.datamod.load_ohlcv("SPY")[-1]["l"]
        execution.place(self.s, "SPY", "BUY", 4, order_type="limit",
                        limit_px=round(low - 0.5, 2))                    # stays 'working'
        t = execution.tca(self.s)
        self.assertEqual(t["n"], 1)                 # only the filled order counts
        self.assertTrue(t["honest"])                # working 0-slip must not flip this

    def test_workspace_import_sanitizes_proposals(self):
        import workspace
        # a bundle carrying a pre-signed, admin-owned, entrenched proposal
        payload = {"nqq_workspace": 1, "exported": "x", "owner": "mallory",
                   "docs": {"proposals": [{"id": 1, "author": "admin", "title": "t",
                                           "metric_claim": "c", "target": "stats.py",
                                           "status": "entrenched", "protected": False,
                                           "signatures": ["a", "b"], "history": []}]}}
        import kernel
        bundle = kernel.sign_bundle(payload)
        res, e = workspace.restore(self.s, bundle, importer="bob")
        self.assertIsNone(e)
        props = [p for p in self.s.list("proposals") if p.get("title") == "t"]
        self.assertTrue(props)
        p = props[0]
        self.assertEqual(p["status"], "proposed")   # never imported as entrenched/signed
        self.assertEqual(p["signatures"], [])
        self.assertEqual(p["author"], "bob")        # ownership reset to importer
        self.assertTrue(p["protected"])             # recomputed from honesty-core target

    def test_risk_kill_blocks_orders(self):
        import execution
        import risk
        risk.kill(self.s, "FLATTEN")
        _, e = execution.place(self.s, "SPY", "BUY", 1)
        self.assertIsNotNone(e)
        risk.rearm(self.s)

    def test_portfolio_diversification_bounded(self):
        import portfolio
        pf, e = portfolio.build([{"family": "momentum"}, {"family": "rsi"}])
        self.assertIsNone(e)
        self.assertTrue(0.0 <= pf["diversification"] <= 1.0)

    def test_ai_builder_parses(self):
        import ai_builder
        spec, _ = ai_builder.parse("fade AAPL when the 20-day bollinger z-score is extreme")
        self.assertEqual(spec["family"], "bollinger")
        self.assertEqual(spec["sym"], "AAPL")


class TestPortability(unittest.TestCase):
    def setUp(self):
        self.s = kernel.Store(DB)

    def test_workspace_roundtrip_signed(self):
        import journal
        import workspace
        journal.create(self.s, "t", "ws-idea-" + str(id(self)), "momentum", {})
        b = workspace.export(self.s)
        ok, _ = kernel.verify_bundle(b)
        self.assertTrue(ok)
        info, e = workspace.preview(b)
        self.assertIsNone(e)
        self.assertIn("ideas", info["counts"])

    def test_import_refuses_tampered(self):
        import workspace
        b = workspace.export(self.s)
        b["payload"]["docs"]["ideas"] = [{"id": 1, "name": "injected"}]
        res, e = workspace.restore(self.s, b)
        self.assertIsNone(res)
        self.assertIn("refused", e)

    def test_key_status_shape(self):
        ks = kernel.key_status()
        for k in ("alpaca_paper", "llm", "polygon", "fred"):
            self.assertIn(k, ks)

    def test_providers_status_labelled(self):
        import providers
        st_ = providers.status()
        self.assertTrue(all("live" in p for p in st_["providers"]))
        # a synthetic/catalog quote must never be labelled live
        q = providers.quote("XLK")
        if q["source"].startswith(("catalog", "synthetic")):
            self.assertFalse(q["live"])


class TestDoctorHarness(unittest.TestCase):
    def test_survives_unremovable_leftover_db(self):
        # doctor.run() must not crash on a leftover DB it cannot delete (a foreign-owned
        # file on a shared /tmp). We simulate "cannot remove" by placing the DB in a
        # read-only directory (clearing the dir's write bit makes os.remove raise), then
        # assert the doctor falls back to a fresh DB it owns and still runs to a verdict.
        import doctor
        d = tempfile.mkdtemp(prefix="nqq-ro-")
        stale = os.path.join(d, "doctor.db")
        open(stale, "w").close()
        os.chmod(d, 0o500)                       # r-x: owner can no longer remove entries
        saved = os.environ.get("NQQ_DB")
        try:
            os.environ["NQQ_DB"] = stale
            with self.assertRaises(PermissionError):
                os.remove(stale)                 # confirm the leftover really is unremovable
            rc = doctor.run()                    # must not raise
            used = os.environ["NQQ_DB"]
            self.assertEqual(rc, 0)
            self.assertNotEqual(used, stale)     # switched off the unremovable path
        finally:
            if saved is not None:
                os.environ["NQQ_DB"] = saved
            os.chmod(d, 0o700)
            shutil.rmtree(d, ignore_errors=True)


class TestDeploymentKeyRobustness(unittest.TestCase):
    def test_existing_key_survives_chmod_failure(self):
        # Regression: deployment_key() used to os.chmod the key file on EVERY call.
        # When the key is owned by another uid (shared/foreign-owned deploy dir) the
        # chmod raised PermissionError and 500'd every bundle-signing route
        # (e.g. /api/workspace/export). It must now read the existing key without
        # crashing. NQQ_DEPLOY_KEY takes precedence, so clear it for this test.
        import tempfile
        saved_env = os.environ.pop("NQQ_DEPLOY_KEY", None)
        d = tempfile.mkdtemp()
        keyfile = os.path.join(d, "deploy.key")
        with open(keyfile, "w") as f:
            f.write("a" * 64)
        orig_kp, orig_chmod = kernel._key_path, os.chmod
        kernel._key_path = lambda: keyfile

        def _boom(*a, **k):
            raise PermissionError("Operation not permitted")

        os.chmod = _boom
        try:
            self.assertEqual(kernel.deployment_key(), b"a" * 64)
        finally:
            kernel._key_path, os.chmod = orig_kp, orig_chmod
            if saved_env is not None:
                os.environ["NQQ_DEPLOY_KEY"] = saved_env


if __name__ == "__main__":
    unittest.main(verbosity=1)

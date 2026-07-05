from AlgorithmImports import *
import math

class MomoEtfV3(QCAlgorithm):

    # ---- parameters (chosen from the optimization plateau, not the peak) ----
    MOM_LOOKBACK   = 252   # 12 months
    MOM_SKIP       = 21    # skip most recent month ("12-1")
    REGIME_SMA     = 200
    VOL_WINDOW     = 20
    VOL_TARGET     = 0.10  # 10% annualized per position
    VOL_SCALE_CAP  = 1.5
    TOP_N          = 3
    ENTRY_CONF     = 0.50
    EXIT_CONF      = 0.25

    def Initialize(self):
        self.SetStartDate(2023, 1, 1)
        self.SetEndDate(2025, 12, 31)
        self.SetCash(100_000)
        self.SetBrokerageModel(BrokerageName.InteractiveBrokersBrokerage, AccountType.Margin)

        tickers = ["XLK","XLF","XLE","XLV","XLI","XLP","XLY","XLU","XLB"]
        self.etfs = [self.AddEquity(t, Resolution.Daily).Symbol for t in tickers]
        self.spy  = self.AddEquity("SPY", Resolution.Daily).Symbol

        # ---- indicators ----
        self.regime_sma = self.SMA(self.spy, self.REGIME_SMA, Resolution.Daily)
        # 12-1 momentum = MOMP(252) - MOMP(21): full-year % move minus last-month % move
        self.mom_long  = {s: self.MOMP(s, self.MOM_LOOKBACK, Resolution.Daily) for s in self.etfs}
        self.mom_short = {s: self.MOMP(s, self.MOM_SKIP,     Resolution.Daily) for s in self.etfs}
        self.vol       = {s: self.STD(s,  self.VOL_WINDOW,   Resolution.Daily) for s in self.etfs}

        # ---- HISTORY: warm-up must cover the LONGEST lookback ----
        # 252 trading days ≈ 365 calendar days; add buffer → 380. Getting this wrong
        # is the #1 cause of parity failures later (see Data & Artifacts screen).
        self.SetWarmUp(timedelta(days=380))

        # weekly rebalance, 30 min after open
        self.Schedule.On(self.DateRules.WeekStart(self.spy),
                         self.TimeRules.AfterMarketOpen(self.spy, 30),
                         self.Rebalance)

        # ---- custom charts: regime, confidence, exposure ----
        for name, series in [("Regime", "RiskOn"), ("Confidence", "MaxConf"), ("Exposure", "Gross")]:
            ch = Chart(name); ch.AddSeries(Series(series, SeriesType.Line, 0)); self.AddChart(ch)

        self.entered = set()   # symbols currently held (for hysteresis)

    # ---------- helpers ----------
    def momentum_12_1(self, s):
        return self.mom_long[s].Current.Value - self.mom_short[s].Current.Value

    def confidence(self, s, risk_on):
        if not risk_on:
            return 0.0
        z = self.momentum_12_1(s) / 8.0     # MOMP is in %, so 8.0 ≈ 8% normalizer
        return 1.0 / (1.0 + math.exp(-3.0 * z))

    def ann_vol(self, s):
        px = self.Securities[s].Price
        if px == 0 or not self.vol[s].IsReady:
            return 0.15
        return max((self.vol[s].Current.Value / px) * math.sqrt(252), 0.02)

    # ---------- the decision chain ----------
    def Rebalance(self):
        if self.IsWarmingUp or not self.regime_sma.IsReady:
            return

        # 1) REGIME FILTER
        risk_on = self.Securities[self.spy].Price > self.regime_sma.Current.Value
        self.Plot("Regime", "RiskOn", 1 if risk_on else 0)

        if not risk_on:
            self.Liquidate()                       # flat in risk-off — no exceptions
            self.entered.clear()
            self.Plot("Exposure", "Gross", 0)
            return

        # 2) TRIGGER: rank by 12-1 momentum
        ranked = sorted(self.etfs, key=self.momentum_12_1, reverse=True)
        top = set(ranked[:self.TOP_N])

        targets, max_conf = {}, 0.0
        for s in self.etfs:
            conf = self.confidence(s, risk_on)
            max_conf = max(max_conf, conf)
            in_pos = s in self.entered

            # 3) CONFIDENCE with hysteresis: enter ≥ 0.50, hold until < 0.25
            should_hold = (s in top) and (conf >= (self.EXIT_CONF if in_pos else self.ENTRY_CONF))

            if should_hold:
                # 4) POSITION SIZING: vol target × confidence
                scale  = min(self.VOL_TARGET / self.ann_vol(s), self.VOL_SCALE_CAP)
                targets[s] = conf * scale / self.TOP_N
                self.entered.add(s)
            else:
                targets[s] = 0.0
                self.entered.discard(s)

        # portfolio cap: gross ≤ 100%
        gross = sum(targets.values())
        if gross > 1.0:
            targets = {s: w / gross for s, w in targets.items()}

        # 5) ORDERS (LEAN's fee/slippage model applies; risk layer is Nautilus-side live)
        self.SetHoldings([PortfolioTarget(s, w) for s, w in targets.items()])
        self.Plot("Confidence", "MaxConf", max_conf)
        self.Plot("Exposure",  "Gross",   sum(targets.values()))

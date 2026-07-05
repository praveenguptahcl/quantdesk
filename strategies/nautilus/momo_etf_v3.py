import math
from collections import deque
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.trading.strategy import Strategy


class MomoEtfV3Config(StrategyConfig, frozen=True):
    etf_bar_types: list[str]      # e.g. ["XLK.XNAS-1-DAY-LAST-EXTERNAL", ...]
    spy_bar_type: str
    mom_lookback: int = 252
    mom_skip: int = 21
    regime_sma: int = 200
    vol_window: int = 20
    vol_target: float = 0.10
    vol_scale_cap: float = 1.5
    top_n: int = 3
    entry_conf: float = 0.50
    exit_conf: float = 0.25


class MomoEtfV3(Strategy):
    def __init__(self, config: MomoEtfV3Config):
        super().__init__(config)
        self.spy_bt  = BarType.from_str(config.spy_bar_type)
        self.etf_bts = [BarType.from_str(s) for s in config.etf_bar_types]
        n = config.mom_lookback + 5
        self.px   = {bt.instrument_id: deque(maxlen=n) for bt in self.etf_bts}
        self.spy_px = deque(maxlen=config.regime_sma + 5)
        self.entered: set[InstrumentId] = set()

    # ---------- lifecycle ----------
    def on_start(self):
        # HISTORY: request warm-up bars BEFORE live subscription — the parity-critical step.
        # Same 380 days LEAN warmed up with; indicators must be primed identically.
        for bt in [self.spy_bt, *self.etf_bts]:
            self.request_bars(bt)          # replays catalog/adapter history into on_historical_data
            self.subscribe_bars(bt)
        # weekly rebalance timer (Mon 10:00 ET expressed in your clock's tz handling)
        self.clock.set_timer(name="rebalance", interval=pd.Timedelta(days=7),
                             callback=self.on_rebalance_timer)

    def on_historical_data(self, data):      # warm-up replay lands here
        for bar in data:
            self._store(bar)

    def on_bar(self, bar: Bar):
        self._store(bar)

    def _store(self, bar: Bar):
        iid = bar.bar_type.instrument_id
        if bar.bar_type == self.spy_bt:
            self.spy_px.append(float(bar.close))
        elif iid in self.px:
            self.px[iid].append(float(bar.close))

    # ---------- identical decision chain ----------
    def _mom_12_1(self, iid):
        p = self.px[iid]
        if len(p) < self.config.mom_lookback:
            return None
        full  = p[-1] / p[-self.config.mom_lookback] - 1
        short = p[-1] / p[-self.config.mom_skip] - 1
        return (full - short) * 100.0          # percent, matching LEAN's MOMP

    def _confidence(self, iid, risk_on):
        if not risk_on:
            return 0.0
        m = self._mom_12_1(iid)
        if m is None:
            return 0.0
        return 1.0 / (1.0 + math.exp(-3.0 * (m / 8.0)))

    def _ann_vol(self, iid):
        p = list(self.px[iid])[-(self.config.vol_window + 1):]
        if len(p) < self.config.vol_window + 1:
            return 0.15
        rets = [p[i] / p[i - 1] - 1 for i in range(1, len(p))]
        mu = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets))
        return max(sd * math.sqrt(252), 0.02)

    def on_rebalance_timer(self, event):
        if len(self.spy_px) < self.config.regime_sma:
            return                                              # still warming up

        # 1) REGIME
        sma = sum(list(self.spy_px)[-self.config.regime_sma:]) / self.config.regime_sma
        risk_on = self.spy_px[-1] > sma
        if not risk_on:
            self.close_all_positions()                          # flat in risk-off
            self.entered.clear()
            return

        # 2) TRIGGER: rank
        moms = {iid: self._mom_12_1(iid) for iid in self.px}
        ranked = sorted((i for i, m in moms.items() if m is not None),
                        key=lambda i: moms[i], reverse=True)
        top = set(ranked[: self.config.top_n])

        equity = float(self.portfolio.account(self.etf_bts[0].instrument_id.venue)
                       .balance_total().as_double())

        targets = {}
        for iid in self.px:
            conf = self._confidence(iid, risk_on)
            in_pos = iid in self.entered
            threshold = self.config.exit_conf if in_pos else self.config.entry_conf
            if iid in top and conf >= threshold:
                scale = min(self.config.vol_target / self._ann_vol(iid),
                            self.config.vol_scale_cap)
                targets[iid] = conf * scale / self.config.top_n   # 3) + 4) conf × vol sizing
                self.entered.add(iid)
            else:
                targets[iid] = 0.0
                self.entered.discard(iid)

        gross = sum(targets.values())
        if gross > 1.0:
            targets = {i: w / gross for i, w in targets.items()}

        # 5) ORDERS: convert target weight → qty delta, submit market orders.
        for iid, w in targets.items():
            instrument = self.cache.instrument(iid)
            price = self.px[iid][-1]
            target_qty = int((equity * w) / price)
            current = self.portfolio.net_position(iid)
            delta = target_qty - int(current)
            if delta == 0:
                continue
            order = self.order_factory.market(
                instrument_id=iid,
                order_side=OrderSide.BUY if delta > 0 else OrderSide.SELL,
                quantity=instrument.make_qty(abs(delta)),
            )
            self.submit_order(order)   # Nautilus RiskEngine validates pre-route (Phase 7 layer)

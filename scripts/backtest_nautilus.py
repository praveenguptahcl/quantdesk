"""M5 runner sketch — see STRATEGY_GUIDE.md §4."""
# scripts/backtest_nautilus.py (sketch)
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import (BacktestRunConfig, BacktestVenueConfig,
                                    BacktestDataConfig, BacktestEngineConfig,
                                    ImportableStrategyConfig)

run = BacktestRunConfig(
    engine=BacktestEngineConfig(strategies=[ImportableStrategyConfig(
        strategy_path="strategies.nautilus.momo_etf_v3:MomoEtfV3",
        config_path="strategies.nautilus.momo_etf_v3:MomoEtfV3Config",
        config={"etf_bar_types": [...], "spy_bar_type": "SPY.XNAS-1-DAY-LAST-EXTERNAL"},
    )]),
    venues=[BacktestVenueConfig(name="XNAS", oms_type="NETTING",
            account_type="MARGIN", starting_balances=["100000 USD"])],
    data=[BacktestDataConfig(catalog_path="data/catalog", data_cls=Bar, ...)],
)
BacktestNode(configs=[run]).run()

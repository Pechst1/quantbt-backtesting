"""Published trend-following systems run as standalone portfolio simulators.

These systems need portfolio-level order logic (intraday stop entries, pyramiding,
unit limits across markets, monthly volatility-scaled rebalancing) that the
event engine does not model, so they keep their own accounting while reusing
the repo's data cleaning and performance metrics.
"""

from quantbt.systems.data import PricePanel, load_panel
from quantbt.systems.tsmom import TSMOM_UNIVERSE, TsmomConfig, run_tsmom
from quantbt.systems.turtle import TURTLE_UNIVERSE, TurtleConfig, run_turtle

__all__ = [
    "PricePanel",
    "TSMOM_UNIVERSE",
    "TURTLE_UNIVERSE",
    "TsmomConfig",
    "TurtleConfig",
    "load_panel",
    "run_tsmom",
    "run_turtle",
]

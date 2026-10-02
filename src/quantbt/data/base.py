from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd


class DataSource(ABC):
    name: str = "base"

    @abstractmethod
    def fetch_ohlcv(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> pd.DataFrame:
        """
        Returns OHLCV data with a DatetimeIndex and lowercase columns:
        open, high, low, close, adj_close, volume
        """


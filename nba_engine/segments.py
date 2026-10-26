"""Customer segments for the dashboards (assumption A25)."""
from __future__ import annotations

import pandas as pd

from nba_engine.config import SNAPSHOT
from nba_engine.loader import Dataset

SEGMENT_ORDER = ("High value", "Mid value", "Low value", "New customer")


def customer_segments(ds: Dataset) -> pd.Series:
    """CLV terciles (low/mid/high value); customers younger than one year are 'New customer'."""
    cv = ds.customer_value.set_index("customer_id")["clv_eur"]
    tiers = pd.qcut(cv.rank(method="first"), 3, labels=["Low value", "Mid value", "High value"]).astype(str)
    since = ds.customer_master.set_index("customer_id")["customer_since"]
    new = (since > SNAPSHOT - pd.Timedelta(days=365)).reindex(tiers.index, fill_value=False)
    return tiers.where(~new, "New customer")

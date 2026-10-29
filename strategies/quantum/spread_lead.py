"""T102 spread lead, frozen as in experiments/T10/SPEC.md rev 3.

Front move 08:59 -> 09:59 ET that the second month (CL.v.1) does not explain, traded 10:00 -> 14:29 on the
front. Needs bars from `load_period(..., with_second=True)` (`data.attach_second` columns).

Today's signal uses only bars up to the 09:59 close (`eligible`, causal). The engine adds the rest: the entry
must fill at 10:00 exactly (`max_fill_delay_min=1`) on the same contract, and no trade spans a roll.
A past day enters the rolling window (and the random-entry pool) only if it was also tradeable: an exact front
bar at 10:00 on the 09:59 contract (`final`). That bar is in the past when a later day uses it.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .published import flat_at_settle
from market_tester.common.sessions import ET
from market_tester.quantum.strategy import Signals, first_bar_at

START = 8 * 60 + 59
END = 9 * 60 + 59
ENTRY = 10 * 60


def _at(bars: pd.DataFrame, minute: int, columns: list[str]) -> pd.DataFrame:
    rows = bars.loc[bars["et_minute"].to_numpy() == minute, ["trading_day", *columns]]
    if rows["trading_day"].duplicated().any():
        raise ValueError(f"two bars at ET minute {minute} on one trading day")
    return rows.set_index("trading_day")


@dataclass
class SpreadLead:
    name: str = "spread_lead"
    requires_second = True  # class attribute (frozen with the source): report loads CL.v.1 columns
    sigma_filter = True  # T102: trade only when |r| >= sigma

    def day_table(self, bars: pd.DataFrame, window: int = 20, min_expiry_days: int = 5) -> pd.DataFrame:
        """One row per trading day with bars: eligible (causal, at 09:59), final (eligible and an exact 10:00
        front bar on the same contract), d0, d1, alpha, beta, sigma, r, side (0 = no trade)."""
        roll_so_far = bars["is_roll"].astype(bool).groupby(bars["trading_day"].to_numpy()).cummax().to_numpy()
        b = bars.assign(front_roll_so_far=roll_so_far)
        cols = ["close_ticks", "instrument_id", "c2_close_ticks", "c2_instrument_id", "c2_is_next"]
        a = _at(b, START, cols)
        e = _at(b, END, cols + ["expiration", "front_roll_so_far", "c2_roll_so_far"])
        f = _at(b, ENTRY, ["instrument_id"])
        days = pd.Index(pd.unique(b["trading_day"]), name="trading_day")
        a, e, f = a.reindex(days), e.reindex(days), f.reindex(days)
        expiry_date = pd.to_datetime(e["expiration"], utc=True).dt.tz_convert(ET).dt.tz_localize(None).dt.normalize()
        to_expiry = (expiry_date - days.to_series(index=days)).dt.days
        eligible = (a["close_ticks"].notna() & e["close_ticks"].notna()
                    & a["c2_close_ticks"].notna() & e["c2_close_ticks"].notna()
                    & (a["instrument_id"] == e["instrument_id"]) & (a["c2_instrument_id"] == e["c2_instrument_id"])
                    & a["c2_is_next"].eq(True) & e["c2_is_next"].eq(True)
                    & e["front_roll_so_far"].eq(False) & e["c2_roll_so_far"].eq(False)
                    & (to_expiry >= min_expiry_days).fillna(False))
        t = pd.DataFrame({"eligible": eligible.to_numpy(bool),
                          "final": (eligible & (f["instrument_id"] == e["instrument_id"])).to_numpy(bool),
                          "d0": (e["close_ticks"] - a["close_ticks"]).to_numpy(float),
                          "d1": (e["c2_close_ticks"] - a["c2_close_ticks"]).to_numpy(float)}, index=days)
        for c in ("alpha", "beta", "sigma", "r"):
            t[c] = np.nan
        t["side"] = 0

        elig, final = t["eligible"].to_numpy(), t["final"].to_numpy()
        d0, d1 = t["d0"].to_numpy(), t["d1"].to_numpy()
        out = {c: t[c].to_numpy().copy() for c in ("alpha", "beta", "sigma", "r", "side")}
        past = []  # final days strictly before today, in order
        for i in range(len(t)):
            if elig[i] and len(past) >= window:
                w = np.asarray(past[-window:])
                self._fit(i, w, d0, d1, window, out, self.sigma_filter)
            if final[i]:
                past.append(i)
        for c, v in out.items():
            t[c] = v
        return t

    @staticmethod
    def _fit(i, w, d0, d1, window, out, sigma_filter=True) -> None:
        """OLS with intercept of d0 on d1 over days `w`; today's residual r and side (0 if degenerate)."""
        x, y = d1[w], d0[w]
        xc = x - x.mean()
        sxx = (xc * xc).sum()
        if sxx <= 0:
            return  # constant d1: beta not identifiable
        beta = (xc * (y - y.mean())).sum() / sxx
        alpha = y.mean() - beta * x.mean()
        resid = y - alpha - beta * x
        sigma = np.sqrt((resid * resid).sum() / (window - 2))
        r = d0[i] - alpha - beta * d1[i]
        out["alpha"][i], out["beta"][i], out["sigma"][i], out["r"][i] = alpha, beta, sigma, r
        if np.isfinite(sigma) and sigma > 0 and r != 0 and (abs(r) >= sigma or not sigma_filter):
            out["side"][i] = int(np.sign(r))

    def eligible(self, bars: pd.DataFrame, **params) -> np.ndarray:
        """Per bar: its trading day is `final` (random-entry partners are drawn only among these days)."""
        t = self.day_table(bars, **params)
        return pd.Series(bars["trading_day"].to_numpy()).map(t["final"]).fillna(False).to_numpy(bool)

    def signals(self, bars: pd.DataFrame, window: int = 20, min_expiry_days: int = 5) -> Signals:
        t = self.day_table(bars, window, min_expiry_days)
        side_by_day = pd.Series(bars["trading_day"].to_numpy()).map(t["side"]).fillna(0).to_numpy(np.int64)
        at = bars["et_minute"].to_numpy() == END
        side = np.where(at, side_by_day, 0).astype(np.int64)
        return Signals(side, flat=flat_at_settle(bars), flat_day_end=True, max_fill_delay_min=1)


EXIT = 10 * 60 + 59


@dataclass
class SpreadLeadHour(SpreadLead):
    """T201 (experiments/T20/SPEC.md rev 2): T102's residual on every eligible day, held 10:00 -> 10:59 close.
    Days with a listed EIA release at 10:00..11:15 ET are not traded but stay in later rolling windows."""
    name: str = "spread_lead_hour"
    sigma_filter = False
    eia_window = (10 * 60, 11 * 60 + 15)  # loader adds `eia_during_hold` from the advance schedule

    def day_table(self, bars: pd.DataFrame, window: int = 20, min_expiry_days: int = 5) -> pd.DataFrame:
        t = super().day_table(bars, window, min_expiry_days)
        eia = pd.Series(bars["eia_during_hold"].to_numpy(bool), index=bars["trading_day"].to_numpy())
        eia = eia[~eia.index.duplicated()].reindex(t.index).to_numpy(bool)
        t["eia_during_hold"] = eia
        t["trade_eligible"] = t["eligible"].to_numpy() & ~eia
        t["trade_final"] = t["final"].to_numpy() & ~eia
        t.loc[eia, "side"] = 0
        return t

    def eligible(self, bars: pd.DataFrame, **params) -> np.ndarray:
        """Per bar: its trading day is `trade_final` (random-entry partners)."""
        t = self.day_table(bars, **params)
        return pd.Series(bars["trading_day"].to_numpy()).map(t["trade_final"]).fillna(False).to_numpy(bool)

    def signals(self, bars: pd.DataFrame, window: int = 20, min_expiry_days: int = 5) -> Signals:
        t = self.day_table(bars, window, min_expiry_days)
        side_by_day = pd.Series(bars["trading_day"].to_numpy()).map(t["side"]).fillna(0).to_numpy(np.int64)
        side = np.where(bars["et_minute"].to_numpy() == END, side_by_day, 0).astype(np.int64)
        return Signals(side, flat=first_bar_at(bars, EXIT), flat_day_end=True, max_fill_delay_min=1)

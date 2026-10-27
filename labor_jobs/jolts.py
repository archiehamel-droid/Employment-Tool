"""JOLTS (Job Openings and Labor Turnover Survey): monthly job openings by
industry and state, with a short-term forecast and a backtest of how
accurate that forecast would have been in the past.

Series IDs: JT + S (seasonally adjusted) + industry (6) + state (2) +
area (5) + size class (2) + data element (JO = job openings) + L (level, in
thousands). State estimates cover total nonfarm only and are published with a
longer lag than the national ones.
"""

import warnings

import numpy as np
import pandas as pd

from .states import STATES

INDUSTRIES = {
    "000000": "Total nonfarm",
    "100000": "Total private",
    "110099": "Mining and logging",
    "230000": "Construction",
    "300000": "Manufacturing",
    "420000": "Wholesale trade",
    "440000": "Retail trade",
    "480099": "Transportation, warehousing, and utilities",
    "510000": "Information",
    "510099": "Financial activities",
    "540099": "Professional and business services",
    "610000": "Private educational services",
    "620000": "Health care and social assistance",
    "710000": "Arts, entertainment, and recreation",
    "720000": "Accommodation and food services",
    "810000": "Other services",
    "900000": "Government",
}


def series_id(industry="000000", state_fips=0):
    return f"JTS{industry}{state_fips:02d}{'0' * 7}JOL"


def series_catalog():
    """Frame of (series_id, group, name, state) for every series used."""
    rows = [{"series_id": series_id(code), "group": "industry", "name": name, "state": None}
            for code, name in INDUSTRIES.items()]
    rows += [{"series_id": series_id(state_fips=fips), "group": "state", "name": name, "state": abbr}
             for abbr, name, fips in STATES]
    return pd.DataFrame(rows)


def _fit_forecast(values, horizon, alpha=0.2):
    """Damped-trend exponential smoothing on log openings. Returns
    (mean, lower, upper) arrays for the next `horizon` months; the interval
    covers 1 - alpha of outcomes if the model's assumptions hold."""
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = ETSModel(pd.Series(np.log(values)), error="add", trend="add",
                       damped_trend=True).fit(disp=False)
        frame = fit.get_prediction(start=len(values), end=len(values) + horizon - 1) \
                   .summary_frame(alpha=alpha)
    return (np.exp(frame["mean"].to_numpy()), np.exp(frame["pi_lower"].to_numpy()),
            np.exp(frame["pi_upper"].to_numpy()))


def forecast(history, horizon=6, min_months=36):
    """history: (series_id, month, value). Returns one row per series and
    forecast month."""
    rows = []
    for sid, grp in history.groupby("series_id"):
        grp = grp.sort_values("month")
        if len(grp) < min_months:
            continue
        mean, lower, upper = _fit_forecast(grp["value"].to_numpy(), horizon)
        last = pd.Period(grp["month"].iat[-1], "M")
        for h in range(horizon):
            rows.append({"series_id": sid, "last_month": str(last),
                         "last_value": grp["value"].iat[-1],
                         "month": str(last + h + 1), "horizon": h + 1,
                         "forecast": mean[h], "lower_80": lower[h], "upper_80": upper[h]})
    return pd.DataFrame(rows)


def backtest(history, horizon=6, origins=12, step=2, min_months=36):
    """Re-run the forecast as if standing at earlier months (the last
    `origins` x `step` months), and compare with what actually happened.
    Returns mean absolute percentage error per series and horizon, for the
    model and for a naive forecast that the latest value stays the same."""
    rows = []
    for sid, grp in history.groupby("series_id"):
        values = grp.sort_values("month")["value"].to_numpy()
        for k in range(origins):
            cut = len(values) - horizon - k * step
            if cut < min_months:
                break
            mean, _, _ = _fit_forecast(values[:cut], horizon)
            actual = values[cut:cut + horizon]
            for h in range(horizon):
                rows.append({"series_id": sid, "horizon": h + 1,
                             "model_ape": abs(mean[h] / actual[h] - 1) * 100,
                             "naive_ape": abs(values[cut - 1] / actual[h] - 1) * 100})
    if not rows:
        return pd.DataFrame(columns=["series_id", "horizon", "model_mape", "naive_mape", "tests"])
    return (pd.DataFrame(rows).groupby(["series_id", "horizon"])
            .agg(model_mape=("model_ape", "mean"), naive_mape=("naive_ape", "mean"),
                 tests=("model_ape", "size"))
            .round(2).reset_index())


def summarize(catalog, forecasts, errors, horizon):
    """One row per series: latest openings, forecast `horizon` months out,
    80% range, and backtest error at that horizon."""
    end = forecasts[forecasts["horizon"] == horizon]
    err = errors[errors["horizon"] == horizon][["series_id", "model_mape", "naive_mape", "tests"]]
    df = catalog.merge(end, on="series_id").merge(err, on="series_id", how="left")
    df["change_pct"] = ((df["forecast"] / df["last_value"] - 1) * 100).round(1)
    df["beats_naive"] = df["model_mape"] < df["naive_mape"]
    for col in ("last_value", "forecast", "lower_80", "upper_80"):
        df[col] = (df[col] * 1000).round()  # JOLTS levels are in thousands
    return df.drop(columns=["horizon"]).rename(columns={"month": "forecast_month"})

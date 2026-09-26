"""
Year-over-year change in per-delivery bowling variation (companion to dl_pro_fit.py).

"Variation" here is exactly the quantity dl_pro_fit.py already builds in
add_delivery_variation(): how far each delivery sits from the one before it on
the (length, line, speed) grid -- the 3-D delivery_distance, plus its three
components |delta length|, |delta line|, |delta speed|.

What this adds:
  1. variation_mean_by_year      mean per-delivery variation by year (+ cluster-robust CI)
  2. variation_year_change       first-year vs last-year difference, tested with
                                 standard errors clustered by match (balls within a match
                                 are not independent), plus a bowl_kind-adjusted version so a
                                 change in pace/spin mix isn't mistaken for a change in behaviour
  3. variation_slope_shift       did the *payoff* of variation (the regression of bowl_impact on
                                 the three |delta| terms, same standardization as
                                 regress_bowl_impact_on_variation) change between the two years?

Needs the frame returned by add_delivery_variation(..., include_speed=True), i.e.
`variation_df_speed` inside main().
"""
import os
import numpy as np
import pandas as pd
from scipy import stats

METRIC_LABELS = {
    "delivery_distance": "Distance (length, line, speed)",
    "abs_dlength": "|change in length|",
    "abs_dline": "|change in line|",
    "abs_dspeed": "|change in speed|",
}


def _prep(d):
    d = d.dropna(subset=["delivery_distance"]).copy()
    d["abs_dlength"] = d["delta_length"].abs()
    d["abs_dline"] = d["delta_line"].abs()
    if "delta_speed" in d.columns:
        d["abs_dspeed"] = d["delta_speed"].abs()
    return d


def _metrics_present(d):
    return [m for m in METRIC_LABELS if m in d.columns]


def _ols_cluster(X, y, groups):
    """OLS with match-clustered (CR1) standard errors. Returns (coef, se, n_clusters)."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    groups = np.asarray(groups)
    XtXi = np.linalg.inv(X.T @ X)
    b = XtXi @ X.T @ y
    e = y - X @ b
    meat = np.zeros((X.shape[1], X.shape[1]))
    uniq = np.unique(groups)
    for g in uniq:
        i = groups == g
        s = X[i].T @ e[i]
        meat += np.outer(s, s)
    G, (n, p) = len(uniq), X.shape
    V = XtXi @ meat @ XtXi * (G / (G - 1)) * ((n - 1) / (n - p))
    return b, np.sqrt(np.diag(V)), G


def variation_mean_by_year(d):
    d = _prep(d)
    rows = []
    for yr, sub in d.groupby("year"):
        for m in _metrics_present(d):
            b, se, _ = _ols_cluster(np.ones((len(sub), 1)), sub[m], sub["p_match"])
            rows.append({"year": int(yr), "metric": METRIC_LABELS[m], "n_balls": len(sub),
                         "mean": b[0], "se": se[0], "ci_low": b[0] - 1.96 * se[0], "ci_high": b[0] + 1.96 * se[0]})
    return pd.DataFrame(rows)


def variation_year_change(d, year_a=None, year_b=None):
    """Difference (year_b - year_a) in mean per-delivery variation; defaults to first vs last year present."""
    d = _prep(d)
    years = sorted(d["year"].dropna().unique())
    year_a = years[0] if year_a is None else year_a
    year_b = years[-1] if year_b is None else year_b
    dd = d[d["year"].isin([year_a, year_b])].copy()
    is_b = (dd["year"] == year_b).astype(float).to_numpy()
    g = dd["p_match"].to_numpy()

    rows = []
    for m in _metrics_present(dd):
        X = np.c_[np.ones(len(dd)), is_b]
        b, se, G = _ols_cluster(X, dd[m], g)
        t = b[1] / se[1]
        rows.append({"metric": METRIC_LABELS[m], "adjusted_for": "none",
                     f"mean_{int(year_a)}": b[0], f"mean_{int(year_b)}": b[0] + b[1],
                     "diff": b[1], "pct_change": 100 * b[1] / b[0], "se": se[1], "t": t,
                     "p_value": 2 * stats.t.sf(abs(t), G - 1)})

    if "bowl_kind" in dd.columns:
        kinds = pd.get_dummies(dd["bowl_kind"].fillna("unknown"), drop_first=True).astype(float)
        X = np.c_[np.ones(len(dd)), is_b, kinds.to_numpy()]
        b, se, G = _ols_cluster(X, dd["delivery_distance"], g)
        t = b[1] / se[1]
        rows.append({"metric": METRIC_LABELS["delivery_distance"], "adjusted_for": "bowl_kind (pace/spin mix)",
                     f"mean_{int(year_a)}": np.nan, f"mean_{int(year_b)}": np.nan,
                     "diff": b[1], "pct_change": np.nan, "se": se[1], "t": t,
                     "p_value": 2 * stats.t.sf(abs(t), G - 1)})
    return pd.DataFrame(rows)


def variation_by_kind(d):
    """Mean distance by year x bowl_kind, so the pace/spin mix effect is visible."""
    d = _prep(d)
    return (d.groupby(["bowl_kind", "year"])["delivery_distance"].agg(n_balls="size", mean_distance="mean")
            .reset_index())


def variation_slope_shift(d, year_a=None, year_b=None):
    """
    bowl_impact ~ z(|dlength|) + z(|dline|) + z(|dspeed|) + is_year_b + interactions.
    z-scoring is over the pooled two-year sample. The ':<year_b>' rows are the change in each slope
    from year_a to year_b; a large |t| means the payoff of that kind of variation genuinely shifted.
    """
    d = _prep(d).dropna(subset=["bowl_impact", "abs_dspeed"])
    years = sorted(d["year"].unique())
    year_a = years[0] if year_a is None else year_a
    year_b = years[-1] if year_b is None else year_b
    dd = d[d["year"].isin([year_a, year_b])].copy()
    cols = ["abs_dlength", "abs_dline", "abs_dspeed"]
    Z = ((dd[cols] - dd[cols].mean()) / dd[cols].std(ddof=0)).to_numpy()
    is_b = (dd["year"] == year_b).astype(float).to_numpy()[:, None]
    X = np.c_[np.ones(len(dd)), Z, is_b, Z * is_b]
    b, se, G = _ols_cluster(X, dd["bowl_impact"], dd["p_match"])
    names = (["intercept"] + [f"slope {c}" for c in cols] + [f"level shift ({int(year_b)})"]
             + [f"slope {c} : {int(year_b)}" for c in cols])
    out = pd.DataFrame({"term": names, "coef": b, "se": se, "t": b / se})
    out["p_value"] = 2 * stats.t.sf(out["t"].abs(), G - 1)
    return out


def plot_variation_mean_by_year(table, outpath):
    """plotnine: mean +/- 95% CI (clustered by match) per year, one panel per variation metric."""
    from plotnine import (ggplot, aes, geom_pointrange, facet_wrap, labs, theme, theme_bw)
    t = table.copy()
    t["year_lbl"] = t["year"].astype(int).astype(str)
    t["metric"] = pd.Categorical(t["metric"], categories=list(dict.fromkeys(t["metric"])), ordered=True)
    p = (ggplot(t, aes("year_lbl", "mean", ymin="ci_low", ymax="ci_high"))
         + geom_pointrange(color="#3366cc")
         + facet_wrap("~metric", nrow=1, scales="free_y")
         + labs(title="Average per-delivery bowling variation by year (95% CI, clustered by match)",
                x="Year", y="Mean per-delivery variation (grid steps)")
         + theme_bw() + theme(figure_size=(4 * t["metric"].nunique(), 4.5)))
    p.save(outpath, dpi=140, verbose=False)


def summarize_variation_change(variation_df_speed, outdir=None, plot_dir=None):
    """Print + save everything above. Returns a dict of tables."""
    means = variation_mean_by_year(variation_df_speed)
    change = variation_year_change(variation_df_speed)
    kind = variation_by_kind(variation_df_speed)
    slopes = variation_slope_shift(variation_df_speed)

    print("=== Average per-delivery variation by year (cluster-robust 95% CI) ===")
    print(means.round(4).to_string(index=False))
    print("\n=== Change in average variation, last year vs first year (SEs clustered by match) ===")
    print(change.round(4).to_string(index=False))
    print("\n=== Mean delivery_distance by bowl_kind x year ===")
    print(kind.round(4).to_string(index=False))
    print("\n=== Did the payoff of variation change? (bowl_impact slopes, year interaction) ===")
    print(slopes.round(4).to_string(index=False))
    print()

    out = {"means": means, "change": change, "by_kind": kind, "slope_shift": slopes}
    if outdir is not None:
        for name, tbl in out.items():
            tbl.to_csv(os.path.join(outdir, f"variation_change_{name}.csv"), index=False)
    if plot_dir is not None:
        plot_variation_mean_by_year(means, os.path.join(plot_dir, "28d_variation_mean_by_year.png"))
    return out

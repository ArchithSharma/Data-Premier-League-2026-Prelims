"""
DL Pro curve fitting + RAA/WAA/Impact for ball-by-ball T20 data, plus
physical-delivery-attribute breakdowns and plots.

Implements:
  - R_std(b, w)        = R0 * F(w) * [1 - exp(-b / beta(w))]
  - R_pro(b, w, lambda) = R0 * F(w) * lambda^(n_w+1) *
                          [1 - exp(-b / (beta(w) * lambda^n_w))]
    with n_w = n0 * F(w)   (Ganjoo, "T20 Metrics: A Primer", DL Pro Edition
    https://hganjoo.github.io/t20basics/#sec-dl)

  - Model selection for F(w) and beta(w) polynomial degree via AIC.
  - Per-match lambda by inverting R_pro(120, 0, lambda) = S1 (first-innings score).
  - RAA / WAA at the ball level using an empirical (over, wickets-down) baseline,
    separately for 1st and 2nd innings.
  - Impact: a DL-based ball-level runs-added metric (batting and bowling).
  - Physical-delivery-attribute breakdowns: how Impact/RAA/WAA relate to line,
    length, bowl speed, release point, bowl kind (pace/spin), shot type and
    shot control -- i.e. observable inputs, not just outcomes.
  - A set of diagnostic and leaderboard plots saved as PNGs (drawn with
    plotnine; all plotting code lives in dl_pro_plots.py).
  - Year-over-year change in per-delivery bowling variation (dl_pro_variation.py).
  - Impact Player substitution analysis (Section 8): ingesting raw Cricsheet
    match JSON, identifying Impact Player substitution events, and measuring
    the batting/bowling Impact of the substitute for the remainder of the
    match, split by year and by which innings the substitution happened in.

Files that must sit next to this one
------------------------------------
    dl_pro_plots.py       plotnine versions of every plot_* function
    dl_pro_variation.py   summarize_variation_change (2023 vs 2024 variation test)
Requires: numpy, pandas, scipy, plotnine.

No command-line arguments
--------------------------
Everything is controlled by the CONFIG dict just below `if __name__ ==
"__main__"` at the bottom of this file. Edit CONFIG and run:
    python dl_pro_fit.py

n0 handling
-----------
n0 is fixed at 1.04 (the paper's value) by default rather than fit from data.
On small samples, n0 is only identified by matches whose lambda deviates
meaningfully above 1, and the SSE surface over n0 tends to be extremely flat
in that regime (lambda^n -> 1 as lambda -> 1 regardless of n0), so a free fit
can land almost anywhere along a shallow plateau depending on noise. Set
CONFIG["fix_n0"] = None to fit n0 by SSE minimization instead, if you have
enough matches with lambda well above 1 to trust it -- sanity-check first
with check_n0_identification.py to confirm there's an actual interior minimum
rather than a plateau.

Rain/DLS handling
------------------
Any match in which EITHER innings was shortened (max_balls != 120) is dropped
before anything else runs (see `get_clean_matches`). This keeps every stage of
the pipeline -- curve fitting, the empirical baseline, lambda-fitting, and the
Impact metric -- working purely on unaffected, full-length games.

Batting vs. bowling Impact scale
---------------------------------
Wickets carry far more DL-modeled value per event (mean ~-8 runs to the
batter, ~+8 to the bowler in one sample dataset) than an average non-wicket
delivery (~+/-0.3 runs), simply because F(w) drops steeply as wickets fall.
That means specialist wicket-taking bowlers naturally accumulate larger raw
Impact totals than batters accumulating small per-ball contributions -- this
is a structural feature of any DL/resource-based Impact metric, not a bug.
Two things are still done about it here:
  1. `impact_clip` winsorizes single-ball impact so that the discontinuous
     "all out -> future runs forced to exactly 0" transition doesn't dump an
     outsized one-ball swing onto whoever happens to take an innings' final
     wicket (this DOES meaningfully change a few individual bowlers' totals;
     see the printed clipping report).
  2. `summarize_by_player` adds Impact-per-ball and within-pool percentile /
     z-score columns, so batters and bowlers can be compared on a normalized
     footing instead of by raw Impact sum.
"""

import difflib
import os
import re
import warnings
from itertools import product

import numpy as np
import pandas as pd
from scipy.optimize import brentq, curve_fit, minimize_scalar

# plotnine-based plotting (all plot_* functions live here) and the
# year-over-year variation test.
from dl_pro_plots import *
from dl_pro_variation import summarize_variation_change

warnings.filterwarnings("ignore")

MAX_BALLS_FULL = 120

# --------------------------------------------------------------------------
# 0. Rain/DLS filtering
# --------------------------------------------------------------------------

def get_clean_matches(df, max_balls_full=MAX_BALLS_FULL):
    """Matches where BOTH innings were played out at full length."""
    full_length = df.groupby("p_match")["max_balls"].apply(lambda s: (s == max_balls_full).all())
    return full_length[full_length].index


def report_dropped_matches(df, clean_matches):
    dropped = df[~df["p_match"].isin(clean_matches)]
    n_dropped = dropped["p_match"].nunique()
    n_total = df["p_match"].nunique()
    print(f"Dropped {n_dropped}/{n_total} rain/DLS-affected (or incomplete) matches; "
          f"{n_total - n_dropped} clean matches remain.")
    if n_dropped:
        reasons = (
            dropped.groupby("p_match")["max_balls"]
            .apply(lambda s: sorted(s.unique()))
            .value_counts()
        )
        print("Breakdown of dropped matches by observed max_balls value(s):")
        print(reasons.to_string())
    print()


# --------------------------------------------------------------------------
# 1. Data preparation
# --------------------------------------------------------------------------

def build_state_table(df, max_balls_full=MAX_BALLS_FULL):
    """
    Build the (b, w, y) table needed to fit R(b, w):
      b = balls remaining *after* the ball is bowled (inns_balls_rem)
      w = wickets down *after* the ball is bowled (inns_wkts)
      y = runs still to come from that state to the end of the innings
    """
    d = df.copy()
    d = d[d["max_balls"] == max_balls_full]
    d = d.dropna(subset=["inns_balls_rem", "inns_wkts", "inns_runs", "p_match", "inns"])

    final_runs = d.groupby(["p_match", "inns"])["inns_runs"].transform("max")
    d["y_runs_to_come"] = final_runs - d["inns_runs"]

    d["b"] = d["inns_balls_rem"].astype(float)
    d["w"] = d["inns_wkts"].astype(float)
    d = d[(d["w"] <= 9) & (d["b"] >= 0) & (d["b"] <= max_balls_full)]
    return d


def first_innings_scores(df, max_balls_full=MAX_BALLS_FULL):
    """S1 = full first-innings score for every (already-clean) match."""
    d = df[(df["inns"] == 1) & (df["max_balls"] == max_balls_full)]
    return d.groupby("p_match")["inns_runs"].max()


# --------------------------------------------------------------------------
# 2. DL Standard: R_std(b, w) = R0 * F(w) * [1 - exp(-b / beta(w))]
# --------------------------------------------------------------------------

def make_F(p_degree):
    """F(w) = 1 + a1*w + a2*w^2 + ... + a_p*w^p   (F(0) == 1 by construction)."""
    def F(w, *coefs):
        w = np.asarray(w, dtype=float)
        out = np.ones_like(w)
        for i, c in enumerate(coefs, start=1):
            out = out + c * w ** i
        return out
    return F, p_degree


def make_beta(q_degree):
    """beta(w) = b0 + b1*w + ... + b_q*w^q   (b0 is beta at w=0)."""
    def beta(w, *coefs):
        w = np.asarray(w, dtype=float)
        out = np.zeros_like(w)
        for i, c in enumerate(coefs):
            out = out + c * w ** i
        return out
    return beta, q_degree + 1


def build_r_std(F_func, n_F, beta_func, n_beta):
    def f(X, R0, *params):
        f_coefs = params[:n_F]
        b_coefs = params[n_F:n_F + n_beta]
        b, w = X
        Fw = F_func(w, *f_coefs)
        betaw = beta_func(w, *b_coefs)
        betaw = np.clip(betaw, 1e-3, None)
        return R0 * Fw * (1.0 - np.exp(-b / betaw))
    return f


def fit_r_std(data, p_degree, q_degree):
    F_func, n_F = make_F(p_degree)
    beta_func, n_beta = make_beta(q_degree)
    model = build_r_std(F_func, n_F, beta_func, n_beta)

    b = data["b"].to_numpy()
    w = data["w"].to_numpy()
    y = data["y_runs_to_come"].to_numpy()

    R0_0 = float(np.nanpercentile(y[w == 0], 90)) if (w == 0).any() else 180.0
    p0 = [R0_0] + [-0.08] + [0.0] * (n_F - 1) + [45.0] + [-3.0] + [0.0] * (n_beta - 2)
    p0 = p0[: 1 + n_F + n_beta]

    lower = [50.0] + [-5.0] * n_F + [1.0] + [-100.0] * (n_beta - 1)
    upper = [400.0] + [5.0] * n_F + [300.0] + [100.0] * (n_beta - 1)

    try:
        popt, _ = curve_fit(
            model, (b, w), y, p0=p0, bounds=(lower, upper),
            method="trf", loss="soft_l1", f_scale=10.0, max_nfev=40000,
        )
    except RuntimeError as e:
        return {"p_degree": p_degree, "q_degree": q_degree, "success": False, "error": str(e)}

    pred = model((b, w), *popt)
    resid = y - pred
    rss = float(np.sum(resid ** 2))
    n = len(y)
    k = len(popt) + 1
    aic = n * np.log(rss / n) + 2 * k

    return {
        "p_degree": p_degree, "q_degree": q_degree, "success": True,
        "R0": float(popt[0]),
        "F_coefs": popt[1:1 + n_F].tolist(),
        "beta_coefs": popt[1 + n_F:1 + n_F + n_beta].tolist(),
        "rss": rss, "n": n, "k": k, "aic": aic,
        "F_func": F_func, "beta_func": beta_func,
        "n_F": n_F, "n_beta": n_beta, "model": model, "popt": popt,
    }


def select_best_by_aic(data, p_degrees=(1, 2, 3, 4), q_degrees=(1, 2, 3)):
    results = []
    for p, q in product(p_degrees, q_degrees):
        results.append(fit_r_std(data, p, q))

    table = pd.DataFrame([
        {"F_degree": r["p_degree"], "beta_degree": r["q_degree"],
         "n_params": r.get("k", np.nan), "RSS": r.get("rss", np.nan),
         "AIC": r.get("aic", np.nan), "success": r["success"]}
        for r in results
    ]).sort_values("AIC").reset_index(drop=True)

    successful = [r for r in results if r["success"]]
    if not successful:
        raise RuntimeError("No (F degree, beta degree) combination converged.")
    best = min(successful, key=lambda r: r["aic"])
    return table, best, results


# --------------------------------------------------------------------------
# 3. DL Pro: R_pro(b, w, lambda), n_w = n0 * F(w)
# --------------------------------------------------------------------------

def r_pro(b, w, lam, R0, F_func, F_coefs, beta_func, beta_coefs, n0):
    b = np.asarray(b, dtype=float)
    w = np.asarray(w, dtype=float)
    Fw = F_func(w, *F_coefs)
    betaw = np.clip(beta_func(w, *beta_coefs), 1e-3, None)
    nw = n0 * Fw
    lam = np.asarray(lam, dtype=float)
    out = R0 * Fw * lam ** (nw + 1.0) * (
        1.0 - np.exp(-b / (betaw * lam ** nw))
    )
    # All-out (w >= 10): no batting resource left, no further runs possible.
    return np.where(w >= 10, 0.0, out)


def solve_lambda(S1, R0, F_func, F_coefs, beta_func, beta_coefs, n0, R_std_full):
    """lambda solving R_pro(120, 0, lambda) = S1.  lambda = 1 if S1 <= R_std(120,0)."""
    if S1 <= R_std_full:
        return 1.0

    def g(lam):
        return r_pro(120.0, 0.0, lam, R0, F_func, F_coefs, beta_func, beta_coefs, n0) - S1

    hi = 1.5
    while g(hi) < 0 and hi < 20:
        hi *= 1.5
    try:
        return float(brentq(g, 1.0, hi, xtol=1e-6))
    except ValueError:
        return 1.0


def fit_n0(data, matches_S1, best_std, n0_bounds=(0.1, 4.0)):
    """Choose n0 to minimize total SSE of R_pro vs actual runs-to-come."""
    R0 = best_std["R0"]
    F_func, F_coefs = best_std["F_func"], best_std["F_coefs"]
    beta_func, beta_coefs = best_std["beta_func"], best_std["beta_coefs"]
    R_std_full = R0 * F_func(np.array([0.0]), *F_coefs)[0] * (
        1 - np.exp(-120.0 / np.clip(beta_func(np.array([0.0]), *beta_coefs)[0], 1e-3, None))
    )

    matches = data["p_match"].to_numpy()
    b = data["b"].to_numpy()
    w = data["w"].to_numpy()
    y = data["y_runs_to_come"].to_numpy()
    unique_matches = matches_S1.index.to_numpy()

    def sse_for_n0(n0):
        lam_map = {
            m: solve_lambda(matches_S1.loc[m], R0, F_func, F_coefs, beta_func, beta_coefs, n0, R_std_full)
            for m in unique_matches
        }
        lam_arr = np.array([lam_map.get(m, 1.0) for m in matches])
        pred = r_pro(b, w, lam_arr, R0, F_func, F_coefs, beta_func, beta_coefs, n0)
        return float(np.sum((y - pred) ** 2))

    res = minimize_scalar(sse_for_n0, bounds=n0_bounds, method="bounded",
                           options={"xatol": 1e-3})
    n0_best = float(res.x)
    lam_map = {
        m: solve_lambda(matches_S1.loc[m], R0, F_func, F_coefs, beta_func, beta_coefs, n0_best, R_std_full)
        for m in unique_matches
    }
    return n0_best, lam_map, R_std_full


# --------------------------------------------------------------------------
# 4. RAA / WAA (empirical over x wickets baseline, computed separately by innings)
# --------------------------------------------------------------------------

def build_baseline(df, inns_no, min_count=15, max_balls_full=MAX_BALLS_FULL):
    d = df[(df["inns"] == inns_no) & (df["wide"] == 0) & (df["max_balls"] == max_balls_full)].copy()
    d["out"] = d["out"].astype(float)
    grp = d.groupby(["over_num", "inns_wkts"])
    runs_mean = grp["score"].mean()
    out_mean = grp["out"].mean()
    counts = grp.size()

    wkt_runs_fallback = d.groupby("inns_wkts")["score"].mean()
    wkt_out_fallback = d.groupby("inns_wkts")["out"].mean()

    base = pd.DataFrame({"runs_mean": runs_mean, "out_mean": out_mean, "n": counts}).reset_index()
    sparse = base["n"] < min_count
    base.loc[sparse, "runs_mean"] = base.loc[sparse, "inns_wkts"].map(wkt_runs_fallback)
    base.loc[sparse, "out_mean"] = base.loc[sparse, "inns_wkts"].map(wkt_out_fallback)
    return base.set_index(["over_num", "inns_wkts"])[["runs_mean", "out_mean"]]


def compute_raa_waa(df):
    d = df[df["wide"] == 0].copy()
    baselines = {inns_no: build_baseline(df, inns_no) for inns_no in (1, 2)}

    def lookup(row, col):
        b = baselines.get(row["inns"])
        if b is None:
            return np.nan
        key = (row["over_num"], row["inns_wkts"])
        return b[col].get(key, np.nan)

    d["exp_runs"] = d.apply(lambda r: lookup(r, "runs_mean"), axis=1)
    d["exp_wkt"] = d.apply(lambda r: lookup(r, "out_mean"), axis=1)
    d["RAA"] = d["score"] - d["exp_runs"]
    d["WAA"] = d["exp_wkt"] - d["out"].astype(float)

    d["bowl_RAA"] = -d["RAA"]
    d["bowl_WAA"] = -d["WAA"]
    return d, baselines


def summarize_by_player(d, player_col, raa_col, waa_col, impact_col=None, min_balls=100):
    """
    Per-player summary. When impact_col is given, also adds:
      - impact_per_ball : Impact normalized by opportunities faced/bowled,
        so specialists with fewer deliveries aren't only compared on volume.
      - Impact_pctile   : percentile rank *within this player pool*
        (batters ranked among batters, bowlers among bowlers).
      - Impact_z        : z-score within this player pool.
    Comparing Impact_pctile or Impact_z across the batting and bowling tables
    is the fairer way to ask "who overperformed most in their role", since
    raw Impact totals aren't on a comparable scale between the two (see the
    module docstring).
    """
    agg = {"balls": (raa_col, "size"), "runs": ("score", "sum"),
           "RAA": (raa_col, "sum"), "WAA": (waa_col, "sum")}
    if impact_col is not None:
        agg["Impact"] = (impact_col, "sum")
    out = (
        d.groupby(player_col)
        .agg(**agg)
        .query("balls >= @min_balls")
        .sort_values("Impact" if impact_col is not None else "RAA", ascending=False)
    )
    out["RAA_per_ball"] = out["RAA"] / out["balls"]
    if impact_col is not None:
        out["impact_per_ball"] = out["Impact"] / out["balls"]
        out["Impact_pctile"] = out["Impact"].rank(pct=True) * 100
        out["Impact_z"] = (out["Impact"] - out["Impact"].mean()) / out["Impact"].std(ddof=0)
    return out


# --------------------------------------------------------------------------
# 4c. Season-level analysis (per-player, per-year splits and year-over-year
#     "biggest movers" -- e.g. surfacing a strong 2023 followed by a quiet
#     2024 automatically instead of eyeballing career totals).
# --------------------------------------------------------------------------

def summarize_by_player_season(d, player_col, raa_col, waa_col, impact_col=None, min_balls=30):
    """
    Same idea as summarize_by_player, but split by year -- one row per
    (player, year). Use a lower min_balls than the career table by default,
    since a single season has far fewer deliveries than a full career.
    """
    agg = {"balls": (raa_col, "size"), "runs": ("score", "sum"),
           "RAA": (raa_col, "sum"), "WAA": (waa_col, "sum")}
    if impact_col is not None:
        agg["Impact"] = (impact_col, "sum")
    out = (
        d.groupby([player_col, "year"])
        .agg(**agg)
        .reset_index()
        .query("balls >= @min_balls")
    )
    out["RAA_per_ball"] = out["RAA"] / out["balls"]
    if impact_col is not None:
        out["impact_per_ball"] = out["Impact"] / out["balls"]
    return out.sort_values([player_col, "year"])


def season_over_season_changes(season_summary, player_col, value_col="impact_per_ball"):
    """
    For each player, one row per consecutive pair of seasons they qualified
    in (>= min_balls both seasons), with the change in value_col between
    them. Sorted biggest riser first -- this is how you'd surface something
    like "great 2023, quiet 2024" automatically instead of eyeballing the
    season table by hand.
    """
    rows = []
    for player, g in season_summary.sort_values("year").groupby(player_col):
        g = g.set_index("year")
        years = list(g.index)
        for y0, y1 in zip(years[:-1], years[1:]):
            rows.append({
                player_col: player,
                "year_from": y0, "year_to": y1,
                "value_from": g.loc[y0, value_col], "value_to": g.loc[y1, value_col],
                "change": g.loc[y1, value_col] - g.loc[y0, value_col],
                "balls_from": g.loc[y0, "balls"], "balls_to": g.loc[y1, "balls"],
            })
    if not rows:
        return pd.DataFrame(columns=[player_col, "year_from", "year_to", "value_from",
                                      "value_to", "change", "balls_from", "balls_to"])
    return pd.DataFrame(rows).sort_values("change", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------
# 4b. Impact: DL-based runs-added metric for a ball, from the batter's POV
#     I_t = s_t + R_pro(b_after, w_after, lambda) - R_pro(b_before, w_before, lambda)
#     Bowling Impact is the same quantity with the sign flipped.
#
#     impact_clip winsorizes the single-ball value. This mainly targets the
#     "all out" discontinuity: when the 10th wicket falls, R_pro forces future
#     runs to exactly 0 regardless of balls remaining, which can dump an
#     outsized one-ball swing (tens of runs) onto whoever takes that final
#     wicket. Clipping caps that without changing the ordinary (and legitimate,
#     model-implied) cost of a regular wicket.
# --------------------------------------------------------------------------

def compute_impact(df, best_std, n0, lam_map, impact_clip=None):
    R0 = best_std["R0"]
    F_func, F_coefs = best_std["F_func"], best_std["F_coefs"]
    beta_func, beta_coefs = best_std["beta_func"], best_std["beta_coefs"]

    d = df[df["wide"] == 0].copy()
    d = d.dropna(subset=["inns_balls_rem", "inns_wkts", "p_match"])
    d["lambda"] = d["p_match"].map(lam_map).fillna(1.0)

    legal_ball = (d["noball"] == 0).astype(int)
    out_flag = d["out"].astype(int)

    b_after = d["inns_balls_rem"].astype(float)
    w_after = d["inns_wkts"].astype(float)
    b_before = b_after + legal_ball
    w_before = w_after - out_flag

    lam = d["lambda"].to_numpy()
    R_before = r_pro(b_before.to_numpy(), w_before.to_numpy(), lam,
                      R0, F_func, F_coefs, beta_func, beta_coefs, n0)
    R_after = r_pro(b_after.to_numpy(), w_after.to_numpy(), lam,
                     R0, F_func, F_coefs, beta_func, beta_coefs, n0)

    raw_impact = d["score"].to_numpy() + R_after - R_before

    if impact_clip is not None:
        n_clipped = int(np.sum(np.abs(raw_impact) > impact_clip))
        if n_clipped:
            biggest = np.max(np.abs(raw_impact))
            print(f"Impact clipping: {n_clipped}/{len(raw_impact)} balls exceeded "
                  f"+/-{impact_clip:.1f} runs (largest raw magnitude: {biggest:.1f}); "
                  f"clipped to +/-{impact_clip:.1f}.")
        d["impact"] = np.clip(raw_impact, -impact_clip, impact_clip)
    else:
        d["impact"] = raw_impact

    d["bowl_impact"] = -d["impact"]
    return d


# --------------------------------------------------------------------------
# 5. Physical-delivery-attribute breakdowns
#    (line, length, shot, control, bowl speed, release point, bowl kind)
# --------------------------------------------------------------------------

PHYSICAL_COLS = [
    "line", "length", "shot", "control", "bowl_speed_category",
    "bowl_release_point", "bowl_kind", "bowl_style", "bowl_type",
]

SPEED_LABELS = {
    1: ">140 kph", 2: "130-140", 3: "120-130", 4: "110-120",
    5: "100-110", 6: "85-100", 7: "70-85", 8: "<70 kph",
}
RELEASE_LABELS = {1: ">2.0 m", 2: "1.8-2.0 m", 3: "1.6-1.8 m", 4: "<1.6 m"}


def ensure_physical_columns(raa_waa_df, df):
    """
    Make sure the observable delivery attributes are present on the ball-level
    RAA/WAA/Impact frame. `raa_waa_df` already carries them (it's built from a
    full copy of `df`), so this only backfills anything actually missing --
    it never merges duplicate copies of columns that are already there.
    """
    missing = [c for c in PHYSICAL_COLS if c not in raa_waa_df.columns]
    if not missing:
        return raa_waa_df
    phys = df[["row_id"] + missing].drop_duplicates(subset="row_id")
    return raa_waa_df.merge(phys, on="row_id", how="left")


def physical_breakdowns(d, min_count=25):
    """
    Returns a dict of small summary tables, each showing how batting/bowling
    Impact and RAA relate to an observable delivery attribute -- i.e. how the
    ball was actually bowled/played, not just what happened afterwards.
    """
    out = {}

    # --- line x length: bowling execution grid ---
    ll = (
        d.groupby(["length", "line"])
        .agg(balls=("bowl_impact", "size"), bowl_impact=("bowl_impact", "mean"),
             bowl_RAA=("bowl_RAA", "mean"))
        .reset_index()
        .query("balls >= @min_count")
    )
    out["line_length"] = ll

    # --- bowl speed category ---
    sp = (
        d.dropna(subset=["bowl_speed_category"])
        .groupby("bowl_speed_category")
        .agg(balls=("bowl_impact", "size"), bowl_impact=("bowl_impact", "mean"),
             bowl_RAA=("bowl_RAA", "mean"), bowl_WAA=("bowl_WAA", "mean"))
        .reset_index()
    )
    sp = sp[sp["balls"] >= min_count].sort_values("bowl_speed_category")
    sp["label"] = sp["bowl_speed_category"].map(SPEED_LABELS)
    out["bowl_speed"] = sp

    # --- release point ---
    rp = (
        d.dropna(subset=["bowl_release_point"])
        .groupby("bowl_release_point")
        .agg(balls=("bowl_impact", "size"), bowl_impact=("bowl_impact", "mean"),
             bowl_RAA=("bowl_RAA", "mean"))
        .reset_index()
    )
    rp = rp[rp["balls"] >= min_count].sort_values("bowl_release_point")
    rp["label"] = rp["bowl_release_point"].map(RELEASE_LABELS)
    out["release_point"] = rp

    # --- pace vs spin ---
    bk = (
        d.dropna(subset=["bowl_kind"])
        .groupby("bowl_kind")
        .agg(balls=("bowl_impact", "size"), bowl_impact=("bowl_impact", "mean"),
             bowl_RAA=("bowl_RAA", "mean"), bowl_WAA=("bowl_WAA", "mean"))
        .reset_index()
    )
    out["bowl_kind"] = bk[bk["balls"] >= min_count]

    # --- shot type (batting perspective) ---
    sh = (
        d.dropna(subset=["shot"])
        .groupby("shot")
        .agg(balls=("impact", "size"), impact=("impact", "mean"), RAA=("RAA", "mean"))
        .reset_index()
    )
    out["shot_type"] = sh[sh["balls"] >= min_count].sort_values("impact", ascending=False)

    # --- shot control: does playing a false shot cost runs even when not dismissed? ---
    ctl = (
        d.dropna(subset=["control"])
        .groupby("control")
        .agg(balls=("impact", "size"), impact=("impact", "mean"),
             RAA=("RAA", "mean"), WAA=("WAA", "mean"))
        .reset_index()
    )
    ctl["control"] = ctl["control"].map({1.0: "In control", 0.0: "False shot"})
    out["control"] = ctl

        # ------------------------------------------------------------
    # Year-specific versions for 2023 vs 2024 comparison plots
    # ------------------------------------------------------------

    years = [y for y in (2023, 2024) if y in set(d["year"].dropna().unique())]

    if years:
        # Line x length
        ll_year = (
            d[d["year"].isin(years)]
            .groupby(["year", "length", "line"])
            .agg(
                balls=("bowl_impact", "size"),
                bowl_impact=("bowl_impact", "mean"),
                bowl_RAA=("bowl_RAA", "mean"),
            )
            .reset_index()
            .query("balls >= @min_count")
        )
        out["line_length_year"] = ll_year

        # Bowl speed
        sp_year = (
            d[
                d["year"].isin(years)
                & d["bowl_speed_category"].notna()
            ]
            .groupby(["year", "bowl_speed_category"])
            .agg(
                balls=("bowl_impact", "size"),
                bowl_impact=("bowl_impact", "mean"),
                bowl_RAA=("bowl_RAA", "mean"),
                bowl_WAA=("bowl_WAA", "mean"),
            )
            .reset_index()
        )

        sp_year = (
            sp_year[sp_year["balls"] >= min_count]
            .sort_values(["year", "bowl_speed_category"])
        )

        sp_year["label"] = sp_year["bowl_speed_category"].map(SPEED_LABELS)
        out["bowl_speed_year"] = sp_year

        # Release point
        rp_year = (
            d[
                d["year"].isin(years)
                & d["bowl_release_point"].notna()
            ]
            .groupby(["year", "bowl_release_point"])
            .agg(
                balls=("bowl_impact", "size"),
                bowl_impact=("bowl_impact", "mean"),
                bowl_RAA=("bowl_RAA", "mean"),
            )
            .reset_index()
        )

        rp_year = (
            rp_year[rp_year["balls"] >= min_count]
            .sort_values(["year", "bowl_release_point"])
        )

        rp_year["label"] = rp_year["bowl_release_point"].map(RELEASE_LABELS)
        out["release_point_year"] = rp_year

        # Pace vs spin
        bk_year = (
            d[
                d["year"].isin(years)
                & d["bowl_kind"].notna()
            ]
            .groupby(["year", "bowl_kind"])
            .agg(
                balls=("bowl_impact", "size"),
                bowl_impact=("bowl_impact", "mean"),
                bowl_RAA=("bowl_RAA", "mean"),
                bowl_WAA=("bowl_WAA", "mean"),
            )
            .reset_index()
        )

        out["bowl_kind_year"] = bk_year[
            bk_year["balls"] >= min_count
        ]

        # Shot type
        sh_year = (
            d[
                d["year"].isin(years)
                & d["shot"].notna()
            ]
            .groupby(["year", "shot"])
            .agg(
                balls=("impact", "size"),
                impact=("impact", "mean"),
                RAA=("RAA", "mean"),
            )
            .reset_index()
        )

        out["shot_type_year"] = sh_year[
            sh_year["balls"] >= min_count
        ]

        # Shot control
        ctl_year = (
            d[
                d["year"].isin(years)
                & d["control"].notna()
            ]
            .groupby(["year", "control"])
            .agg(
                balls=("impact", "size"),
                impact=("impact", "mean"),
                RAA=("RAA", "mean"),
                WAA=("WAA", "mean"),
            )
            .reset_index()
        )

        ctl_year["control"] = ctl_year["control"].map({
            1.0: "In control",
            0.0: "False shot",
        })

        out["control_year"] = ctl_year

    return out


# --------------------------------------------------------------------------
# 6. Team performance
# --------------------------------------------------------------------------

def team_performance_by_year(d, min_balls=200):
    """
    Team-level batting and bowling Impact by year, expressed as Impact per
    100 balls rather than a season total. This matters because teams don't
    all play the same number of matches/balls in every season (e.g. a side
    that only made 12 games one year vs 16 the year before) -- a raw total
    Impact swing conflates "got better/worse per ball" with "played more or
    fewer balls", and per-100-balls removes that confound.
    """
    bat = (
        d.groupby(["team_bat", "year"])
        .agg(balls=("impact", "size"), total_impact=("impact", "sum"))
        .reset_index()
        .rename(columns={"team_bat": "team"})
    )
    bat["impact_per_100_balls"] = 100 * bat["total_impact"] / bat["balls"]
    bat = bat[bat["balls"] >= min_balls]

    bowl = (
        d.groupby(["team_bowl", "year"])
        .agg(balls=("bowl_impact", "size"), total_impact=("bowl_impact", "sum"))
        .reset_index()
        .rename(columns={"team_bowl": "team"})
    )
    bowl["impact_per_100_balls"] = 100 * bowl["total_impact"] / bowl["balls"]
    bowl = bowl[bowl["balls"] >= min_balls]

    return bat, bowl


def team_performance_change(bat_table, bowl_table):
    """
    Year-over-year change in batting and bowling Impact per 100 balls, by
    team (first year vs last year each team qualified in min_balls both
    tables). One row per team; positive change = improved.
    """
    years = sorted(set(bat_table["year"]).union(bowl_table["year"]))
    if len(years) < 2:
        return pd.DataFrame()
    y0, y1 = years[0], years[-1]

    bat_piv = bat_table.pivot(index="team", columns="year", values="impact_per_100_balls")
    bowl_piv = bowl_table.pivot(index="team", columns="year", values="impact_per_100_balls")
    all_teams = sorted(set(bat_piv.index) | set(bowl_piv.index))

    out = pd.DataFrame(index=all_teams)
    out[f"bat_impact_per100_{y0}"] = bat_piv.reindex(all_teams).get(y0)
    out[f"bat_impact_per100_{y1}"] = bat_piv.reindex(all_teams).get(y1)
    out["bat_change_per100"] = out[f"bat_impact_per100_{y1}"] - out[f"bat_impact_per100_{y0}"]
    out[f"bowl_impact_per100_{y0}"] = bowl_piv.reindex(all_teams).get(y0)
    out[f"bowl_impact_per100_{y1}"] = bowl_piv.reindex(all_teams).get(y1)
    out["bowl_change_per100"] = out[f"bowl_impact_per100_{y1}"] - out[f"bowl_impact_per100_{y0}"]

    return out.reset_index().rename(columns={"index": "team"}).sort_values("bat_change_per100", ascending=False)


def add_mean_relative_outputs(bat_summary, bowl_summary, season_bat_summary,
                                season_bowl_summary, team_bat_table, team_bowl_table,
                                phys_tables, auction_table=None, variation_table=None,
                                variation_table_speed=None):
    """Add role-mean-relative metrics without removing raw output columns."""
    bat_mean = bat_summary["impact_per_ball"].mean()
    bowl_mean = bowl_summary["impact_per_ball"].mean()

    bat_summary["impact_vs_mean_per_ball"] = bat_summary["impact_per_ball"] - bat_mean
    bowl_summary["impact_vs_mean_per_ball"] = bowl_summary["impact_per_ball"] - bowl_mean
    season_bat_summary["impact_vs_mean_per_ball"] = season_bat_summary["impact_per_ball"] - bat_mean
    season_bowl_summary["impact_vs_mean_per_ball"] = season_bowl_summary["impact_per_ball"] - bowl_mean
    team_bat_table["impact_vs_mean_per_100"] = team_bat_table["impact_per_100_balls"] - 100 * bat_mean
    team_bowl_table["impact_vs_mean_per_100"] = team_bowl_table["impact_per_100_balls"] - 100 * bowl_mean

    for name in ("shot_type", "control", "shot_type_year", "control_year"):
        if name in phys_tables:
            phys_tables[name]["impact_vs_mean_per_ball"] = phys_tables[name]["impact"] - bat_mean
    for name in ("line_length", "bowl_speed", "release_point", "bowl_kind",
                 "line_length_year", "bowl_speed_year", "release_point_year", "bowl_kind_year"):
        if name in phys_tables:
            phys_tables[name]["bowl_impact_vs_mean_per_ball"] = phys_tables[name]["bowl_impact"] - bowl_mean

    for table in (variation_table, variation_table_speed):
        if table is not None and len(table):
            table["mean_bowl_impact_vs_mean_per_ball"] = table["mean_bowl_impact"] - bowl_mean

    if auction_table is not None and len(auction_table):
        auction_role = np.where(auction_table["type"].eq("Bowler"), "bowling", "batting")
        auction_mean = np.where(auction_role == "bowling", bowl_mean, bat_mean)
        auction_table["impact_per_ball"] = auction_table["total_impact"] / auction_table["total_balls"]
        auction_table["impact_vs_mean_per_ball"] = auction_table["impact_per_ball"] - auction_mean
        auction_table["impact_vs_mean_per_unit_money"] = (
            auction_table["impact_vs_mean_per_ball"] / auction_table["price_in_unit"]
        )

    return {"batting": float(bat_mean), "bowling": float(bowl_mean)}


# --------------------------------------------------------------------------
# 4d. Venue effects ("park factors"). RAA/WAA/Impact are computed against a
#     baseline pooled across all grounds, so a ground that's unusually easy
#     or hard to score at will systematically inflate or deflate the numbers
#     of whoever plays there most (a home-ground effect) -- this doesn't fix
#     that, but surfaces it so it isn't invisible.
# --------------------------------------------------------------------------

def venue_summary(raa_waa_df, min_matches=3):
    """
    Per-ground scoring environment and average Impact, plus a "venue factor":
    that ground's mean runs-per-ball divided by the league-wide mean. 1.0 is
    league-average; above 1.0 is a hitter-friendly ground, below 1.0 is
    bowler-friendly. Grounds with fewer than min_matches are still returned
    (flagged via the `reliable` column) but excluded from anything that ranks
    or plots them, since 2-3 matches is not enough to trust a park factor.
    """
    d = raa_waa_df.dropna(subset=["ground"])
    match_counts = d.groupby("ground")["p_match"].nunique().rename("matches")

    agg = {"balls": ("score", "size"), "runs_per_ball": ("score", "mean"),
           "mean_bowl_impact": ("bowl_impact", "mean"), "mean_bat_impact": ("impact", "mean")}
    if "batruns" in d.columns:
        agg["six_rate"] = ("batruns", lambda s: (s == 6).mean())
    env = d.groupby("ground").agg(**agg).reset_index()
    env = env.merge(match_counts, on="ground")

    league_rpb = d["score"].mean()
    env["venue_factor"] = env["runs_per_ball"] / league_rpb
    env["reliable"] = env["matches"] >= min_matches
    return env.sort_values("venue_factor", ascending=False).reset_index(drop=True)


def player_venue_exposure(raa_waa_df, venue_table, player_col, min_balls=100):
    """
    For each qualifying player, the balls-weighted average venue factor of
    the grounds they actually played at. A batter sitting well above 1.0 has
    had more of their raw RAA/Impact shaped by favorable venues than the
    league as a whole; well below 1.0 for a bowler means the opposite --
    their numbers may understate their true skill relative to a neutral
    venue mix. Only uses grounds marked `reliable` in venue_table (matches >=
    min_matches there) to avoid weighting by a noisy 2-match park factor.
    """
    reliable = venue_table[venue_table["reliable"]][["ground", "venue_factor"]]
    merged = raa_waa_df.merge(reliable, on="ground", how="inner")
    out = (
        merged.groupby(player_col)
        .agg(balls=("venue_factor", "size"), avg_venue_factor=("venue_factor", "mean"))
        .query("balls >= @min_balls")
        .sort_values("avg_venue_factor", ascending=False)
    )
    return out


# --------------------------------------------------------------------------
# 4e. Impact per rupee spent (auction value). Joins ball-by-ball Impact to
#     an external auction price sheet (xlsx, one sheet per season, columns
#     including Player Name / Status / Final Price). Player names between
#     the two datasets don't always match exactly (nicknames, "Phil" vs
#     "Philip", etc.), so names are matched exactly first and then via fuzzy
#     matching within the same season; anything still unmatched is reported
#     rather than silently dropped.
# --------------------------------------------------------------------------

def parse_auction_price(value):
    """Parse a price string like '20.00 L' or '6.75 Cr' into rupees. Returns NaN for '--' / missing."""
    if pd.isna(value):
        return np.nan
    s = str(value).strip()
    if s in ("", "--", "-"):
        return np.nan
    m = re.match(r"^([\d.,]+)\s*(Cr|L|cr|l)$", s)
    if not m:
        return np.nan
    amount = float(m.group(1).replace(",", ""))
    unit = m.group(2).lower()
    return amount * (1e7 if unit == "cr" else 1e5)


def load_auction_data(xlsx_path, sheet_year_map=None):
    """
    Read an IPL-style auction workbook: one sheet per season, with a title
    row above the real header (header=1), and columns including at least
    'Player Name', 'Status', and 'Final Price'. sheet_year_map optionally
    maps sheet name -> year explicitly; otherwise the first 4-digit number
    found in the sheet name is used as the year.

    Returns one row per (player, year) with a positive resolved price --
    i.e. players who were actually Sold/Retained/Traded that year, not
    players who went Unsold (no salary, didn't play) or rows with an
    unparseable price.
    """
    xls = pd.ExcelFile(xlsx_path)
    frames = []
    for sheet in xls.sheet_names:
        if sheet_year_map is not None:
            year = sheet_year_map.get(sheet)
            if year is None:
                continue
        else:
            m = re.search(r"(20\d{2})", sheet)
            if not m:
                print(f"load_auction_data: couldn't infer a year from sheet name {sheet!r}; skipping it.")
                continue
            year = int(m.group(1))

        d = pd.read_excel(xlsx_path, sheet_name=sheet, header=1)
        needed = {"Player Name", "Final Price"}
        if not needed.issubset(d.columns):
            print(f"load_auction_data: sheet {sheet!r} is missing expected columns {needed}; skipping it.")
            continue
        d = d.rename(columns={"Player Name": "player", "Type": "type", "Status": "status", "Team": "auction_team"})
        d["year"] = year
        d["price_rupees"] = d["Final Price"].apply(parse_auction_price)
        keep_cols = [c for c in ["player", "year", "type", "status", "auction_team", "price_rupees"] if c in d.columns]
        frames.append(d[keep_cols])

    if not frames:
        return pd.DataFrame(columns=["player", "year", "price_rupees"])
    out = pd.concat(frames, ignore_index=True)
    return out.dropna(subset=["price_rupees"])


def player_total_impact_by_year(raa_waa_df):
    """
    Every player's TOTAL Impact by year and role, with no min-balls
    qualifying threshold (unlike the leaderboard summaries) -- for an
    auction-value comparison we want every player who took the field,
    including small-sample ones, as long as the ball counts are reported
    alongside so low-sample numbers can be judged accordingly.
    """
    bat = (raa_waa_df.groupby(["bat", "year"])
           .agg(bat_balls=("impact", "size"), bat_impact=("impact", "sum"))
           .reset_index().rename(columns={"bat": "player"}))
    bowl = (raa_waa_df.groupby(["bowl", "year"])
            .agg(bowl_balls=("bowl_impact", "size"), bowl_impact=("bowl_impact", "sum"))
            .reset_index().rename(columns={"bowl": "player"}))
    merged = pd.merge(bat, bowl, on=["player", "year"], how="outer").fillna(0)
    merged["total_balls"] = merged["bat_balls"] + merged["bowl_balls"]
    merged["total_impact"] = merged["bat_impact"] + merged["bowl_impact"]
    return merged


def match_player_names(bbb_names, auction_names, cutoff=0.82):
    """
    Exact match first, then difflib fuzzy match. A second pass then tries
    last-name + first-initial matching for anything still unresolved (this
    catches cases like "N Jagadeesan" vs "Narayan Jagadeesan" that plain
    string-similarity misses since the strings themselves aren't that
    similar) -- but only applies it when exactly one auction name shares
    that last name and initial, to avoid guessing between two different
    players who happen to share a surname.
    """
    auction_list = list(auction_names)

    def last_name(n):
        parts = n.strip().split()
        return parts[-1].lower() if parts else ""

    def first_initial(n):
        parts = n.strip().split()
        return parts[0][0].lower() if parts and parts[0] else ""

    auction_by_last = {}
    for a in auction_list:
        auction_by_last.setdefault(last_name(a), []).append(a)

    mapping, unmatched = {}, []
    for name in bbb_names:
        if name in auction_names:
            mapping[name] = name
            continue
        candidates = difflib.get_close_matches(name, auction_list, n=1, cutoff=cutoff)
        if candidates:
            mapping[name] = candidates[0]
            continue
        ln, fi = last_name(name), first_initial(name)
        same_surname = [a for a in auction_by_last.get(ln, []) if first_initial(a) == fi]
        if len(same_surname) == 1:
            mapping[name] = same_surname[0]
            continue
        name_tokens = name.lower().split()
        prefix_matches = [
            a for a in auction_list
            if (lambda t: t[: len(name_tokens)] == name_tokens or name_tokens[: len(t)] == t)(a.lower().split())  # noqa: PLC3002
        ]
        if len(prefix_matches) == 1:
            mapping[name] = prefix_matches[0]
        else:
            unmatched.append(name)
    return mapping, unmatched


def compute_impact_per_rupee(raa_waa_df, auction_df, money_unit="crore", fuzzy_cutoff=0.82):
    """
    Join per-player-per-year total Impact to auction price and compute
    Impact per unit of money spent (default: per crore rupees, since a
    literal "per rupee" figure would be an unreadably small number -- the
    underlying quantity is the same, just rescaled for legibility).

    Name matching is done separately within each year (a player's auction
    listing and their ball-by-ball name should refer to the same season's
    roster). Returns (matched_table, unmatched_names) where unmatched_names
    is a list of (player_name, year) pairs that couldn't be matched to an
    auction entry even fuzzily, so they can be inspected/fixed by hand if
    it matters (e.g. a debutant with a very unusual romanization).
    """
    unit_scale = {"crore": 1e7, "lakh": 1e5, "rupee": 1.0}[money_unit]
    impact_table = player_total_impact_by_year(raa_waa_df)

    matched_frames = []
    unmatched_all = []
    for year in sorted(impact_table["year"].unique()):
        bbb_year = impact_table[impact_table["year"] == year].copy()
        auc_year = auction_df[auction_df["year"] == year]
        if auc_year.empty:
            continue
        auction_names = set(auc_year["player"])
        mapping, unmatched = match_player_names(set(bbb_year["player"]), auction_names, cutoff=fuzzy_cutoff)
        unmatched_all.extend((name, year) for name in unmatched)

        bbb_year["auction_player"] = bbb_year["player"].map(mapping)
        merged = bbb_year.merge(
            auc_year[["player", "price_rupees", "type", "status", "auction_team"]].rename(
                columns={"player": "auction_player"}),
            on="auction_player", how="inner",
        )
        matched_frames.append(merged)

    if not matched_frames:
        return pd.DataFrame(), unmatched_all
    out = pd.concat(matched_frames, ignore_index=True)
    out["price_in_unit"] = out["price_rupees"] / unit_scale
    out["impact_per_unit_money"] = out["total_impact"] / out["price_in_unit"]
    out["money_unit"] = money_unit
    return out.sort_values("impact_per_unit_money", ascending=False).reset_index(drop=True), unmatched_all


# --------------------------------------------------------------------------
# 4f. Delivery-to-delivery variation. Treats line and length (and, when
#     requested, bowl speed category) as a small coordinate grid, measures
#     how far each delivery is (Euclidean distance) from the immediately
#     preceding delivery in the innings, and relates that distance to how
#     the delivery that changed actually performed -- i.e. does bowling a
#     delivery that's very different from the last one pay off, or is it
#     noise?
# --------------------------------------------------------------------------

LENGTH_COORD = {
    "SHORT": 1, "SHORT_OF_A_GOOD_LENGTH": 2, "GOOD_LENGTH": 3,
    "FULL": 4, "YORKER": 5, "FULL_TOSS": 6,
}
LINE_COORD = {
    "WIDE_OUTSIDE_OFFSTUMP": 1, "OUTSIDE_OFFSTUMP": 2, "ON_THE_STUMPS": 3, "DOWN_LEG": 4,
}
# bowl_speed_category is already an ordinal 1..8 scale (see SPEED_LABELS
# above), so it's used directly as the optional third grid coordinate --
# no relabeling needed, unlike line/length.
SPEED_COORD = {k: k for k in SPEED_LABELS}


def add_delivery_variation(raa_waa_df, same_bowler_only=False, include_speed=False):
    """
    Adds delivery_distance: the Euclidean distance, on the (length, line)
    grid above -- or, if include_speed=True, the (length, line, speed) grid
    -- between each delivery and the one immediately before it.

    include_speed=False (default): distance is the original 2-D distance on
    (length, line) alone.
    include_speed=True: bowl_speed_category (1..8) is added as a third
    coordinate, so delivery_distance becomes 3-D Euclidean distance over
    (length, line, speed). This captures a bowler changing pace between
    deliveries (e.g. a slower ball) as its own axis of variation, distinct
    from moving around on the length/line grid alone. Balls with a missing
    bowl_speed_category are dropped from this analysis the same way missing
    line/length values already are; the count dropped is printed.

    same_bowler_only=False (default) compares each ball to the literal
    previous ball in the innings, whoever bowled it -- this is the most
    direct reading of "ball i vs ball i+1", but a small share of these pairs
    will straddle a bowling change, so part of the "distance" there reflects
    a different bowler's natural line/length/pace rather than one bowler's
    own variation. same_bowler_only=True instead compares each ball only to
    that SAME bowler's previous delivery in the innings (skipping overs
    bowled by others in between), isolating one bowler's own shot-to-shot
    (and, with include_speed, pace-to-pace) variation. Both are legitimate
    questions; run both if you want to compare them.

    Deliveries with a line/length value outside the given coordinate maps
    (missing data, or the rare WIDE_DOWN_LEG line -- not part of the
    requested 4-category line grid) are dropped from this analysis, since
    there's no grid position to place them at. The count dropped is printed.
    """
    d = raa_waa_df.copy()
    d["length_coord"] = d["length"].map(LENGTH_COORD)
    d["line_coord"] = d["line"].map(LINE_COORD)

    coord_cols = ["length_coord", "line_coord"]
    if include_speed:
        d["speed_coord"] = d["bowl_speed_category"].map(SPEED_COORD)
        coord_cols.append("speed_coord")

    valid = np.logical_and.reduce([d[c].notna() for c in coord_cols])
    n_dropped = int((~valid).sum())
    if n_dropped:
        dims = "length/line/speed" if include_speed else "length/line"
        print(f"add_delivery_variation: dropping {n_dropped} balls with no {dims} grid position "
              f"(missing data, or line=WIDE_DOWN_LEG which isn't in the given 4-category line grid).")
    d = d[valid].copy()

    d = d.sort_values("row_id")
    group_cols = ["p_match", "inns", "bowl"] if same_bowler_only else ["p_match", "inns"]
    grp = d.groupby(group_cols, sort=False)

    # Signed per-axis deltas from the previous delivery -- exposed on the
    # frame (not just folded into delivery_distance) so they can be used as
    # separate regressors, e.g. in regress_bowl_impact_on_variation below.
    d["delta_length"] = d["length_coord"] - grp["length_coord"].shift(1)
    d["delta_line"] = d["line_coord"] - grp["line_coord"].shift(1)
    sq_terms = [d["delta_length"] ** 2, d["delta_line"] ** 2]
    if include_speed:
        d["delta_speed"] = d["speed_coord"] - grp["speed_coord"].shift(1)
        sq_terms.append(d["delta_speed"] ** 2)
    d["delivery_distance"] = np.sqrt(sum(sq_terms))
    return d


def regress_bowl_impact_on_variation(d, standardize=True):
    """
    OLS regression of bowl_impact on the ABSOLUTE change in length, line,
    and bowl speed category from the previous delivery -- lets the data
    decide which axis of "bowling variation" actually predicts bowling
    Impact, rather than folding all three into one hand-set, equally-
    weighted Euclidean distance. Requires
    add_delivery_variation(..., include_speed=True) to have been run first
    (needs delta_length/delta_line/delta_speed on the frame).

    standardize=True (default) z-scores each |delta| predictor first, so the
    three coefficients are directly comparable in size (effect of a 1-SD
    change in each) even though length/line/speed sit on different raw
    category-count scales (6, 4, and 8 respectively). Set False for
    coefficients in raw category-step units instead.

    Returns a small table: one row per term (intercept + the three deltas),
    with the fitted coefficient, the model's overall n and R-squared.
    """
    cols = ["delta_length", "delta_line", "delta_speed"]
    dd = d.dropna(subset=[c for c in cols if c in d.columns] + ["bowl_impact"]).copy()
    missing = [c for c in cols if c not in dd.columns]
    if missing:
        raise ValueError(f"regress_bowl_impact_on_variation: missing {missing}; "
                          f"call add_delivery_variation(..., include_speed=True) first.")

    X_raw = dd[cols].abs()
    X = (X_raw - X_raw.mean()) / X_raw.std(ddof=0) if standardize else X_raw.copy()
    X.insert(0, "intercept", 1.0)
    y = dd["bowl_impact"].to_numpy()

    coefs, _, _, _ = np.linalg.lstsq(X.to_numpy(), y, rcond=None)
    pred = X.to_numpy() @ coefs
    resid = y - pred
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan

    out = pd.Series(coefs, index=X.columns).rename("coef").reset_index().rename(columns={"index": "term"})
    out["r_squared"] = r2
    out["n"] = len(dd)
    out["standardized"] = standardize
    return out


def regress_bowl_impact_on_variation_by_year(d, standardize=True):
    """regress_bowl_impact_on_variation, run separately within each year, stacked into one table."""
    rows = []
    for yr, sub in d.dropna(subset=["year"]).groupby("year"):
        t = regress_bowl_impact_on_variation(sub, standardize=standardize)
        t["year"] = yr
        rows.append(t)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["term", "coef", "r_squared", "n", "standardized", "year"])


def variation_impact_by_year(d, min_count=20):
    """
    Mean bowling and batting Impact, grouped by (year, delivery_distance).
    The grid is small enough that delivery_distance only takes a handful of
    exact values, so grouping on the raw value (rounded to guard against
    float noise) gives a clean, fully granular table rather than needing
    arbitrary bins. Works whether delivery_distance came from the 2-D or the
    3-D (speed-inclusive) grid.
    """
    dd = d.dropna(subset=["delivery_distance"]).copy()
    dd["distance"] = dd["delivery_distance"].round(3)
    tbl = (
        dd.groupby(["year", "distance"])
        .agg(balls=("bowl_impact", "size"), mean_bowl_impact=("bowl_impact", "mean"), mean_bat_impact=("impact", "mean"))
        .reset_index()
    )
    tbl["reliable"] = tbl["balls"] >= min_count
    return tbl.sort_values(["year", "distance"]).reset_index(drop=True)


def variation_impact_by_year_speed(d, min_count=20):
    """
    Same idea as variation_impact_by_year, but keeps bowl_speed_category as
    its own grouping column alongside the (3-D, speed-inclusive) distance,
    so you can see e.g. "at distance X, was it mostly pace deliveries or
    mostly a pace change driving that distance, and did it pay off
    differently by speed band". Requires
    add_delivery_variation(..., include_speed=True) to have been run first.
    """
    dd = d.dropna(subset=["delivery_distance", "bowl_speed_category"]).copy()
    dd["distance"] = dd["delivery_distance"].round(3)
    dd["speed_label"] = dd["bowl_speed_category"].map(SPEED_LABELS)
    tbl = (
        dd.groupby(["year", "distance", "bowl_speed_category", "speed_label"])
        .agg(balls=("bowl_impact", "size"), mean_bowl_impact=("bowl_impact", "mean"), mean_bat_impact=("impact", "mean"))
        .reset_index()
    )
    tbl["reliable"] = tbl["balls"] >= min_count
    return tbl.sort_values(["year", "distance", "bowl_speed_category"]).reset_index(drop=True)


def variation_correlation_by_year(d):
    """Pearson correlation between delivery_distance and bowl_impact, by year -- a quick single-number summary."""
    dd = d.dropna(subset=["delivery_distance"])
    rows = []
    for yr, sub in dd.groupby("year"):
        rows.append({
            "year": yr, "n_balls": len(sub),
            "corr_distance_vs_bowl_impact": sub["delivery_distance"].corr(sub["bowl_impact"]),
            "corr_distance_vs_bat_impact": sub["delivery_distance"].corr(sub["impact"]),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# 4g. Wagon-wheel shot location analysis. wagonx/wagony/wagonzone are raw,
#     absolute field-pixel positions -- verified empirically (cross-tabbing
#     shot type against wagonzone by bat_hand) that they are NOT normalized
#     for batting handedness: a left-hander's cover drive lands in the
#     mirror-image zone of a right-hander's. Left-handers are mirrored here
#     onto a right-handed frame of reference (the majority in this data) so
#     "off side" and "leg side" mean the same physical side of the chart for
#     every batter before anything is aggregated across players.
# --------------------------------------------------------------------------

WAGON_CENTER = 181.5  # midpoint of the observed 0-363 pixel range on both axes


def add_wagon_batter_perspective(raa_waa_df):
    """
    Returns only rows with a real recorded shot location (wagonzone > 0;
    wagonzone == 0 / wagonx == wagony == 0 means no location was captured
    for that ball, including most LEFT_ALONE deliveries and some others with
    missing tracking data). Adds wagonx_bp, wagony_bp, wagonzone_bp: the
    batter-perspective coordinates and zone, mirrored for LHB batters so
    zone k for a lefty lines up with the same physical side of the field as
    zone k for a righty. The zone mirror pairs (1<->8, 2<->7, 3<->6, 4<->5)
    were derived from, and verified against, each zone's actual pixel
    centroid.
    """
    d = raa_waa_df[raa_waa_df["wagonzone"] > 0].copy()
    is_lhb = d["bat_hand"] == "LHB"
    d["wagonx_bp"] = np.where(is_lhb, 2 * WAGON_CENTER - d["wagonx"], d["wagonx"])
    d["wagony_bp"] = d["wagony"]
    d["wagonzone_bp"] = np.where(is_lhb, 9 - d["wagonzone"], d["wagonzone"]).astype(int)
    return d


def wagon_zone_proportions_by_year(d):
    """Share of (batter-perspective) shots landing in each of the 8 wagon zones, by year."""
    tbl = d.groupby(["year", "wagonzone_bp"]).size().rename("balls").reset_index()
    tbl["proportion"] = tbl["balls"] / tbl.groupby("year")["balls"].transform("sum")
    return tbl.sort_values(["year", "wagonzone_bp"]).reset_index(drop=True)


def zone_proportions_by_year(d, min_count=20):
    """
    What fraction of deliveries each year landed in each line x length "zone",
    plus the mean bowling Impact of that zone that year -- i.e. did bowlers
    change where they bowled, and did it work.
    """
    dd = d.dropna(subset=["line", "length"]).copy()
    dd["zone"] = dd["length"].astype(str) + " / " + dd["line"].astype(str)

    totals = dd.groupby("year").size().rename("total_balls")
    counts = dd.groupby(["year", "zone"]).size().rename("balls").reset_index()
    counts = counts.merge(totals, on="year")
    counts["proportion"] = counts["balls"] / counts["total_balls"]

    impact = dd.groupby(["year", "zone"])["bowl_impact"].mean().rename("mean_bowl_impact").reset_index()
    out = counts.merge(impact, on=["year", "zone"])
    return out[out["balls"] >= min_count]


def zone_change_table(zone_table):
    """Year-over-year change in bowling proportion and mean Impact, per zone (first year vs last year present)."""
    years = sorted(zone_table["year"].unique())
    if len(years) < 2:
        return pd.DataFrame()
    y0, y1 = years[0], years[-1]
    prop_pivot = zone_table.pivot(index="zone", columns="year", values="proportion")
    impact_pivot = zone_table.pivot(index="zone", columns="year", values="mean_bowl_impact")
    out = pd.DataFrame({
        f"prop_{y0}": prop_pivot.get(y0), f"prop_{y1}": prop_pivot.get(y1),
        f"impact_{y0}": impact_pivot.get(y0), f"impact_{y1}": impact_pivot.get(y1),
    })
    out["prop_change"] = out[f"prop_{y1}"] - out[f"prop_{y0}"]
    out["impact_change"] = out[f"impact_{y1}"] - out[f"impact_{y0}"]
    return out.dropna().sort_values("prop_change", ascending=False).reset_index().rename(columns={"index": "zone"})


def marginal_impact_by_shot(d, min_count=30, control_cols=("line", "length"), reference_shot=None, verbose=True):
    """
    Marginal batting Impact of each shot type, holding delivery characteristics
    (line, length by default) fixed via a linear regression with dummy
    variables. This is different from a raw mean-impact-by-shot table: a shot
    like DEFENDED mostly gets played to good-length deliveries, and PULL
    mostly to short ones, so raw means partly reflect how hard the delivery
    was rather than the value of the shot choice itself. Coefficients here are
    each shot's Impact relative to a reference shot, after controlling for
    which line/length it was played to.

    reference_shot: fix the baseline category explicitly (rather than letting
    get_dummies pick one alphabetically) -- required if you want marginal
    values to be comparable across separate calls, e.g. one regression per
    year (see marginal_impact_by_shot_year).
    """
    dd = d.dropna(subset=["shot", *control_cols, "impact"]).copy()
    shot_counts = dd["shot"].value_counts()
    valid_shots = shot_counts[shot_counts >= min_count].index.tolist()

    if reference_shot is None:
        reference_shot = shot_counts.loc[valid_shots].idxmax()
    elif reference_shot not in valid_shots:
        fallback = shot_counts.loc[valid_shots].idxmax() if valid_shots else None
        print(f"marginal_impact_by_shot: requested reference shot {reference_shot!r} doesn't clear "
              f"min_count in this subset; falling back to {fallback!r}.")
        reference_shot = fallback
    if reference_shot is None or not valid_shots:
        return pd.DataFrame(columns=["shot", "marginal_impact", "balls"])

    dd = dd[dd["shot"].isin(valid_shots)]
    ordered_cats = [reference_shot] + [s for s in valid_shots if s != reference_shot]
    dd["shot"] = pd.Categorical(dd["shot"], categories=ordered_cats)

    X_shot = pd.get_dummies(dd["shot"], prefix="shot", drop_first=True)
    X_controls = pd.get_dummies(dd[list(control_cols)], drop_first=True)
    X = pd.concat([X_shot, X_controls], axis=1).astype(float)
    X.insert(0, "intercept", 1.0)
    y = dd["impact"].to_numpy()

    coefs, _, _, _ = np.linalg.lstsq(X.to_numpy(), y, rcond=None)
    coef_series = pd.Series(coefs, index=X.columns)

    shot_effects = coef_series[[c for c in coef_series.index if c.startswith("shot_")]].copy()
    shot_effects.index = shot_effects.index.str.replace("shot_", "", regex=False)

    result = shot_effects.rename("marginal_impact").reset_index().rename(columns={"index": "shot"})
    result = pd.concat([result, pd.DataFrame({"shot": [reference_shot], "marginal_impact": [0.0]})],
                        ignore_index=True)
    result = result.merge(shot_counts.rename("balls"), left_on="shot", right_index=True)
    result = result.sort_values("marginal_impact", ascending=False).reset_index(drop=True)
    if verbose:
        print(f"marginal_impact_by_shot: reference shot = {reference_shot!r} "
              f"(all marginal_impact values are relative to it, controlling for {list(control_cols)})")
    return result


def shot_proportions_by_year(d, min_count=20):
    """
    Same idea as zone_proportions_by_year, but for shot selection: what
    fraction of deliveries each year were played with each shot, plus the
    raw mean batting Impact of that shot that year.
    """
    dd = d.dropna(subset=["shot"]).copy()
    totals = dd.groupby("year").size().rename("total_balls")
    counts = dd.groupby(["year", "shot"]).size().rename("balls").reset_index()
    counts = counts.merge(totals, on="year")
    counts["proportion"] = counts["balls"] / counts["total_balls"]

    impact = dd.groupby(["year", "shot"])["impact"].mean().rename("mean_impact").reset_index()
    out = counts.merge(impact, on=["year", "shot"])
    return out[out["balls"] >= min_count]


def marginal_impact_by_shot_year(d, min_count=20, control_cols=("line", "length"), reference_shot=None):
    """
    marginal_impact_by_shot, run separately within each year, using the SAME
    reference shot across years so the numbers are actually comparable
    year-to-year (otherwise each year's regression could silently pick a
    different baseline shot and "change" would be meaningless). Returns a
    wide table: one row per shot, marginal_impact_<year> for each year plus
    "change" (last year minus first year), sorted biggest riser first.
    """
    d = d.dropna(subset=["shot", *control_cols, "impact", "year"])
    years = sorted(d["year"].unique())

    if reference_shot is None:
        overall_counts = d["shot"].value_counts()
        # pick the highest-volume shot that also clears min_count in every year,
        # so it's actually usable as the reference in each year's regression
        for candidate in overall_counts.index:
            counts_by_year = d[d["shot"] == candidate].groupby("year").size()
            if (counts_by_year.reindex(years).fillna(0) >= min_count).all():
                reference_shot = candidate
                break
        if reference_shot is None:
            reference_shot = overall_counts.index[0]
    print(f"marginal_impact_by_shot_year: using fixed reference shot = {reference_shot!r} "
          f"across all years for comparability")

    per_year = {}
    for yr in years:
        sub = d[d["year"] == yr]
        t = marginal_impact_by_shot(sub, min_count=min_count, control_cols=control_cols,
                                     reference_shot=reference_shot, verbose=False)
        per_year[yr] = t.set_index("shot")[["marginal_impact", "balls"]]

    all_shots = sorted(set().union(*[t.index for t in per_year.values()]))
    combined = pd.DataFrame(index=all_shots)
    for yr in years:
        combined[f"marginal_impact_{yr}"] = per_year[yr]["marginal_impact"]
        combined[f"balls_{yr}"] = per_year[yr]["balls"]
    combined = combined.reset_index().rename(columns={"index": "shot"})

    if len(years) >= 2:
        y0, y1 = years[0], years[-1]
        combined["change"] = combined[f"marginal_impact_{y1}"] - combined[f"marginal_impact_{y0}"]
        combined = combined.sort_values("change", ascending=False)
    combined["reference_shot"] = reference_shot
    return combined.reset_index(drop=True)


# --------------------------------------------------------------------------
# 6b. Plot orchestration. Every plot_* function below is imported from
#     dl_pro_plots.py (plotnine); this function only decides which ones to
#     call and what to name the files.
# --------------------------------------------------------------------------

def generate_all_plots(plot_dir, fit_data, best_std, n0, lam_series,
                        bat_summary, bowl_summary, raa_waa_df, phys_tables, top_n_plot,
                        season_bat_summary=None, season_bowl_summary=None,
                        bat_changes=None, bowl_changes=None, n_movers_plot=8,
                        zone_table=None, shot_marginal_table=None,
                        shot_prop_table=None, shot_marginal_year_table=None,
                        team_change=None, zone_changes=None, venue_table=None, min_matches_venue=3,
                        auction_table=None, min_balls_auction=100, money_unit="crore",
                        variation_table=None, variation_overall_means=None, min_count_variation=20,
                        variation_table_speed=None,
                        wagon_df=None, wagon_zone_table=None):
    os.makedirs(plot_dir, exist_ok=True)

    example_lams = sorted(set([1.0] + list(lam_series[lam_series > 1.03].round(2).unique()[:2])))
    plot_fit_diagnostics(fit_data, best_std, n0, example_lams,
                          os.path.join(plot_dir, "01_dl_fit_diagnostics.png"))

    plot_top_n_bar(bat_summary, "impact_vs_mean_per_ball", f"Top {top_n_plot} batters vs role mean",
                   "Impact vs batting mean per ball", os.path.join(plot_dir, "02_top_batters_impact.png"),
                   top_n=top_n_plot, color="#3366cc")
    plot_top_n_bar(bowl_summary, "impact_vs_mean_per_ball", f"Top {top_n_plot} bowlers vs role mean",
                   "Impact vs bowling mean per ball", os.path.join(plot_dir, "03_top_bowlers_impact.png"),
                   top_n=top_n_plot, color="#cc3333")
    plot_top_n_bar(bat_summary, "impact_per_ball", f"Top {top_n_plot} batters by Impact per ball",
                   "Impact per ball", os.path.join(plot_dir, "04_top_batters_impact_rate.png"),
                   top_n=top_n_plot, color="#3366cc")
    plot_top_n_bar(bowl_summary, "impact_per_ball", f"Top {top_n_plot} bowlers by Impact per ball",
                   "Impact per ball", os.path.join(plot_dir, "05_top_bowlers_impact_rate.png"),
                   top_n=top_n_plot, color="#cc3333")

    plot_raa_vs_waa(bat_summary, "Batters: RAA vs WAA (size=balls faced, color=Impact)",
                     os.path.join(plot_dir, "06_raa_vs_waa_batters.png"))
    plot_raa_vs_waa(bowl_summary, "Bowlers: RAA vs WAA (size=balls bowled, color=Impact)",
                     os.path.join(plot_dir, "07_raa_vs_waa_bowlers.png"))

    plot_impact_distribution(raa_waa_df, os.path.join(plot_dir, "08_impact_distribution.png"))

    if len(phys_tables["line_length"]):
        line_length_plot = phys_tables["line_length"].copy()
        line_length_plot["bowl_impact"] = line_length_plot["bowl_impact_vs_mean_per_ball"]
        plot_line_length_heatmap(line_length_plot, os.path.join(plot_dir, "09_impact_by_line_length.png"))
    if len(phys_tables["bowl_speed"]):
        plot_physical_bar(phys_tables["bowl_speed"], "label", "bowl_impact_vs_mean_per_ball",
               "Bowling Impact vs role mean by ball speed", "Speed category",
               "Impact vs bowling mean per ball",
                           os.path.join(plot_dir, "10_impact_by_bowl_speed.png"),
                           order=[SPEED_LABELS[k] for k in sorted(SPEED_LABELS)])
    if len(phys_tables["release_point"]):
        plot_physical_bar(phys_tables["release_point"], "label", "bowl_impact_vs_mean_per_ball",
               "Bowling Impact vs role mean by release point", "Release height",
               "Impact vs bowling mean per ball",
                           os.path.join(plot_dir, "11_impact_by_release_point.png"),
                           order=[RELEASE_LABELS[k] for k in sorted(RELEASE_LABELS)])
    if len(phys_tables["bowl_kind"]):
        plot_physical_bar(phys_tables["bowl_kind"], "bowl_kind", "bowl_impact_vs_mean_per_ball",
               "Bowling Impact vs role mean: pace vs spin", "Bowl kind",
               "Impact vs bowling mean per ball",
                           os.path.join(plot_dir, "12_impact_by_bowl_kind.png"))
    if len(phys_tables["shot_type"]):
        plot_physical_bar(phys_tables["shot_type"].head(12), "shot", "impact_vs_mean_per_ball",
               "Batting Impact vs role mean by shot type (top 12 by sample size)",
               "Shot", "Impact vs batting mean per ball",
                           os.path.join(plot_dir, "13_impact_by_shot_type.png"))
    if len(phys_tables["control"]):
        plot_physical_bar(phys_tables["control"], "control", "impact_vs_mean_per_ball",
               "Batting Impact vs role mean: in control vs false shot",
               "Shot control", "Impact vs batting mean per ball",
                           os.path.join(plot_dir, "14_impact_by_control.png"))
        # ---- Physical attributes: 2023 vs 2024 ----

    if len(phys_tables["line_length_year"]):
        plot_line_length_heatmap_by_year(
            phys_tables["line_length_year"],
            os.path.join(plot_dir, "09b_impact_by_line_length_2023_2024.png"),
        )

    if len(phys_tables["bowl_speed_year"]):
        plot_physical_bar_by_year(
            phys_tables["bowl_speed_year"],
            "label",
            "bowl_impact",
            "Mean bowling Impact by ball speed: 2023 vs. 2024",
            "Speed category",
            "Mean bowl_impact per ball",
            os.path.join(plot_dir, "10b_impact_by_bowl_speed_2023_2024.png"),
            order=[SPEED_LABELS[k] for k in sorted(SPEED_LABELS)],
        )

    if len(phys_tables["release_point_year"]):
        plot_physical_bar_by_year(
            phys_tables["release_point_year"],
            "label",
            "bowl_impact",
            "Mean bowling Impact by release point: 2023 vs. 2024",
            "Release height",
            "Mean bowl_impact per ball",
            os.path.join(plot_dir, "11b_impact_by_release_point_2023_2024.png"),
            order=[RELEASE_LABELS[k] for k in sorted(RELEASE_LABELS)],
        )

    if len(phys_tables["bowl_kind_year"]):
        plot_physical_bar_by_year(
            phys_tables["bowl_kind_year"],
            "bowl_kind",
            "bowl_impact",
            "Mean bowling Impact: pace vs spin, 2023 vs. 2024",
            "Bowl kind",
            "Mean bowl_impact per ball",
            os.path.join(plot_dir, "12b_impact_by_bowl_kind_2023_2024.png"),
        )

    if len(phys_tables["shot_type_year"]):
        plot_physical_bar_by_year(
            phys_tables["shot_type_year"],
            "shot",
            "impact",
            "Mean batting Impact by shot type: 2023 vs. 2024",
            "Shot",
            "Mean batting Impact per ball",
            os.path.join(plot_dir, "13b_impact_by_shot_type_2023_2024.png"),
        )

    if len(phys_tables["control_year"]):
        plot_physical_bar_by_year(
            phys_tables["control_year"],
            "control",
            "impact",
            "Mean batting Impact by shot control: 2023 vs. 2024",
            "Shot control",
            "Mean batting Impact per ball",
            os.path.join(plot_dir, "14b_impact_by_control_2023_2024.png"),
        )

    print(f"Saved physical-attribute and core plots to: {plot_dir}/")

    # ---- Season-level trajectory / movers plots ----
    if season_bat_summary is not None and bat_changes is not None and not bat_changes.empty:
        movers = pd.concat([bat_changes.head(n_movers_plot // 2),
                             bat_changes.tail(n_movers_plot // 2)])["bat"].unique().tolist()
        plot_season_trajectory(season_bat_summary, movers, "bat", "impact_per_ball",
                                "Batters: Impact per ball by season (biggest movers)",
                                "Impact per ball", os.path.join(plot_dir, "15_season_trajectory_batters.png"))
        plot_season_movers_bar(bat_changes, "bat",
                                "Biggest season-over-season swings: batters",
                                os.path.join(plot_dir, "16_season_movers_batters.png"),
                                top_n=n_movers_plot)

    if season_bowl_summary is not None and bowl_changes is not None and not bowl_changes.empty:
        movers = pd.concat([bowl_changes.head(n_movers_plot // 2),
                             bowl_changes.tail(n_movers_plot // 2)])["bowl"].unique().tolist()
        plot_season_trajectory(season_bowl_summary, movers, "bowl", "impact_per_ball",
                                "Bowlers: Impact per ball by season (biggest movers)",
                                "Impact per ball", os.path.join(plot_dir, "17_season_trajectory_bowlers.png"))
        plot_season_movers_bar(bowl_changes, "bowl",
                                "Biggest season-over-season swings: bowlers",
                                os.path.join(plot_dir, "18_season_movers_bowlers.png"),
                                top_n=n_movers_plot)

    if zone_table is not None and len(zone_table):
        plot_zone_year_comparison(zone_table, os.path.join(plot_dir, "19_zone_proportions_by_year.png"))

    if shot_marginal_table is not None and len(shot_marginal_table):
        plot_marginal_impact_by_shot(shot_marginal_table,
                                      os.path.join(plot_dir, "20_marginal_impact_by_shot.png"))

    if (shot_prop_table is not None and len(shot_prop_table)
            and shot_marginal_year_table is not None and len(shot_marginal_year_table)):
        plot_shot_year_comparison(shot_prop_table, shot_marginal_year_table,
                                   os.path.join(plot_dir, "21_shot_selection_by_year.png"))

    if team_change is not None and len(team_change):
        plot_team_impact_per100(team_change, os.path.join(plot_dir, "22_team_impact_per100.png"))

    if zone_changes is not None and len(zone_changes):
        plot_zone_quadrant(zone_changes, os.path.join(plot_dir, "23_zone_quadrant.png"))

    if (shot_prop_table is not None and len(shot_prop_table)
            and shot_marginal_year_table is not None and len(shot_marginal_year_table)):
        plot_shot_quadrant(shot_prop_table, shot_marginal_year_table,
                            os.path.join(plot_dir, "24_shot_quadrant.png"))

    if venue_table is not None and len(venue_table):
        plot_venue_factor(venue_table, os.path.join(plot_dir, "25_venue_factor.png"), min_matches=min_matches_venue)

    if auction_table is not None and len(auction_table):
        plot_impact_per_rupee_leaderboard(auction_table, os.path.join(plot_dir, "26_impact_per_rupee_leaderboard.png"),
                                           min_balls=min_balls_auction, money_unit=money_unit)
        plot_price_vs_impact(auction_table, os.path.join(plot_dir, "27_price_vs_impact.png"),
                              min_balls=min_balls_auction, money_unit=money_unit)

    if variation_table is not None and len(variation_table):
        plot_variation_impact(variation_table, os.path.join(plot_dir, "28_delivery_variation_impact.png"),
                               overall_means=variation_overall_means, min_count=min_count_variation)

    if variation_table_speed is not None and len(variation_table_speed):
        plot_variation_impact_by_speed(variation_table_speed,
                                        os.path.join(plot_dir, "28b_delivery_variation_impact_by_speed.png"),
                                        min_count=min_count_variation)

    if wagon_df is not None and len(wagon_df):
        plot_wagon_impact_gradient(wagon_df, os.path.join(plot_dir, "29_wagon_impact_gradient.png"))
    if wagon_zone_table is not None and len(wagon_zone_table):
        plot_wagon_zone_pies(wagon_zone_table, os.path.join(plot_dir, "30_wagon_zone_shares.png"))

    print(f"Saved plots to: {plot_dir}/")


# --------------------------------------------------------------------------
# 7. Main (original CSV-driven pipeline: physical attributes, season, venue,
#    auction, wagon-wheel, shot/zone analyses -- everything from the earlier
#    version of this script. Requires a ball-tracking CSV with line/length/
#    bowl_speed_category/shot/control/wagon columns, e.g.
#    Data_Premier_League_Prelims_Dataset.csv.)
# --------------------------------------------------------------------------

def main(csv_path, outdir="dl_pro_out", fix_n0=1.04, impact_clip=20.0,
         min_balls_bat=100, min_balls_bowl=100, min_balls_season=100, min_balls_team=200,
         min_matches_venue=4, top_n_plot=20,
         auction_xlsx_path=None, money_unit="crore", min_balls_auction=100, name_match_cutoff=0.82,
         min_count_variation=20, variation_include_speed=False,
         impact_player_json_dir=None, impact_player_year_min=None, impact_player_year_max=None):
    os.makedirs(outdir, exist_ok=True)
    plot_dir = os.path.join(outdir, "plots")
    os.makedirs(plot_dir, exist_ok=True)

    df = pd.read_csv(csv_path, low_memory=False)

    # ---- Drop rain/DLS-affected matches up front ----
    clean_matches = get_clean_matches(df)
    report_dropped_matches(df, clean_matches)
    df = df[df["p_match"].isin(clean_matches)].copy()

    # ---- DL curve fitting on first-innings data ----
    state = build_state_table(df)
    fit_data = state[state["inns"] == 1].copy()
    print(f"Fitting DL curves on {len(fit_data)} balls from {fit_data['p_match'].nunique()} first innings.\n")

    aic_table, best_std, all_results = select_best_by_aic(fit_data)
    print("=== AIC comparison: F(w) and beta(w) polynomial degree ===")
    print(aic_table.to_string(index=False))
    print(f"\nBest by AIC: F degree={best_std['p_degree']}, beta degree={best_std['q_degree']}\n")

    print("=== DL Standard best-fit parameters ===")
    print(f"R0 = {best_std['R0']:.2f}")
    print(f"F(w) coefficients (w^1..w^{best_std['n_F']}): {np.round(best_std['F_coefs'], 5)}")
    print(f"beta(w) coefficients (w^0..w^{best_std['n_beta']-1}): {np.round(best_std['beta_coefs'], 3)}\n")

    # ---- DL Pro: fit n0, compute lambda per match ----
    S1 = first_innings_scores(df)
    R0 = best_std["R0"]
    R_std_full = R0 * best_std["F_func"](np.array([0.0]), *best_std["F_coefs"])[0] * (
        1 - np.exp(-120.0 / np.clip(
            best_std["beta_func"](np.array([0.0]), *best_std["beta_coefs"])[0], 1e-3, None))
    )
    n_above = int((S1 > R_std_full).sum())
    print("=== DL Pro ===")
    print(f"{n_above}/{len(S1)} matches score above R_std(120,0)={R_std_full:.1f} "
          f"and therefore get lambda > 1.")

    if fix_n0 is not None:
        n0_best = float(fix_n0)
        lam_map = {
            m: solve_lambda(S1.loc[m], R0, best_std["F_func"], best_std["F_coefs"],
                             best_std["beta_func"], best_std["beta_coefs"], n0_best, R_std_full)
            for m in S1.index
        }
    else:
        n0_best, lam_map, R_std_full = fit_n0(fit_data, S1, best_std)

    print(f"n0 used = {n0_best:.4f}  ({'fixed' if fix_n0 is not None else 'fit by SSE minimization'}; "
          f"paper reports n0 = 1.04)")
    lam_series = pd.Series(lam_map, name="lambda")
    print(f"lambda summary across {len(lam_series)} matches:")
    print(lam_series.describe().to_string())
    print()

    # ---- RAA / WAA (batting and bowling) ----
    raa_waa_df, baselines = compute_raa_waa(df)

    # ---- Impact (DL-based runs-added metric, batting and bowling) ----
    impact_df = compute_impact(df, best_std, n0_best, lam_map, impact_clip=impact_clip)
    raa_waa_df = raa_waa_df.merge(
        impact_df[["row_id", "impact", "bowl_impact"]], on="row_id", how="left"
    )

    # ---- Physical-delivery-attribute breakdowns ----
    raa_waa_df = ensure_physical_columns(raa_waa_df, df)
    phys_tables = physical_breakdowns(raa_waa_df)

    bat_summary = summarize_by_player(raa_waa_df, "bat", "RAA", "WAA", "impact", min_balls=min_balls_bat)
    bowl_summary = summarize_by_player(raa_waa_df, "bowl", "bowl_RAA", "bowl_WAA", "bowl_impact", min_balls=min_balls_bowl)
    bowl_summary = bowl_summary.rename(columns={"runs": "runs_conceded"})

    print(f"=== Top 10 batters by Impact (min {min_balls_bat} balls faced) ===")
    print(bat_summary.head(10).round(2).to_string())
    print()
    print(f"=== Top 10 bowlers by Impact (min {min_balls_bowl} balls bowled) ===")
    print(bowl_summary.head(10).round(2).to_string())
    print()

    print("=== Physical attributes: mean bowling Impact by ball speed ===")
    print(phys_tables["bowl_speed"][["label", "balls", "bowl_impact", "bowl_RAA", "bowl_WAA"]]
          .round(3).to_string(index=False))
    print()

    # ---- Season-level analysis ----
    season_bat_summary = summarize_by_player_season(raa_waa_df, "bat", "RAA", "WAA", "impact",
                                                      min_balls=min_balls_season)
    season_bowl_summary = summarize_by_player_season(raa_waa_df, "bowl", "bowl_RAA", "bowl_WAA", "bowl_impact",
                                                       min_balls=min_balls_season)
    bat_changes = season_over_season_changes(season_bat_summary, "bat", "impact_per_ball")
    bowl_changes = season_over_season_changes(season_bowl_summary, "bowl", "impact_per_ball")

    # ---- Bowling zone (line x length) proportions, year over year ----
    zone_table = zone_proportions_by_year(raa_waa_df)
    zone_changes = zone_change_table(zone_table)

    # ---- Marginal batting Impact by shot type, controlling for line & length ----
    shot_marginal_table = marginal_impact_by_shot(raa_waa_df)
    shot_prop_table = shot_proportions_by_year(raa_waa_df)
    shot_marginal_year_table = marginal_impact_by_shot_year(raa_waa_df)

    # ---- Team performance, by Impact per 100 balls ----
    team_bat_table, team_bowl_table = team_performance_by_year(raa_waa_df, min_balls=min_balls_team)
    team_change = team_performance_change(team_bat_table, team_bowl_table)

    # ---- Venue effects ("park factors") ----
    venue_table = venue_summary(raa_waa_df, min_matches=min_matches_venue)
    bat_venue_exposure = player_venue_exposure(raa_waa_df, venue_table, "bat", min_balls=min_balls_bat)
    bowl_venue_exposure = player_venue_exposure(raa_waa_df, venue_table, "bowl", min_balls=min_balls_bowl)

    # ---- Impact per rupee spent (auction value), only if an auction workbook is configured ----
    auction_table, auction_unmatched = pd.DataFrame(), []
    if auction_xlsx_path is not None and os.path.exists(auction_xlsx_path):
        auction_df = load_auction_data(auction_xlsx_path)
        if not auction_df.empty:
            auction_table, auction_unmatched = compute_impact_per_rupee(
                raa_waa_df, auction_df, money_unit=money_unit, fuzzy_cutoff=name_match_cutoff
            )

    # ---- Delivery-to-delivery variation on the line/length grid (optionally + speed) ----
    variation_df = add_delivery_variation(raa_waa_df, same_bowler_only=False, include_speed=False)
    variation_table = variation_impact_by_year(variation_df, min_count=min_count_variation)
    variation_corr = variation_correlation_by_year(variation_df)
    overall_means = raa_waa_df.groupby("year")["bowl_impact"].mean().to_dict()

    variation_table_speed = pd.DataFrame()
    variation_regression_table = pd.DataFrame()
    variation_change = {}
    if variation_include_speed and "bowl_speed_category" in raa_waa_df.columns:
        variation_df_speed = add_delivery_variation(raa_waa_df, same_bowler_only=False, include_speed=True)
        variation_table_speed = variation_impact_by_year_speed(variation_df_speed, min_count=min_count_variation)
        variation_table_speed.to_csv(os.path.join(outdir, "delivery_variation_impact_by_year_speed.csv"), index=False)
        print("=== Delivery variation (length/line/SPEED, 3-D) vs. bowling Impact, by year/distance/speed ===")
        print(variation_table_speed[variation_table_speed["reliable"]].round(3).to_string(index=False))
        print()

        # ---- Let a regression, not a hand-set Euclidean distance, decide which
        #      axis of variation (length, line, or speed change) actually predicts
        #      bowling Impact, and how that's shifted by year. ----
        variation_regression_table = regress_bowl_impact_on_variation_by_year(variation_df_speed, standardize=True)
        print("=== Regression: |\u0394length|, |\u0394line|, |\u0394speed| (standardized) predicting bowl_impact, by year ===")
        print(variation_regression_table.round(4).to_string(index=False))
        print()
        variation_regression_table.to_csv(
            os.path.join(outdir, "delivery_variation_regression_by_year.csv"), index=False)
        plot_variation_regression_coefs(
            variation_regression_table, os.path.join(plot_dir, "28c_variation_regression_coefs.png"))

        # ---- Did average per-delivery variation change year over year (and did its payoff)? ----
        # Must live inside this block: variation_df_speed only exists when include_speed ran.
        variation_change = summarize_variation_change(variation_df_speed, outdir=outdir, plot_dir=plot_dir)

    variation_df_samebowler = add_delivery_variation(raa_waa_df, same_bowler_only=True)
    variation_table_samebowler = variation_impact_by_year(variation_df_samebowler, min_count=min_count_variation)

    # ---- Wagon-wheel shot location: Impact gradient and zone shares, by year ----
    wagon_df = add_wagon_batter_perspective(raa_waa_df)
    wagon_zone_table = wagon_zone_proportions_by_year(wagon_df)

    role_means = add_mean_relative_outputs(
        bat_summary, bowl_summary, season_bat_summary, season_bowl_summary,
        team_bat_table, team_bowl_table, phys_tables, auction_table,
        variation_table, variation_table_speed,
    )
    print(f"Role means (Impact/ball): batting={role_means['batting']:.3f}, "
          f"bowling={role_means['bowling']:.3f}")

    # Save team-by-year breakdowns used by the dashboard, including the
    # role-relative values shown in its team charts.
    team_shot_rows, team_zone_rows, team_line_rows, team_speed_rows = [], [], [], []
    for team in sorted(raa_waa_df["team_bat"].dropna().unique()):
        bat_team = raa_waa_df[raa_waa_df["team_bat"] == team]
        for (year, shot), g in bat_team.dropna(subset=["shot"]).groupby(["year", "shot"]):
            value = g["impact"].mean()
            team_shot_rows.append({"team": team, "year": int(year), "shot": shot,
                                   "balls": len(g), "impact": value,
                                   "impact_vs_mean": value - role_means["batting"]})
        for (year, zone), g in wagon_df[wagon_df["team_bat"] == team].groupby(["year", "wagonzone_bp"]):
            value = g["impact"].mean()
            team_zone_rows.append({"team": team, "year": int(year), "zone": int(zone),
                                   "balls": len(g), "impact": value,
                                   "impact_vs_mean": value - role_means["batting"]})
    for team in sorted(raa_waa_df["team_bowl"].dropna().unique()):
        bowl_team = raa_waa_df[raa_waa_df["team_bowl"] == team]
        for (year, length, line), g in bowl_team.dropna(subset=["length", "line"]).groupby(["year", "length", "line"]):
            value = g["bowl_impact"].mean()
            team_line_rows.append({"team": team, "year": int(year), "length": length, "line": line,
                                   "balls": len(g), "impact": value,
                                   "impact_vs_mean": value - role_means["bowling"]})
        for (year, speed), g in bowl_team.dropna(subset=["bowl_speed_category"]).groupby(["year", "bowl_speed_category"]):
            value = g["bowl_impact"].mean()
            team_speed_rows.append({"team": team, "year": int(year), "speed": int(speed),
                                    "balls": len(g), "impact": value,
                                    "impact_vs_mean": value - role_means["bowling"]})
    pd.DataFrame(team_shot_rows).to_csv(os.path.join(outdir, "team_shot_type_by_year.csv"), index=False)
    pd.DataFrame(team_zone_rows).to_csv(os.path.join(outdir, "team_wagon_zone_by_year.csv"), index=False)
    pd.DataFrame(team_line_rows).to_csv(os.path.join(outdir, "team_line_length_by_year.csv"), index=False)
    pd.DataFrame(team_speed_rows).to_csv(os.path.join(outdir, "team_speed_by_year.csv"), index=False)

    # ---- Save tables ----
    fit_params = {
        "F_degree": best_std["p_degree"], "beta_degree": best_std["q_degree"],
        "R0": best_std["R0"], "F_coefs": best_std["F_coefs"],
        "beta_coefs": best_std["beta_coefs"], "n0": n0_best,
        "R_std_120_0": R_std_full, "impact_clip": impact_clip,
        "n_clean_matches": int(df["p_match"].nunique()),
    }
    import json
    with open(os.path.join(outdir, "dl_pro_params.json"), "w") as f:
        json.dump(fit_params, f, indent=2)

    aic_table.to_csv(os.path.join(outdir, "aic_comparison.csv"), index=False)
    lam_series.to_csv(os.path.join(outdir, "lambda_by_match.csv"))
    raa_waa_df.to_csv(os.path.join(outdir, "ball_by_ball_raa_waa_impact.csv"), index=False)
    bat_summary.to_csv(os.path.join(outdir, "batter_summary.csv"))
    bowl_summary.to_csv(os.path.join(outdir, "bowler_summary.csv"))
    for name, table in phys_tables.items():
        table.to_csv(os.path.join(outdir, f"physical_{name}.csv"), index=False)
    season_bat_summary.to_csv(os.path.join(outdir, "season_batter_summary.csv"), index=False)
    season_bowl_summary.to_csv(os.path.join(outdir, "season_bowler_summary.csv"), index=False)
    bat_changes.to_csv(os.path.join(outdir, "season_change_batters.csv"), index=False)
    bowl_changes.to_csv(os.path.join(outdir, "season_change_bowlers.csv"), index=False)
    zone_table.to_csv(os.path.join(outdir, "zone_proportions_by_year.csv"), index=False)
    zone_changes.to_csv(os.path.join(outdir, "zone_year_change.csv"), index=False)
    shot_marginal_table.to_csv(os.path.join(outdir, "marginal_impact_by_shot.csv"), index=False)
    shot_prop_table.to_csv(os.path.join(outdir, "shot_proportions_by_year.csv"), index=False)
    shot_marginal_year_table.to_csv(os.path.join(outdir, "marginal_impact_by_shot_year.csv"), index=False)
    team_bat_table.to_csv(os.path.join(outdir, "team_batting_impact_per100_by_year.csv"), index=False)
    team_bowl_table.to_csv(os.path.join(outdir, "team_bowling_impact_per100_by_year.csv"), index=False)
    team_change.to_csv(os.path.join(outdir, "team_performance_change.csv"), index=False)
    venue_table.to_csv(os.path.join(outdir, "venue_summary.csv"), index=False)
    bat_venue_exposure.to_csv(os.path.join(outdir, "batter_venue_exposure.csv"))
    bowl_venue_exposure.to_csv(os.path.join(outdir, "bowler_venue_exposure.csv"))
    if len(auction_table):
        auction_table.to_csv(os.path.join(outdir, "impact_per_rupee.csv"), index=False)
    if auction_unmatched:
        pd.DataFrame(auction_unmatched, columns=["player", "year"]).to_csv(
            os.path.join(outdir, "auction_unmatched_names.csv"), index=False)
    variation_table.to_csv(os.path.join(outdir, "delivery_variation_impact_by_year.csv"), index=False)
    variation_table_samebowler.to_csv(
        os.path.join(outdir, "delivery_variation_impact_by_year_samebowler.csv"), index=False)
    variation_corr.to_csv(os.path.join(outdir, "delivery_variation_correlation_by_year.csv"), index=False)
    wagon_zone_table.to_csv(os.path.join(outdir, "wagon_zone_proportions_by_year.csv"), index=False)

    # ---- Plots ----
    generate_all_plots(plot_dir, fit_data, best_std, n0_best, lam_series,
                        bat_summary, bowl_summary, raa_waa_df, phys_tables, top_n_plot,
                        season_bat_summary=season_bat_summary, season_bowl_summary=season_bowl_summary,
                        bat_changes=bat_changes, bowl_changes=bowl_changes,
                        zone_table=zone_table, shot_marginal_table=shot_marginal_table,
                        shot_prop_table=shot_prop_table, shot_marginal_year_table=shot_marginal_year_table,
                        team_change=team_change, zone_changes=zone_changes, venue_table=venue_table,
                        min_matches_venue=min_matches_venue,
                        auction_table=auction_table, min_balls_auction=min_balls_auction, money_unit=money_unit,
                        variation_table=variation_table, variation_overall_means=overall_means,
                        min_count_variation=min_count_variation, variation_table_speed=variation_table_speed,
                        wagon_df=wagon_df, wagon_zone_table=wagon_zone_table)

    # ---- Impact Player substitutions vs. the team's median same-role
    #      alternative, using THIS dataset's own Impact numbers. Substitution
    #      events themselves come from raw Cricsheet JSON (Data_Premier_League
    #      Prelims-style CSVs don't carry a substitution flag), matched back
    #      to this CSV's matches by p_match. ----
    impact_sub_perf = pd.DataFrame()
    if impact_player_json_dir is not None and os.path.isdir(impact_player_json_dir):
        match_ids = set(df["p_match"].astype(str).unique())
        subs_df = extract_impact_subs_from_json(impact_player_json_dir, match_ids=match_ids)
        if len(subs_df):
            subs_df["p_match"] = subs_df["p_match"].astype(raa_waa_df["p_match"].dtype)
            subs_df, name_unresolved = resolve_substitute_names(subs_df, raa_waa_df)
            if name_unresolved:
                print(f"\n{len(name_unresolved)} substitute name(s) from the JSON couldn't be resolved "
                      f"to a name in this CSV even fuzzily (shown as (match, name)): "
                      f"{name_unresolved[:10]}{'...' if len(name_unresolved) > 10 else ''}")
            impact_sub_perf = compute_slot_performance(raa_waa_df, subs_df)

        if len(impact_sub_perf):
            impact_sub_filtered = filter_impact_subs_by_year(
                impact_sub_perf, impact_player_year_min, impact_player_year_max)
            print(f"\n{len(impact_sub_perf)} Impact Player substitution events matched to this dataset "
                  f"({len(impact_sub_filtered)} within year range "
                  f"{impact_player_year_min}-{impact_player_year_max}).")

            impact_summaries = summarize_impact_subs(
                impact_sub_perf, year_min=impact_player_year_min, year_max=impact_player_year_max)
            print("\n=== Combined slot Impact vs. WORST same-role teammate, by year x team_role ===")
            print(impact_summaries["vs_worst_by_year_teamrole"].to_string())
            print("\n=== Combined slot Impact vs. WORST same-role teammate, by slot role (pooled) ===")
            print(impact_summaries["vs_worst_by_slotrole"].to_string())
            print("\n=== Combined slot Impact vs. MEAN same-role teammate, by year x team_role ===")
            print(impact_summaries["vs_mean_by_year_teamrole"].to_string())
            print("\n=== Combined slot Impact vs. MEAN same-role teammate, by slot role (pooled) ===")
            print(impact_summaries["vs_mean_by_slotrole"].to_string())

            impact_sub_filtered.to_csv(os.path.join(outdir, "impact_player_performance.csv"), index=False)
            for name, table in impact_summaries.items():
                table.to_csv(os.path.join(outdir, f"impact_player_{name}.csv"))

            plot_impact_sub_distribution(
                impact_sub_perf, os.path.join(plot_dir, "31_impact_sub_distribution"),
                year_min=impact_player_year_min, year_max=impact_player_year_max)
            plot_impact_sub_distribution_by_year_role(
                impact_sub_perf, os.path.join(plot_dir, "31b_impact_sub_distribution_by_year_role.png"))
        else:
            print(f"\nNo Impact Player substitution events from {impact_player_json_dir!r} "
                  f"matched a player/match in this dataset; skipping that analysis.")

    print(f"\nSaved parameters, tables and plots to: {outdir}/")
    return (fit_params, lam_series, raa_waa_df, bat_summary, bowl_summary, aic_table, best_std,
            phys_tables, season_bat_summary, season_bowl_summary, bat_changes, bowl_changes,
            zone_table, zone_changes, shot_marginal_table, shot_prop_table, shot_marginal_year_table,
            team_bat_table, team_bowl_table, team_change, venue_table, bat_venue_exposure, bowl_venue_exposure,
            auction_table, variation_table, variation_table_samebowler, variation_corr, variation_table_speed,
            variation_regression_table, impact_sub_perf,
            wagon_df, wagon_zone_table)


# --------------------------------------------------------------------------
# 8. Impact Player substitution analysis. Ingests raw Cricsheet match JSON
#    (does NOT require the physical-attribute CSV used by Section 7/main --
#    only ball-by-ball runs/wickets/extras, which Cricsheet always has),
#    identifies Impact Player substitution events from the `replacements`
#    block Cricsheet attaches to deliveries, fits the same DL Pro curve as
#    above on that data, and measures the batting Impact and bowling Impact
#    (the "two different" Impact stats) the substitute produced for the
#    rest of the match -- split by year and by which innings they entered.
# --------------------------------------------------------------------------

import glob
import json as _json


def season_to_year(season):
    """Cricsheet season strings are e.g. '2016' or '2020/21' (crosses New Year) -> int year."""
    s = str(season)
    return int(s.split("/")[0]) if "/" in s else int(s)


def load_cricsheet_matches(json_dir):
    """
    Parse every Cricsheet match JSON in json_dir into:
      - df: one row per legal-or-illegal delivery, with the columns Sections
            1-4 above need (p_match, year, ground, inns, over_num, team_bat,
            team_bowl, bat, bowl, score, wide, noball, out, max_balls,
            inns_balls_rem, inns_wkts, inns_runs, row_id).
      - subs_df: one row per Impact Player substitution event found in the
            `replacements` block of a delivery (reason == "impact_player"),
            with p_match, year, inns_entered (1 or 2 -- which innings the
            substitution was recorded in), over_num, team, player_in,
            player_out.

    Super-over innings (a 3rd/4th `innings` entry) are ignored -- only the
    two main innings are used, same as the rest of this module (which is
    built around a fixed 120-ball-per-innings T20 game). DLS/rain-shortened
    second innings are flagged via a reduced `max_balls` (from the
    innings' `target.overs`, when present) so get_clean_matches drops them
    exactly as it would a CSV-sourced match.
    """
    rows, subs = [], []
    row_id = 0
    files = sorted(glob.glob(os.path.join(json_dir, "*.json")))
    for fp in files:
        d = _json.load(open(fp))
        info = d["info"]
        match_id = os.path.splitext(os.path.basename(fp))[0]
        year = season_to_year(info.get("season"))
        full_overs = info.get("overs", 20)
        ground = info.get("venue")
        teams = info.get("teams", [])

        innings_list = d["innings"][:2]
        if len(innings_list) < 2:
            continue

        for inns_idx, inn in enumerate(innings_list, start=1):
            team_bat = inn["team"]
            team_bowl_list = [t for t in teams if t != team_bat]
            team_bowl = team_bowl_list[0] if team_bowl_list else None

            if inns_idx == 2 and inn.get("target") and "overs" in inn["target"]:
                max_balls = inn["target"]["overs"] * 6
            else:
                max_balls = full_overs * 6

            balls_bowled = 0
            wkts = 0
            runs_cum = 0

            for over in inn["overs"]:
                over_num = over["over"]
                for deliv in over["deliveries"]:
                    runs = deliv.get("runs", {}) or {}
                    score = runs.get("total", 0)
                    extras = deliv.get("extras") or {}
                    is_wide = 1 if "wides" in extras else 0
                    is_noball = 1 if "noballs" in extras else 0
                    is_legal = (is_wide == 0 and is_noball == 0)
                    out = 1 if "wickets" in deliv else 0

                    runs_cum += score
                    if out:
                        wkts += 1
                    if is_legal:
                        balls_bowled += 1

                    row_id += 1
                    rows.append({
                        "row_id": row_id, "p_match": match_id, "year": year, "ground": ground,
                        "inns": inns_idx, "over_num": over_num,
                        "team_bat": team_bat, "team_bowl": team_bowl,
                        "bat": deliv.get("batter"), "bowl": deliv.get("bowler"),
                        "score": score, "wide": is_wide, "noball": is_noball, "out": out,
                        "max_balls": max_balls, "inns_balls_rem": max_balls - balls_bowled,
                        "inns_wkts": min(wkts, 10), "inns_runs": runs_cum,
                    })

                    repl = deliv.get("replacements")
                    if repl and "match" in repl:
                        for r in repl["match"]:
                            if r.get("reason") == "impact_player":
                                subs.append({
                                    "p_match": match_id, "year": year, "inns_entered": inns_idx,
                                    "over_num": over_num, "team": r.get("team"),
                                    "player_in": r.get("in"), "player_out": r.get("out"),
                                })

    df = pd.DataFrame(rows)
    subs_df = pd.DataFrame(subs).drop_duplicates(subset=["p_match", "player_in", "team"])
    print(f"load_cricsheet_matches: {len(df)} ball rows from {df['p_match'].nunique()} matches "
          f"in {json_dir}; {len(subs_df)} impact-player substitution events found.")
    return df, subs_df


def extract_impact_subs_from_json(json_dir, match_ids=None):
    """
    Lightweight companion to load_cricsheet_matches: scans Cricsheet JSON for
    Impact Player substitution events ONLY (skips building the full
    ball-by-ball table), optionally restricted to match_ids -- a set/iterable
    of match-id strings (Cricsheet JSON filenames without the .json
    extension). Use this to pair substitution events with a richer
    ball-tracking dataset that already covers those same matches (e.g.
    Data_Premier_League_Prelims_Dataset.csv), instead of rebuilding Impact
    from the plain JSON via load_cricsheet_matches. Returns the same subs_df
    shape load_cricsheet_matches does (p_match as a string, matching the
    JSON filename -- cast it to match the other dataset's p_match dtype
    before merging, e.g. `.astype(int)` if the CSV uses integer match ids).
    """
    match_ids = set(str(m) for m in match_ids) if match_ids is not None else None
    subs = []
    for fp in sorted(glob.glob(os.path.join(json_dir, "*.json"))):
        match_id = os.path.splitext(os.path.basename(fp))[0]
        if match_ids is not None and match_id not in match_ids:
            continue
        d = _json.load(open(fp))
        year = season_to_year(d["info"].get("season"))
        for inns_idx, inn in enumerate(d["innings"][:2], start=1):
            for over in inn["overs"]:
                over_num = over["over"]
                for deliv in over["deliveries"]:
                    repl = deliv.get("replacements")
                    if repl and "match" in repl:
                        for r in repl["match"]:
                            if r.get("reason") == "impact_player":
                                subs.append({
                                    "p_match": match_id, "year": year, "inns_entered": inns_idx,
                                    "over_num": over_num, "team": r.get("team"),
                                    "player_in": r.get("in"), "player_out": r.get("out"),
                                })
    return pd.DataFrame(subs).drop_duplicates(subset=["p_match", "player_in", "team"])


def compute_teammate_baselines(raa_waa_df, subs_df):
    """
    For each Impact Player substitution event, both the WORST (minimum) and
    MEDIAN bowling Impact among the substitute's team's OTHER bowlers that
    match, and the same two summaries for batting Impact among the team's
    other batters -- "other" here excluding BOTH player_in and player_out,
    since together they now form the single roster "slot" being evaluated
    (see compute_slot_performance), not a baseline teammate to compare
    against. Worst is an unstable, single-observation floor (one unusually
    bad teammate performance inflates the sub's apparent value-add); median
    is a more representative "typical teammate" comparison. Both are kept so
    the slot's Impact can be judged against each.
    """
    rows = []
    for _, sub in subs_df.iterrows():
        m, team = sub["p_match"], sub["team"]
        excl = {sub["player_in"], sub["player_out"]}
        md = raa_waa_df[raa_waa_df["p_match"] == m]
        if md.empty:
            continue
        team_bowlers = (md[(md["team_bowl"] == team) & (~md["bowl"].isin(excl))]
                         .groupby("bowl")["bowl_impact"].sum())
        team_batters = (md[(md["team_bat"] == team) & (~md["bat"].isin(excl))]
                         .groupby("bat")["impact"].sum())
        rows.append({
            "p_match": m, "team": team, "player_in": sub["player_in"], "player_out": sub["player_out"],
            "worst_bowler_impact": float(team_bowlers.min()) if len(team_bowlers) else np.nan,
            "mean_bowler_impact": float(team_bowlers.mean()) if len(team_bowlers) else np.nan,
            "worst_batter_impact": float(team_batters.min()) if len(team_batters) else np.nan,
            "mean_batter_impact": float(team_batters.mean()) if len(team_batters) else np.nan,
        })
    return pd.DataFrame(rows)


def resolve_substitute_names(subs_df, raa_waa_df, cutoff=0.82):
    """
    Cricsheet's `replacements` block names players in the short "initials +
    surname" form Cricsheet uses everywhere (e.g. "TU Deshpande"), but a
    richer ball-tracking dataset such as Data_Premier_League_Prelims_Dataset.csv
    may instead use full names ("Tushar Deshpande") for `bat`/`bowl`. Left
    unresolved, most player_in/player_out lookups against such a dataset
    silently fail and get dropped as "never appears" -- this is why an
    earlier run of this pipeline against that CSV only matched 90 of the
    ~270 actual substitution events.

    Resolves player_in/player_out to whatever convention raa_waa_df's own
    bat/bowl columns use, matching PER MATCH against just that match's own
    ~13-22-player roster (reusing match_player_names, built originally for
    the auction-price join) rather than globally -- far fewer candidates to
    confuse, and unambiguous even when two different players share a similar
    name in different matches. Returns (resolved_subs_df, unresolved), where
    unresolved is a list of (p_match, name) pairs that couldn't be matched
    even fuzzily, so they can be inspected/fixed by hand if it matters.
    """
    out = subs_df.copy()
    unresolved = []
    resolved_in, resolved_out = [], []
    for _, row in out.iterrows():
        m = row["p_match"]
        roster = set(raa_waa_df.loc[raa_waa_df["p_match"] == m, "bat"]).union(
                 raa_waa_df.loc[raa_waa_df["p_match"] == m, "bowl"])
        mapping, unmatched = match_player_names({row["player_in"], row["player_out"]}, roster, cutoff=cutoff)
        resolved_in.append(mapping.get(row["player_in"], row["player_in"]))
        resolved_out.append(mapping.get(row["player_out"], row["player_out"]))
        unresolved.extend((m, n) for n in unmatched)
    out["player_in_raw"] = out["player_in"]
    out["player_out_raw"] = out["player_out"]
    out["player_in"] = resolved_in
    out["player_out"] = resolved_out
    return out, unresolved


def _player_contribution(md, player):
    """One player's total batting and bowling Impact/balls/runs in match-frame md."""
    bat_balls = md[md["bat"] == player]
    bowl_balls = md[md["bowl"] == player]
    return {
        "bat_balls": len(bat_balls), "bat_runs": int(bat_balls["score"].sum()) if len(bat_balls) else 0,
        "batting_impact": float(bat_balls["impact"].sum()) if len(bat_balls) else 0.0,
        "bowl_balls": len(bowl_balls), "bowl_runs": int(bowl_balls["score"].sum()) if len(bowl_balls) else 0,
        "bowl_wkts": int(bowl_balls["out"].sum()) if len(bowl_balls) else 0,
        "bowling_impact": float(bowl_balls["bowl_impact"].sum()) if len(bowl_balls) else 0.0,
    }


def compute_slot_performance(raa_waa_df, subs_df):
    """
    For every Impact Player substitution event, this treats player_out (who
    left) and player_in (the Impact Player who replaced them) as ONE roster
    "slot" for the whole match, and sums their contributions: combined
    batting Impact = player_out's batting Impact + player_in's batting
    Impact, and likewise for bowling Impact. This answers "how much value
    did this roster slot produce across the whole game", rather than judging
    the substitute alone -- a team that used the Impact Player to swap in a
    death-overs bowler for a part-timer who'd already bowled two expensive
    overs, for instance, should be judged on the two bowlers' combined
    figures, not the substitute's alone.

    slot_role ("Bowling slot" / "Batting slot") is picked from whichever
    activity the pair combined did more of (by balls), and slot_impact is
    the corresponding combined Impact figure. The slot is then compared to
    both the WORST and the MEDIAN other player on the team in that same
    role that match (see compute_teammate_baselines), giving
    slot_impact_vs_worst and slot_impact_vs_median.

    team_role records whether the substitute's team batted first or second
    in that match (independent of which innings the substitution itself
    happened in). Only substitution events whose match survived
    get_clean_matches are meaningful here, since raa_waa_df/Impact is only
    computed for clean matches.
    """
    match_team = raa_waa_df.drop_duplicates(subset=["p_match", "inns"])[["p_match", "inns", "team_bat"]]
    team1 = match_team[match_team["inns"] == 1].set_index("p_match")["team_bat"]

    rows = []
    for _, sub in subs_df.iterrows():
        m = sub["p_match"]
        md = raa_waa_df[raa_waa_df["p_match"] == m]
        if md.empty:
            continue  # match not present in raa_waa_df (e.g. dropped as rain/DLS-affected)

        c_in = _player_contribution(md, sub["player_in"])
        c_out = _player_contribution(md, sub["player_out"])
        if (c_in["bat_balls"] + c_in["bowl_balls"] + c_out["bat_balls"] + c_out["bowl_balls"]) == 0:
            continue  # neither player appears at all (rare name-mismatch edge case)

        batted_first = team1.get(m) == sub["team"] if m in team1.index else np.nan
        team_role = ("Batted 1st" if batted_first is True
                      else "Batted 2nd" if batted_first is False else "Unknown")

        rows.append({
            "p_match": m, "year": sub["year"], "team": sub["team"],
            "player_in": sub["player_in"], "player_out": sub["player_out"],
            "inns_entered": sub["inns_entered"], "team_role": team_role,
            "bat_balls_in": c_in["bat_balls"], "batting_impact_in": c_in["batting_impact"],
            "bowl_balls_in": c_in["bowl_balls"], "bowling_impact_in": c_in["bowling_impact"],
            "bat_balls_out": c_out["bat_balls"], "batting_impact_out": c_out["batting_impact"],
            "bowl_balls_out": c_out["bowl_balls"], "bowling_impact_out": c_out["bowling_impact"],
        })

    perf = pd.DataFrame(rows)
    if perf.empty:
        return perf

    perf["combined_batting_impact"] = perf["batting_impact_in"] + perf["batting_impact_out"]
    perf["combined_bowling_impact"] = perf["bowling_impact_in"] + perf["bowling_impact_out"]
    perf["combined_bat_balls"] = perf["bat_balls_in"] + perf["bat_balls_out"]
    perf["combined_bowl_balls"] = perf["bowl_balls_in"] + perf["bowl_balls_out"]

    perf["slot_role"] = np.where(perf["combined_bowl_balls"] >= perf["combined_bat_balls"],
                                  np.where(perf["combined_bowl_balls"] > 0, "Bowling slot", "Batting slot"),
                                  "Batting slot")
    perf["slot_impact"] = np.where(perf["slot_role"] == "Bowling slot",
                                    perf["combined_bowling_impact"], perf["combined_batting_impact"])

    baselines = compute_teammate_baselines(raa_waa_df, subs_df)
    perf = perf.merge(baselines, on=["p_match", "team", "player_in", "player_out"], how="left")

    perf["baseline_worst"] = np.where(perf["slot_role"] == "Bowling slot",
                                       perf["worst_bowler_impact"], perf["worst_batter_impact"])
    perf["baseline_mean"] = np.where(perf["slot_role"] == "Bowling slot",
                                      perf["mean_bowler_impact"], perf["mean_batter_impact"])
    perf["slot_impact_vs_worst"] = perf["slot_impact"] - perf["baseline_worst"]
    perf["slot_impact_vs_mean"] = perf["slot_impact"] - perf["baseline_mean"]
    perf["batting_slot_impact_vs_mean"] = (
        perf["combined_batting_impact"] - perf["mean_batter_impact"]
    )
    perf["bowling_slot_impact_vs_mean"] = (
        perf["combined_bowling_impact"] - perf["mean_bowler_impact"]
    )
    perf["substitution_effect"] = (
        perf["batting_slot_impact_vs_mean"] + perf["bowling_slot_impact_vs_mean"]
    )
    return perf


def filter_impact_subs_by_year(perf, year_min=None, year_max=None):
    """Restrict the impact-player performance table to a year range, inclusive on both ends."""
    out = perf.copy()
    if year_min is not None:
        out = out[out["year"] >= year_min]
    if year_max is not None:
        out = out[out["year"] <= year_max]
    return out.reset_index(drop=True)


def summarize_impact_subs(perf, year_min=None, year_max=None):
    """
    Returns a dict of summary tables over the (optionally year-filtered)
    slot-performance table:
      - by_year_slotrole: mean combined slot Impact by (year, slot_role)
      - vs_worst_by_year_teamrole / vs_median_by_year_teamrole: mean & median
        slot_impact_vs_worst / slot_impact_vs_median by (year, team_role) --
        the clearest tables for "does the rule benefit teams batting second
        more", now measured relative to what the team's weakest / typical
        other option in that role produced
      - vs_worst_by_slotrole / vs_median_by_slotrole: the same, pooled
        across years, by the slot's role (Bowling slot vs Batting slot)
    """
    p = filter_impact_subs_by_year(perf, year_min, year_max)
    out = {}
    out["by_year_slotrole"] = p.groupby(["year", "slot_role"]).agg(
        n=("p_match", "size"), mean_slot_impact=("slot_impact", "mean"),
        mean_baseline_worst=("baseline_worst", "mean"), mean_baseline_mean=("baseline_mean", "mean"),
    ).round(3)
    out["vs_worst_by_year_teamrole"] = p.groupby(["year", "team_role"]).agg(
        n=("p_match", "size"), mean_slot_impact_vs_worst=("slot_impact_vs_worst", "mean"),
        median_slot_impact_vs_worst=("slot_impact_vs_worst", "median"),
    ).round(3)
    out["vs_mean_by_year_teamrole"] = p.groupby(["year", "team_role"]).agg(
        n=("p_match", "size"), mean_slot_impact_vs_mean=("slot_impact_vs_mean", "mean"),
        median_slot_impact_vs_mean=("slot_impact_vs_mean", "median"),
    ).round(3)
    out["vs_worst_by_slotrole"] = p.groupby("slot_role").agg(
        n=("p_match", "size"), mean_slot_impact_vs_worst=("slot_impact_vs_worst", "mean"),
        median_slot_impact_vs_worst=("slot_impact_vs_worst", "median"),
    ).round(3)
    out["vs_mean_by_slotrole"] = p.groupby("slot_role").agg(
        n=("p_match", "size"), mean_slot_impact_vs_mean=("slot_impact_vs_mean", "mean"),
        median_slot_impact_vs_mean=("slot_impact_vs_mean", "median"),
    ).round(3)
    out["both_categories_by_year_teamrole"] = p.groupby(["year", "team_role"]).agg(
        n=("p_match", "size"),
        mean_batting_slot_impact_vs_mean=("batting_slot_impact_vs_mean", "mean"),
        mean_bowling_slot_impact_vs_mean=("bowling_slot_impact_vs_mean", "mean"),
        mean_substitution_effect=("substitution_effect", "mean"),
    ).round(3)
    return out


def run_impact_player_pipeline(json_dir, outdir="dl_pro_out_impact_player", fix_n0=1.04, impact_clip=20.0,
                                year_min=None, year_max=None):
    """
    End-to-end: load raw Cricsheet JSON, fit the DL Pro curve, compute
    Impact, find every Impact Player substitution, and summarize/plot the
    substitute's batting Impact and bowling Impact by year and by which
    innings they entered -- restricted to [year_min, year_max] inclusive if
    given (the Impact Player rule only exists from 2023 onward; pass e.g.
    year_min=2023, year_max=2024 to match a 2023-2024-only source dataset).
    """
    os.makedirs(outdir, exist_ok=True)
    plot_dir = os.path.join(outdir, "plots")
    os.makedirs(plot_dir, exist_ok=True)

    df, subs_df = load_cricsheet_matches(json_dir)

    clean_matches = get_clean_matches(df)
    report_dropped_matches(df, clean_matches)
    df = df[df["p_match"].isin(clean_matches)].copy()
    subs_df = subs_df[subs_df["p_match"].isin(clean_matches)].copy()

    state = build_state_table(df)
    fit_data = state[state["inns"] == 1].copy()
    print(f"Fitting DL curves on {len(fit_data)} balls from {fit_data['p_match'].nunique()} first innings.")
    aic_table, best_std, _ = select_best_by_aic(fit_data, p_degrees=(1, 2, 3), q_degrees=(1, 2))
    print(aic_table.to_string(index=False))

    S1 = first_innings_scores(df)
    R0 = best_std["R0"]
    R_std_full = R0 * best_std["F_func"](np.array([0.0]), *best_std["F_coefs"])[0] * (
        1 - np.exp(-120.0 / np.clip(best_std["beta_func"](np.array([0.0]), *best_std["beta_coefs"])[0], 1e-3, None))
    )
    n0_best = float(fix_n0)
    lam_map = {
        m: solve_lambda(S1.loc[m], R0, best_std["F_func"], best_std["F_coefs"],
                         best_std["beta_func"], best_std["beta_coefs"], n0_best, R_std_full)
        for m in S1.index
    }

    raa_waa_df, _ = compute_raa_waa(df)
    impact_df = compute_impact(df, best_std, n0_best, lam_map, impact_clip=impact_clip)
    raa_waa_df = raa_waa_df.merge(impact_df[["row_id", "impact", "bowl_impact"]], on="row_id", how="left")

    perf = compute_slot_performance(raa_waa_df, subs_df)
    perf_filtered = filter_impact_subs_by_year(perf, year_min, year_max)
    print(f"\n{len(perf)} substitution events matched to on-field performance "
          f"({len(perf_filtered)} within year range {year_min}-{year_max}).")

    summaries = summarize_impact_subs(perf, year_min=year_min, year_max=year_max)
    print("\n=== Mean combined slot Impact by year x slot role ===")
    print(summaries["by_year_slotrole"].to_string())
    print("\n=== Combined slot Impact vs. WORST same-role teammate, by year x team_role ===")
    print(summaries["vs_worst_by_year_teamrole"].to_string())
    print("\n=== Combined slot Impact vs. WORST same-role teammate, by slot role (pooled) ===")
    print(summaries["vs_worst_by_slotrole"].to_string())
    print("\n=== Combined slot Impact vs. MEAN same-role teammate, by year x team_role ===")
    print(summaries["vs_mean_by_year_teamrole"].to_string())
    print("\n=== Combined slot Impact vs. MEAN same-role teammate, by slot role (pooled) ===")
    print(summaries["vs_mean_by_slotrole"].to_string())

    perf_filtered.to_csv(os.path.join(outdir, "impact_player_performance.csv"), index=False)
    for name, table in summaries.items():
        table.to_csv(os.path.join(outdir, f"impact_player_{name}.csv"))

    plot_impact_sub_distribution(perf, os.path.join(plot_dir, "impact_sub_distribution"),
                                  year_min=year_min, year_max=year_max)

    print(f"\nSaved impact-player tables and plot to: {outdir}/")
    return perf, perf_filtered, summaries, raa_waa_df


# --------------------------------------------------------------------------
# CONFIG -- edit these and run `python dl_pro_fit.py`, no CLI flags needed.
# CSV_CONFIG drives the Section-7 physical-attribute pipeline (needs a ball-
# tracking CSV like Data_Premier_League_Prelims_Dataset.csv: line, length,
# bowl_speed_category, shot, control, wagon coordinates, etc.). When
# impact_player_json_dir also points at a folder of raw Cricsheet match JSON
# covering the SAME matches as the CSV (substitution events aren't in the
# CSV itself), main() also runs the Impact Player vs. median-teammate
# analysis and drops its plot into the exact same outdir/plots/ folder as
# everything else -- one dataset, one set of Impact numbers, one folder of
# visuals.
#
# IMPACT_PLAYER_CONFIG is a fallback for when there's no ball-tracking CSV
# at all: it drives the Section-8 standalone pipeline, which computes Impact
# straight from raw Cricsheet JSON (runs/wickets/balls only -- no physical
# attributes, and no bowling-variation-regression, since that needs line/
# length/speed) across every match in json_dir rather than just the ones a
# CSV happens to cover.
# --------------------------------------------------------------------------

CSV_CONFIG = {
    "csv_path": "Data_Premier_League_Prelims_Dataset.csv",
    "outdir": "dl_pro_out",
    "fix_n0": 1.04,
    "impact_clip": 20.0,
    "min_balls_bat": 100,
    "min_balls_bowl": 100,
    "min_balls_season": 100,
    "min_balls_team": 200,
    "min_matches_venue": 4,
    "top_n_plot": 20,
    "auction_xlsx_path": "IPL_Auction_2023_24.xlsx",
    "money_unit": "crore",
    "min_balls_auction": 100,
    "name_match_cutoff": 0.82,
    "min_count_variation": 20,
    "variation_include_speed": True,   # adds bowl_speed_category as a 3rd distance dimension + regression
    "impact_player_json_dir": "ipl_json",  # raw Cricsheet JSON covering the CSV's matches; None to skip
    "impact_player_year_min": 2023,
    "impact_player_year_max": 2024,
}

IMPACT_PLAYER_CONFIG = {
    "json_dir": "ipl_json",
    "outdir": "dl_pro_out_impact_player",
    "fix_n0": 1.04,
    "impact_clip": 20.0,
    "year_min": 2023,
    "year_max": 2024,
}

if __name__ == "__main__":
    if os.path.exists(CSV_CONFIG["csv_path"]):
        main(**CSV_CONFIG)
    elif os.path.isdir(IMPACT_PLAYER_CONFIG["json_dir"]):
        print(f"CSV_CONFIG['csv_path']={CSV_CONFIG['csv_path']!r} not found; "
              f"falling back to the JSON-only Section 8 pipeline.")
        run_impact_player_pipeline(**IMPACT_PLAYER_CONFIG)
    else:
        print("Neither CSV_CONFIG['csv_path'] nor IMPACT_PLAYER_CONFIG['json_dir'] were found; nothing to run.")

"""
plotnine versions of every matplotlib plot in dl_pro_fit.py.

Every function keeps the SAME name and signature as its matplotlib original, so
generate_all_plots(), main(), run_impact_player_pipeline() and any ad-hoc calls keep
working untouched. To use: put this file next to dl_pro_fit.py and add

    from dl_pro_plots import *      # overrides the matplotlib plot_* functions

just above the CONFIG block at the bottom of dl_pro_fit.py (names bound later win, and
main() looks them up at call time). Once you're happy you can delete the old matplotlib
functions, the `_barh` helper, and the matplotlib imports from dl_pro_fit.py.

Deliberately left in matplotlib:
  * plot_wagon_impact_gradient  -- the hexagon (hexbin) heatmap of mean Impact by shot location
  * plot_wagon_zone_pies        -- the per-year pie charts of wagon-wheel zone shares
    (plotnine has no coord_polar and no mean-valued hexbin.)

Changed shape in the plotnine port:
  * plot_fit_diagnostics    -- the two side-by-side matplotlib axes are one faceted plot.
"""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt   # only used by the two wagon plots below
from plotnine import (
    ggplot, aes, geom_col, geom_point, geom_line, geom_text, geom_hline, geom_vline, geom_tile,
    geom_histogram, geom_path, coord_flip, coord_fixed, facet_wrap, labs, theme, theme_bw,
    theme_minimal, element_text, element_blank, scale_fill_manual, scale_color_manual,
    scale_fill_gradient2, scale_color_cmap, scale_size_continuous, scale_x_continuous,
    position_dodge, after_stat,
)

__all__ = [
    "plot_top_n_bar", "plot_raa_vs_waa", "plot_impact_distribution", "plot_line_length_heatmap",
    "plot_physical_bar", "plot_physical_bar_by_year", "plot_line_length_heatmap_by_year",
    "plot_season_trajectory", "plot_season_movers_bar", "plot_venue_factor",
    "plot_impact_per_rupee_leaderboard", "plot_price_vs_impact", "plot_variation_regression_coefs",
    "plot_variation_impact", "plot_variation_impact_by_speed", "plot_wagon_impact_gradient",
    "plot_wagon_zone_pies", "plot_single_player_season", "plot_zone_year_comparison",
    "plot_marginal_impact_by_shot", "plot_team_impact_per100", "plot_zone_quadrant",
    "plot_shot_quadrant", "plot_shot_year_comparison", "plot_fit_diagnostics",
    "plot_impact_sub_distribution",
    "plot_impact_sub_distribution_by_year_role",
]

BLUE, RED, GREEN = "#3366cc", "#cc3333", "#33aa55"
LINE_ORDER = ["DOWN_LEG", "WIDE_DOWN_LEG", "ON_THE_STUMPS", "OUTSIDE_OFFSTUMP", "WIDE_OUTSIDE_OFFSTUMP"]
LENGTH_ORDER = ["FULL_TOSS", "YORKER", "FULL", "GOOD_LENGTH", "SHORT_OF_A_GOOD_LENGTH", "SHORT"]
_SPEED_LABELS = {1: ">140 kph", 2: "130-140", 3: "120-130", 4: "110-120",
                 5: "100-110", 6: "85-100", 7: "70-85", 8: "<70 kph"}
_WAGON_CENTER = 181.5


# ---------------------------------------------------------------- helpers
def _base(w, h):
    return theme_bw() + theme(figure_size=(w, h))


def _save(p, outpath):
    p.save(outpath, dpi=140, verbose=False)


def _uniq(labels):
    seen, out = {}, []
    for l in labels:
        seen[l] = seen.get(l, 0) + 1
        out.append(l if seen[l] == 1 else f"{l} #{seen[l]}")
    return out


def _yr(s):
    return pd.Series(s).astype(int).astype(str).to_numpy()


def _hbar(labels, values, title, xlabel, figsize, color=BLUE, by_sign=False, ref=0.0, ge=False):
    """Horizontal bars, largest at the top (matplotlib's _barh ordering). by_sign: blue above ref, red below."""
    t = pd.DataFrame({"label": _uniq([str(l) for l in labels]), "value": np.asarray(values, dtype=float)})
    t = t.sort_values("value", kind="stable").reset_index(drop=True)
    t["label"] = pd.Categorical(t["label"], categories=t["label"].tolist(), ordered=True)
    if by_sign:
        t["sign"] = np.where(t["value"] >= ref if ge else t["value"] > ref, "up", "down")
        p = (ggplot(t, aes("label", "value", fill="sign")) + geom_col()
             + scale_fill_manual(values={"up": BLUE, "down": RED}))
    else:
        p = ggplot(t, aes("label", "value")) + geom_col(fill=color)
    return (p + geom_hline(yintercept=ref) + coord_flip()
            + labs(title=title, x="", y=xlabel) + _base(*figsize) + theme(legend_position="none"))


def _long_by_year(rows_by_year):
    return pd.concat(rows_by_year, ignore_index=True)


# ---------------------------------------------------------------- leaderboards / scatter
def plot_top_n_bar(summary, value_col, title, xlabel, outpath, top_n=20, color=BLUE):
    top = summary.sort_values(value_col, ascending=False).head(top_n)
    _save(_hbar(top.index.tolist(), top[value_col].to_numpy(), title, xlabel,
                (8, max(4, 0.35 * len(top))), color=color), outpath)


def plot_raa_vs_waa(summary, title, outpath, size_col="balls"):
    t = summary.copy()
    t["name"] = t.index.astype(str)
    has_imp = "Impact" in t.columns
    mapping = dict(x="RAA", y="WAA", size=size_col)
    if has_imp:
        mapping["color"] = "Impact"
    p = (ggplot(t, aes(**mapping)) + geom_hline(yintercept=0, color="gray") + geom_vline(xintercept=0, color="gray")
         + geom_point(alpha=0.6) + scale_size_continuous(range=(1.5, 7)))
    if has_imp:
        p = p + scale_color_cmap(cmap_name="viridis")
    lab = t.sort_values("Impact" if has_imp else "RAA", ascending=False).head(8)
    p = (p + geom_text(data=lab, mapping=aes(x="RAA", y="WAA", label="name"), inherit_aes=False,
                       size=7, ha="left", va="bottom")
         + labs(title=title, x="RAA (runs above average)", y="WAA (wicket-preservation above average)")
         + _base(7.5, 6))
    _save(p, outpath)


def plot_impact_distribution(impact_df, outpath):
    wkt = impact_df.loc[impact_df["out"] == 1, "impact"]
    non = impact_df.loc[impact_df["out"] == 0, "impact"]
    l_non, l_wkt = f"Non-wicket balls (n={len(non)})", f"Wicket balls (n={len(wkt)})"
    t = pd.concat([pd.DataFrame({"impact": non.to_numpy(), "group": l_non}),
                   pd.DataFrame({"impact": wkt.to_numpy(), "group": l_wkt})], ignore_index=True)
    means = pd.DataFrame({"m": [non.mean(), wkt.mean()], "group": [l_non, l_wkt]})
    cols = {l_non: BLUE, l_wkt: RED}
    p = (ggplot(t, aes("impact", fill="group"))
         + geom_histogram(aes(y=after_stat("density")), bins=60, alpha=0.6, position="identity")
         + geom_vline(data=means, mapping=aes(xintercept="m", color="group"), inherit_aes=False, linetype="dashed")
         + scale_fill_manual(values=cols) + scale_color_manual(values=cols)
         + labs(title="Impact distribution: wicket vs. non-wicket balls", x="Per-ball batting Impact",
                y="Density", fill="", color="")
         + _base(7, 5) + theme(legend_position="bottom"))
    _save(p, outpath)


def plot_line_length_heatmap(ll_table, outpath):
    t = ll_table.copy()
    rows = [r for r in LENGTH_ORDER if r in set(t["length"])] + [r for r in t["length"].unique() if r not in LENGTH_ORDER]
    cols = [c for c in LINE_ORDER if c in set(t["line"])] + [c for c in t["line"].unique() if c not in LINE_ORDER]
    t["length"] = pd.Categorical(t["length"], categories=rows[::-1], ordered=True)   # first row at the top
    t["line"] = pd.Categorical(t["line"], categories=cols, ordered=True)
    t["lab"] = t["bowl_impact"].map("{:.2f}".format)
    vmax = float(t["bowl_impact"].abs().max())
    p = (ggplot(t, aes("line", "length", fill="bowl_impact")) + geom_tile(color="white")
         + geom_text(aes(x="line", y="length", label="lab"), inherit_aes=False, size=7)
         + scale_fill_gradient2(low="#b2182b", mid="white", high="#2166ac", midpoint=0, limits=(-vmax, vmax))
         + labs(title="Mean bowling Impact per ball, by line & length\n(positive = good for bowler)",
                x="", y="", fill="Mean bowl_impact")
         + _base(8, 5.5) + theme(axis_text_x=element_text(rotation=40, ha="right", size=8),
                                 axis_text_y=element_text(size=8), panel_grid=element_blank()))
    _save(p, outpath)


def plot_physical_bar(table, x_col, y_col, title, xlabel, ylabel, outpath, order=None):
    t = table.copy()
    t[x_col] = t[x_col].astype(str)
    if order is not None:
        cats = [o for o in order if o in set(t[x_col])]
        t = t[t[x_col].isin(cats)]
    else:
        cats = list(dict.fromkeys(t[x_col]))
    t[x_col] = pd.Categorical(t[x_col], categories=cats, ordered=True)
    p = (ggplot(t, aes(x_col, y_col)) + geom_col(fill=BLUE) + geom_hline(yintercept=0)
         + labs(title=title, x=xlabel, y=ylabel) + _base(7, 5)
         + theme(axis_text_x=element_text(rotation=30, ha="right")))
    _save(p, outpath)

def plot_physical_bar_by_year(
    table,
    x_col,
    y_col,
    title,
    xlabel,
    ylabel,
    outpath,
    order=None,
    years=(2023, 2024),
):
    """
    Compare a physical delivery attribute across years.

    Bars are grouped by attribute category, with separate bars for each year.
    """
    t = table.copy()

    t = t[t["year"].isin(years)].copy()
    if t.empty:
        return

    t[x_col] = t[x_col].astype(str)
    t["Year"] = t["year"].astype(int).astype(str)

    if order is not None:
        cats = [str(o) for o in order if str(o) in set(t[x_col])]
        t = t[t[x_col].isin(cats)]
    else:
        cats = list(dict.fromkeys(t[x_col]))

    t[x_col] = pd.Categorical(
        t[x_col],
        categories=cats,
        ordered=True,
    )

    p = (
        ggplot(
            t,
            aes(
                x=x_col,
                y=y_col,
                fill="Year",
            ),
        )
        + geom_col(
            position=position_dodge(width=0.8),
            width=0.75,
        )
        + geom_hline(yintercept=0)
        + labs(
            title=title,
            x=xlabel,
            y=ylabel,
            fill="Year",
        )
        + _base(8, 5.5)
        + theme(
            axis_text_x=element_text(
                rotation=30,
                ha="right",
            ),
            legend_position="bottom",
        )
    )

    _save(p, outpath)


def plot_line_length_heatmap_by_year(
    ll_table,
    outpath,
    years=(2023, 2024),
):
    """
    Compare mean bowling Impact by line × length separately for 2023 and 2024.
    Uses one facet per year and a shared color scale.
    """
    t = ll_table.copy()
    t = t[t["year"].isin(years)].copy()

    if t.empty:
        return

    rows = (
        [r for r in LENGTH_ORDER if r in set(t["length"])]
        + [r for r in t["length"].unique()
           if r not in LENGTH_ORDER]
    )

    cols = (
        [c for c in LINE_ORDER if c in set(t["line"])]
        + [c for c in t["line"].unique()
           if c not in LINE_ORDER]
    )

    t["length"] = pd.Categorical(
        t["length"],
        categories=rows[::-1],
        ordered=True,
    )

    t["line"] = pd.Categorical(
        t["line"],
        categories=cols,
        ordered=True,
    )

    t["Year"] = t["year"].astype(int).astype(str)
    t["lab"] = t["bowl_impact"].map("{:.2f}".format)

    vmax = float(t["bowl_impact"].abs().max())

    p = (
        ggplot(
            t,
            aes(
                "line",
                "length",
                fill="bowl_impact",
            ),
        )
        + geom_tile(color="white")
        + geom_text(
            aes(
                x="line",
                y="length",
                label="lab",
            ),
            inherit_aes=False,
            size=6,
        )
        + scale_fill_gradient2(
            low="#b2182b",
            mid="white",
            high="#2166ac",
            midpoint=0,
            limits=(-vmax, vmax),
        )
        + facet_wrap("~Year", nrow=1)
        + labs(
            title="Mean bowling Impact by line & length: 2023 vs. 2024",
            x="Line",
            y="Length",
            fill="Mean bowl_impact",
        )
        + _base(12, 5.5)
        + theme(
            axis_text_x=element_text(
                rotation=40,
                ha="right",
                size=8,
            ),
            axis_text_y=element_text(size=8),
            panel_grid=element_blank(),
        )
    )

    _save(p, outpath)


# ---------------------------------------------------------------- season-level
def plot_season_trajectory(season_summary, players, player_col, value_col, title, ylabel, outpath):
    t = season_summary[season_summary[player_col].isin(players)].copy()
    if t.empty:
        return
    t["player"] = t[player_col].astype(str)
    t = t.sort_values(["player", "year"])
    last = t.groupby("player").tail(1)
    years = sorted(int(y) for y in season_summary["year"].unique())
    p = (ggplot(t, aes("year", value_col, color="player", group="player"))
         + geom_hline(yintercept=0, color="gray") + geom_line(size=1) + geom_point(size=2.5)
         + geom_text(data=last, mapping=aes(x="year", y=value_col, label="player", color="player"),
                     inherit_aes=False, size=8, ha="left", nudge_x=0.05)
         + scale_x_continuous(breaks=years, expand=(0.05, 0, 0.35, 0))
         + labs(title=title, x="Year", y=ylabel) + _base(7.5, 5.5) + theme(legend_position="none"))
    _save(p, outpath)


def plot_season_movers_bar(changes, player_col, title, outpath, top_n=8):
    if changes.empty:
        return
    combo = pd.concat([changes.head(top_n), changes.tail(top_n)]).drop_duplicates(subset=player_col)
    labels = [f"{r[player_col]} ({r['year_from']}\u2192{r['year_to']})" for _, r in combo.iterrows()]
    _save(_hbar(labels, combo["change"].to_numpy(), title, "Change in Impact per ball",
                (8, max(4, 0.35 * len(combo))), by_sign=True), outpath)


def plot_single_player_season(season_summary, player, player_col, outpath, title=None):
    sub = season_summary[season_summary[player_col] == player].sort_values("year")
    if sub.empty:
        print(f"No qualifying seasons found for {player!r} (check spelling / min_balls_season).")
        return
    long = sub.melt(id_vars=["year", "balls"], value_vars=["RAA_per_ball", "impact_per_ball"],
                    var_name="metric", value_name="value")
    long["metric"] = long["metric"].map({"RAA_per_ball": "RAA per ball", "impact_per_ball": "Impact per ball"})
    ann = sub.assign(lab=[f"{b:.0f} balls" for b in sub["balls"]])
    span = float(long["value"].max() - long["value"].min()) or 1.0
    p = (ggplot(long, aes("year", "value", color="metric"))
         + geom_hline(yintercept=0, color="gray") + geom_line() + geom_point()
         + geom_text(data=ann, mapping=aes(x="year", y="impact_per_ball", label="lab"), inherit_aes=False,
                     size=7, va="top", nudge_y=-0.04 * span)
         + scale_x_continuous(breaks=[int(y) for y in sub["year"]])
         + scale_color_manual(values={"RAA per ball": BLUE, "Impact per ball": "#ff7f0e"})
         + labs(title=title or f"{player}: season-by-season", x="Year", y="Per-ball value", color="")
         + _base(6.5, 5))
    _save(p, outpath)
    print(f"Saved {outpath}")


# ---------------------------------------------------------------- venue / auction
def plot_venue_factor(venue_table, outpath, min_matches=3):
    t = venue_table[venue_table["reliable"]]
    if t.empty:
        return
    p = _hbar(t["ground"].tolist(), t["venue_factor"].to_numpy(),
              f"Venue scoring environment (grounds with \u2265{min_matches} matches)\n"
              f"blue = hitter-friendly, red = bowler-friendly",
              "Venue factor (mean runs/ball at ground \u00f7 league mean)",
              (10, max(4, 0.4 * len(t))), by_sign=True, ref=1.0, ge=True)
    _save(p, outpath)


def plot_impact_per_rupee_leaderboard(table, outpath, min_balls=100, top_n=15, money_unit="crore"):
    t = table[table["total_balls"] >= min_balls]
    if t.empty:
        return
    top = pd.concat([t.head(top_n), t.tail(top_n)]).drop_duplicates(subset=["player", "year"])
    labels = [f"{r['player']} ({int(r['year'])})" for _, r in top.iterrows()]
    value_col = "impact_vs_mean_per_unit_money" if "impact_vs_mean_per_unit_money" in top else "impact_per_unit_money"
    ylabel = f"Impact vs role mean per ball per {money_unit} spent" if value_col != "impact_per_unit_money" else f"Impact per {money_unit} spent"
    _save(_hbar(labels, top[value_col].to_numpy(),
                f"Best and worst auction value vs role mean (min {min_balls} balls)", ylabel,
                (9, max(5, 0.32 * len(top))), by_sign=True), outpath)


def plot_price_vs_impact(table, outpath, min_balls=100, money_unit="crore"):
    t = table[table["total_balls"] >= min_balls].copy()
    if t.empty:
        return
    t["Year"] = _yr(t["year"])
    ranked = t.sort_values("impact_per_unit_money", ascending=False)
    ext = pd.concat([ranked.head(4), ranked.tail(4)]).drop_duplicates(subset=["player", "year"])
    ext = ext.assign(lab=[f"{r['player']} ({int(r['year'])})" for _, r in ext.iterrows()])
    y_col = "impact_vs_mean_per_ball" if "impact_vs_mean_per_ball" in t else "total_impact"
    y_label = "Impact vs role mean per ball" if y_col != "total_impact" else "Total season Impact"
    p = (ggplot(t, aes("price_in_unit", y_col, color="Year", shape="Year"))
         + geom_hline(yintercept=0, color="gray") + geom_point(alpha=0.6, size=2.5)
         + geom_text(data=ext, mapping=aes(x="price_in_unit", y=y_col, label="lab"),
                     inherit_aes=False, size=7, ha="left", nudge_x=0.05)
         + labs(title=f"Price vs. Impact (min {min_balls} balls)", x=f"Price paid ({money_unit})",
                y=y_label) + _base(8.5, 7))
    _save(p, outpath)


# ---------------------------------------------------------------- delivery variation
def plot_variation_regression_coefs(table, outpath, standardize=True):
    t = table[table["term"] != "intercept"].copy()
    if t.empty:
        return
    labels = {"delta_length": "Length change", "delta_line": "Line change", "delta_speed": "Speed change"}
    t["Term"] = pd.Categorical(t["term"].map(labels), categories=list(labels.values()), ordered=True)
    t["Year"] = _yr(t["year"])
    unit = "standardized coefficient, per 1-SD change" if standardize else "coefficient, per category-step change"
    p = (ggplot(t, aes("Year", "coef", fill="Term"))
         + geom_col(position=position_dodge(width=0.8), width=0.7) + geom_hline(yintercept=0)
         + scale_fill_manual(values={"Length change": BLUE, "Line change": RED, "Speed change": GREEN})
         + labs(title="What kind of bowling variation actually pays off?\n"
                      "OLS: bowl_impact ~ |\u0394length| + |\u0394line| + |\u0394speed|, by year",
                x="Year", y=f"Effect on bowl_impact ({unit})", fill="") + _base(8, 5.5))
    _save(p, outpath)


def plot_variation_impact(tbl, outpath, overall_means=None, min_count=20):
    t = tbl[tbl["reliable"]].copy()
    if t.empty:
        return
    t["Year"] = _yr(t["year"])
    y_col = "mean_bowl_impact_vs_mean_per_ball" if "mean_bowl_impact_vs_mean_per_ball" in t else "mean_bowl_impact"
    y_label = "Bowling Impact vs role mean per ball" if y_col != "mean_bowl_impact" else "Mean bowling Impact per ball"
    p = (ggplot(t, aes("distance", y_col, color="Year", group="Year"))
         + geom_hline(yintercept=0) + geom_line() + geom_point())
    if overall_means is not None:
        h = pd.DataFrame({"Year": [str(int(y)) for y in overall_means if str(int(y)) in set(t["Year"])],
                          "m": [v for y, v in overall_means.items() if str(int(y)) in set(t["Year"])]})
        p = p + geom_hline(data=h, mapping=aes(yintercept="m", color="Year"), inherit_aes=False, linetype="dotted", alpha=0.6)
    p = (p + labs(title=f"Does bowling variation pay off? (min {min_count} balls per point)\n"
                        f"dotted line = that year's overall average bowling Impact",
                  x="Distance from previous delivery, on the line/length grid",
                          y=y_label) + _base(8.5, 6))
    _save(p, outpath)


def plot_variation_impact_by_speed(tbl, outpath, min_count=20, top_n_speeds=4):
    t = tbl[tbl["reliable"]]
    if t.empty:
        return
    parts = []
    for yr, sub in t.groupby("year"):
        keep = sub.groupby("bowl_speed_category")["balls"].sum().sort_values(ascending=False).head(top_n_speeds).index
        parts.append(sub[sub["bowl_speed_category"].isin(keep)])
    t = pd.concat(parts, ignore_index=True)
    t["Year"] = _yr(t["year"])
    cats = [_SPEED_LABELS.get(k, str(k)) for k in sorted(t["bowl_speed_category"].unique())]
    t["Speed category"] = pd.Categorical(t["bowl_speed_category"].map(lambda k: _SPEED_LABELS.get(k, str(k))),
                                         categories=cats, ordered=True)
    n_years = t["Year"].nunique()
    y_col = "mean_bowl_impact_vs_mean_per_ball" if "mean_bowl_impact_vs_mean_per_ball" in t else "mean_bowl_impact"
    y_label = "Bowling Impact vs role mean per ball" if y_col != "mean_bowl_impact" else "Mean bowling Impact per ball"
    p = (ggplot(t, aes("distance", y_col, color="Speed category", group="Speed category"))
         + geom_hline(yintercept=0) + geom_line() + geom_point()
         + facet_wrap("~Year", nrow=1)
          + labs(title=f"Bowling variation vs role mean, by year (min {min_count} balls per point)",
              x="3-D distance from previous delivery\n(length, line, speed)", y=y_label)
         + _base(6.5 * n_years, 5.5))
    _save(p, outpath)


# ---------------------------------------------------------------- wagon wheel
# These two stay in matplotlib on purpose: plotnine has no pie chart / coord_polar
# and no mean-valued hexbin, so the original hexagon heatmap and pies are kept as-is.
def plot_wagon_impact_gradient(d, outpath, gridsize=18, mincnt=12):
    """
    Hexbin heatmap of mean batting Impact by shot location, one panel per
    year, sharing one color scale so the years are visually comparable.
    Uses batter-perspective (handedness-mirrored) coordinates. (matplotlib)
    """
    years = sorted(d["year"].unique())
    fig, axes = plt.subplots(1, len(years), figsize=(6.2 * len(years) + 1.2, 6.6),
                             constrained_layout=True)
    axes = np.atleast_1d(axes)

    extent = (d["wagonx_bp"].min(), d["wagonx_bp"].max(), d["wagony_bp"].min(), d["wagony_bp"].max())

    # First pass on a throwaway axis: find the shared colour limit.
    vals = []
    for yr in years:
        sub = d[d["year"] == yr]
        hb = axes[0].hexbin(sub["wagonx_bp"], sub["wagony_bp"], C=sub["impact"], reduce_C_function=np.mean,
                            gridsize=gridsize, extent=extent, mincnt=mincnt)
        vals.append(np.asarray(hb.get_array()))
        hb.remove()
    all_vals = np.concatenate(vals) if vals else np.array([0.0])
    vlim = max(float(np.nanmax(np.abs(all_vals))) if all_vals.size else 1.0, 0.1)

    cx = cy = _WAGON_CENTER
    radius = max(d["wagonx_bp"].max() - cx, cx - d["wagonx_bp"].min(),
                 d["wagony_bp"].max() - cy, cy - d["wagony_bp"].min())
    # Hexagons overhang the data extent by up to one cell, so give the frame room for them
    # (otherwise the outermost hexes are clipped).
    half = max(radius, (extent[1] - extent[0]) / 2, (extent[3] - extent[2]) / 2) * 1.12
    mid_x = (extent[0] + extent[1]) / 2
    mid_y = (extent[2] + extent[3]) / 2

    last_hb = None
    for ax, yr in zip(axes, years):
        sub = d[d["year"] == yr]
        last_hb = ax.hexbin(sub["wagonx_bp"], sub["wagony_bp"], C=sub["impact"], reduce_C_function=np.mean,
                            gridsize=gridsize, extent=extent, mincnt=mincnt, cmap="RdBu", vmin=-vlim, vmax=vlim)
        ax.add_patch(plt.Circle((cx, cy), radius, fill=False, color="black", linewidth=1))
        ax.scatter([cx], [cy], marker="+", color="black", s=60, zorder=3)
        ax.set_xlim(mid_x - half, mid_x + half)
        ax.set_ylim(mid_y - half, mid_y + half)
        ax.set_aspect("equal", adjustable="box")
        ax.axis("off")
        ax.set_title(str(int(yr)), fontsize=12)
    fig.suptitle("Mean batting Impact by shot location\n"
                 "(batter's-eye view: LHB mirrored to RHB frame; blue = above expectation)")
    cbar = fig.colorbar(last_hb, ax=list(axes), shrink=0.75, aspect=25, pad=0.02)
    cbar.set_label("Mean batting Impact")
    fig.savefig(outpath, dpi=140)
    plt.close(fig)


def plot_wagon_zone_pies(tbl, outpath):
    """Share of shots by wagon-wheel zone, one pie per year. (matplotlib)"""
    years = sorted(tbl["year"].unique())
    fig, axes = plt.subplots(1, len(years), figsize=(6 * len(years), 6))
    if len(years) == 1:
        axes = [axes]
    colors = plt.cm.tab10(np.linspace(0, 0.8, 8))
    for ax, yr in zip(axes, years):
        sub = tbl[tbl["year"] == yr].sort_values("wagonzone_bp")
        ax.pie(sub["proportion"], labels=[f"Zone {int(z)}" for z in sub["wagonzone_bp"]],
               autopct="%1.1f%%", startangle=90, counterclock=False, colors=colors)
        ax.set_title(str(int(yr)))
    fig.suptitle("Share of shots by wagon-wheel zone, batter's-eye view (LHB mirrored to RHB frame)")
    fig.tight_layout()
    fig.savefig(outpath, dpi=140)
    plt.close(fig)


# ---------------------------------------------------------------- zone / shot analyses
def _year_facets(frames, x_order, title, xlabel_rot=35, size=(10, 9)):
    long = pd.concat(frames, ignore_index=True).dropna(subset=["value"])
    long["x"] = pd.Categorical(long["x"], categories=x_order, ordered=True)
    long["metric"] = pd.Categorical(long["metric"], categories=list(dict.fromkeys(long["metric"])), ordered=True)
    return (ggplot(long, aes("x", "value", fill="Year"))
            + geom_col(position=position_dodge(width=0.8), width=0.75) + geom_hline(yintercept=0)
            + facet_wrap("~metric", ncol=1, scales="free_y")
            + labs(title=title, x="", y="") + _base(*size)
            + theme(axis_text_x=element_text(rotation=xlabel_rot, ha="right", size=8)))


def plot_zone_year_comparison(zone_table, outpath, top_n=10):
    years = sorted(zone_table["year"].unique())
    top = zone_table.groupby("zone")["balls"].sum().sort_values(ascending=False).head(top_n).index.tolist()
    sub = zone_table[zone_table["zone"].isin(top)]
    frames = []
    for yr in years:
        s = sub[sub["year"] == yr]
        frames.append(pd.DataFrame({"x": s["zone"], "value": s["proportion"], "Year": str(int(yr)),
                                    "metric": "Share of deliveries"}))
    for yr in years:
        s = sub[sub["year"] == yr]
        frames.append(pd.DataFrame({"x": s["zone"], "value": s["mean_bowl_impact"], "Year": str(int(yr)),
                                    "metric": "Mean bowl_impact per ball"}))
    p = _year_facets(frames, top, f"Bowling zones by year (top {top_n} zones by volume)")
    _save(p, outpath)


def plot_marginal_impact_by_shot(table, outpath, top_n=25):
    t = table.sort_values("marginal_impact", ascending=False).head(top_n)
    _save(_hbar(t["shot"].tolist(), t["marginal_impact"].to_numpy(),
                "Marginal batting Impact by shot type\n(vs. reference shot, controlling for line & length)",
                "Marginal Impact per ball (runs)", (8, max(5, 0.3 * len(t)))), outpath)


def plot_team_impact_per100(team_change, outpath):
    if team_change.empty:
        return
    t = team_change.sort_values("bat_change_per100")
    teams = t["team"].tolist()
    long = pd.concat([
        pd.DataFrame({"team": t["team"], "value": t["bat_change_per100"], "Metric": "Batting Impact/100 balls, change"}),
        pd.DataFrame({"team": t["team"], "value": t["bowl_change_per100"], "Metric": "Bowling Impact/100 balls, change"}),
    ], ignore_index=True).dropna(subset=["value"])
    long["team"] = pd.Categorical(long["team"], categories=teams, ordered=True)
    p = (ggplot(long, aes("team", "value", fill="Metric"))
         + geom_col(position=position_dodge(width=0.8), width=0.7) + geom_hline(yintercept=0) + coord_flip()
         + scale_fill_manual(values={"Batting Impact/100 balls, change": BLUE, "Bowling Impact/100 balls, change": RED})
         + labs(title="Team batting & bowling Impact per 100 balls: year-over-year change",
                x="", y="Change in Impact per 100 balls", fill="")
         + _base(9, max(4, 0.5 * len(t))) + theme(legend_position="bottom"))
    _save(p, outpath)


def plot_zone_quadrant(zone_changes, outpath, n_labels=10):
    if zone_changes.empty:
        return
    t = zone_changes.copy()
    t["x"] = t["prop_change"] * 100
    t["y"] = t["impact_change"]
    t["Direction"] = np.where(t["y"] > 0, "more bowler-friendly", "less bowler-friendly")
    prop_cols = sorted(c for c in t.columns if c.startswith("prop_") and c != "prop_change")
    size_col = prop_cols[-1] if prop_cols else None
    label_set = pd.concat([
        t.reindex(t["prop_change"].abs().sort_values(ascending=False).index).head(n_labels // 2),
        t.reindex(t["impact_change"].abs().sort_values(ascending=False).index).head(n_labels // 2),
    ]).drop_duplicates(subset="zone")
    mapping = dict(x="x", y="y", color="Direction")
    if size_col:
        mapping["size"] = size_col
    p = (ggplot(t, aes(**mapping)) + geom_hline(yintercept=0, color="gray") + geom_vline(xintercept=0, color="gray")
         + geom_point(alpha=0.75)
         + geom_text(data=label_set, mapping=aes(x="x", y="y", label="zone"), inherit_aes=False,
                     size=7, ha="left", nudge_x=0.1)
         + scale_color_manual(values={"more bowler-friendly": BLUE, "less bowler-friendly": RED})
         + scale_size_continuous(range=(2, 9))
         + labs(title="Bowling zones: usage shift vs. effectiveness shift\n(blue = became more bowler-friendly)",
                x="Change in share of deliveries bowled there (pp), year over year",
                y="Change in mean bowling Impact per ball", size="Share (latest yr)")
         + _base(8.5, 7))
    _save(p, outpath)


def plot_shot_year_comparison(prop_table, marginal_table, outpath, top_n=12):
    years = sorted(prop_table["year"].unique())
    top = prop_table.groupby("shot")["balls"].sum().sort_values(ascending=False).head(top_n).index.tolist()
    ref = marginal_table["reference_shot"].iloc[0] if "reference_shot" in marginal_table and len(marginal_table) else "?"
    frames = []
    for yr in years:
        s = prop_table[(prop_table["year"] == yr) & (prop_table["shot"].isin(top))]
        frames.append(pd.DataFrame({"x": s["shot"], "value": s["proportion"], "Year": str(int(yr)),
                                    "metric": "Share of deliveries"}))
    for yr in years:
        col = f"marginal_impact_{yr}"
        if col in marginal_table.columns:
            s = marginal_table[marginal_table["shot"].isin(top)]
            frames.append(pd.DataFrame({"x": s["shot"], "value": s[col], "Year": str(int(yr)),
                                        "metric": f"Marginal Impact per ball (vs. {ref}, controlling for line & length)"}))
    _save(_year_facets(frames, top, f"Shot selection by year (top {top_n} shots by volume)"), outpath)


def plot_shot_quadrant(shot_prop_table, shot_marginal_year_table, outpath, min_balls=60, n_labels=14):
    years = sorted(shot_prop_table["year"].unique())
    if len(years) < 2 or shot_marginal_year_table.empty:
        return
    y0, y1 = years[0], years[-1]
    prop_pivot = shot_prop_table.pivot(index="shot", columns="year", values="proportion")
    prop_change = (prop_pivot.get(y1) - prop_pivot.get(y0)).rename("prop_change")
    merged = shot_marginal_year_table.dropna(subset=["change"]).merge(
        prop_change, left_on="shot", right_index=True, how="inner")
    balls_col = f"balls_{y0}"
    if balls_col in merged.columns:
        merged = merged[merged[balls_col] >= min_balls]
    if merged.empty:
        return
    merged = merged.copy()
    merged["x"] = merged["prop_change"] * 100
    merged["Direction"] = np.where(merged["change"] > 0, "value up", "value down")
    size_col = f"balls_{y1}" if f"balls_{y1}" in merged.columns else None
    mapping = dict(x="x", y="change", color="Direction")
    if size_col:
        mapping["size"] = size_col
    ref = merged["reference_shot"].iloc[0] if "reference_shot" in merged.columns else "?"
    p = (ggplot(merged, aes(**mapping)) + geom_hline(yintercept=0, color="gray") + geom_vline(xintercept=0, color="gray")
         + geom_point(alpha=0.8)
         + geom_text(mapping=aes(x="x", y="change", label="shot"), inherit_aes=False, size=7, ha="left", nudge_x=0.05)
         + scale_color_manual(values={"value up": BLUE, "value down": RED})
         + scale_size_continuous(range=(2, 9))
         + labs(title=f"Shot selection: usage shift vs. value shift\n(vs. reference shot {ref}, controlling for line & length)",
                x="Change in share of deliveries played (pp), year over year",
                y="Change in marginal batting Impact per ball", size="Balls (latest yr)")
         + _base(8.5, 7))
    _save(p, outpath)


# ---------------------------------------------------------------- DL fit diagnostics
def _r_pro_curve(b, w, lam, best_std, n0):
    """Same formula as dl_pro_fit.r_pro (re-implemented here to avoid a circular import)."""
    w = np.asarray(w, float)
    F = best_std["F_func"](w, *best_std["F_coefs"])
    beta = np.clip(best_std["beta_func"](w, *best_std["beta_coefs"]), 1e-3, None)
    nw = n0 * F
    out = best_std["R0"] * F * lam ** (nw + 1.0) * (1.0 - np.exp(-np.asarray(b, float) / (beta * lam ** nw)))
    return np.where(w >= 10, 0.0, out)


def plot_fit_diagnostics(fit_data, best_std, n0, lam_examples, outpath, wkts_to_show=(0, 2, 4, 6, 8)):
    p1 = "DL Standard: fit vs. binned data"
    p2 = f"DL Pro vs Standard (n0={n0:.2f})"
    panels = [p1, p2]
    b_grid = np.linspace(0, 120, 121)
    model, popt = best_std["model"], best_std["popt"]

    pts, fit_lines = [], []
    for w in wkts_to_show:
        name = f"w={int(w)}"
        sub = fit_data[fit_data["w"] == w]
        if len(sub):
            emp = sub.groupby("b")["y_runs_to_come"].mean()
            pts.append(pd.DataFrame({"b": emp.index.to_numpy(float), "runs": emp.to_numpy(), "series": name, "panel": p1}))
        fit_lines.append(pd.DataFrame({"b": b_grid, "runs": model((b_grid, np.full_like(b_grid, w)), *popt),
                                       "series": name, "panel": p1}))
    w0 = np.zeros_like(b_grid)
    std = pd.DataFrame({"b": b_grid, "runs": model((b_grid, w0), *popt), "series": "R_std (lambda=1)", "panel": p2})
    pro = pd.concat([pd.DataFrame({"b": b_grid, "runs": _r_pro_curve(b_grid, w0, lam, best_std, n0),
                                   "series": f"R_pro, lambda={lam:.2f}", "panel": p2}) for lam in lam_examples],
                    ignore_index=True)
    lines = pd.concat(fit_lines + [pro], ignore_index=True)
    for df_ in [lines, std] + pts:
        df_["panel"] = pd.Categorical(df_["panel"], categories=panels, ordered=True)
    lines["series"] = pd.Categorical(lines["series"], categories=list(dict.fromkeys(lines["series"])), ordered=True)

    p = ggplot() + geom_line(data=lines, mapping=aes("b", "runs", color="series", group="series"))
    if pts:
        pts_df = pd.concat(pts, ignore_index=True)
        pts_df["panel"] = pd.Categorical(pts_df["panel"], categories=panels, ordered=True)
        p = p + geom_point(data=pts_df, mapping=aes("b", "runs", color="series"), size=1.2, alpha=0.5)
    p = (p + geom_line(data=std, mapping=aes("b", "runs", group="series"), color="black", linetype="dashed")
         + facet_wrap("~panel", ncol=2, scales="free_y")
         + labs(x="Balls remaining (b)", y="Runs still to come", color="")
         + _base(13, 5.2))
    _save(p, outpath)


# ---------------------------------------------------------------- Impact Player substitutions
def _plot_slot_distribution(p, value_col, baseline_label, outpath, year_label, bins=24):
    pp = p.dropna(subset=[value_col])
    if pp.empty:
        print(f"_plot_slot_distribution: nothing to plot for {value_col}.")
        return
    t1, t2 = f"By team's batting order{year_label}", f"By slot's role{year_label}"
    parts = []
    for role, sub in pp.groupby("team_role"):
        parts.append(pd.DataFrame({"value": sub[value_col].to_numpy(), "group": f"{role} (n={len(sub)})", "panel": t1}))
    for role, sub in pp.groupby("slot_role"):
        parts.append(pd.DataFrame({"value": sub[value_col].to_numpy(), "group": f"{role} (n={len(sub)})", "panel": t2}))
    t = pd.concat(parts, ignore_index=True)
    t["panel"] = pd.Categorical(t["panel"], categories=[t1, t2], ordered=True)
    means = t.groupby(["panel", "group"], observed=True)["value"].mean().reset_index(name="m")

    palette = {"Batted 1st": BLUE, "Batted 2nd": RED, "Unknown": "#999999",
               "Bowling slot": GREEN, "Batting slot": "#aa6633"}
    cols = {g: palette.get(g.split(" (n=")[0], "#999999") for g in t["group"].unique()}
    plot = (ggplot(t, aes("value", fill="group"))
            + geom_histogram(aes(y=after_stat("density")), bins=bins, alpha=0.55, position="identity")
            + geom_vline(xintercept=0)
            + geom_vline(data=means, mapping=aes(xintercept="m", color="group"), inherit_aes=False, linetype="dashed")
            + scale_fill_manual(values=cols) + scale_color_manual(values=cols)
            + facet_wrap("~panel", nrow=1)
            + labs(title=f"Distribution of combined (player-out + player-in) slot Impact vs. the "
                         f"{baseline_label} other teammate,\nper substitution event (dashed = mean per group)",
                   x=f"Combined slot Impact vs. {baseline_label} teammate (runs)", y="Density", fill="", color="")
            + _base(13, 5.5) + theme(legend_position="bottom"))
    _save(plot, outpath)
    print(f"Saved {outpath}")


def _filter_years(perf, year_min, year_max):
    out = perf.copy()
    if year_min is not None:
        out = out[out["year"] >= year_min]
    if year_max is not None:
        out = out[out["year"] <= year_max]
    return out.reset_index(drop=True)


def plot_impact_sub_distribution(perf, outpath_prefix, year_min=None, year_max=None, bins=24):
    p = _filter_years(perf, year_min, year_max)
    if p.empty:
        print("plot_impact_sub_distribution: nothing to plot after year filtering.")
        return
    year_label = f" ({year_min}-{year_max})" if (year_min is not None or year_max is not None) else ""
    _plot_slot_distribution(p, "slot_impact_vs_worst", "worst", f"{outpath_prefix}_vs_worst.png", year_label, bins)
    _plot_slot_distribution(p, "slot_impact_vs_mean", "mean", f"{outpath_prefix}_vs_mean.png", year_label, bins)
    _plot_slot_distribution(p, "substitution_effect", "both-category substitution effect",
                            f"{outpath_prefix}_substitution_effect.png", year_label, bins)
    for value_col, label in (("batting_slot_impact_vs_mean", "mean batting"),
                             ("bowling_slot_impact_vs_mean", "mean bowling")):
        _plot_slot_distribution(p, value_col, label, f"{outpath_prefix}_vs_{value_col}.png", year_label, bins)


def plot_impact_sub_distribution_by_year_role(perf, outpath, bins=12):
    """Plot batting and bowling slot effects by season and batting innings role."""
    parts = []
    for (year, team_role), group in perf.groupby(["year", "team_role"]):
        for value_col, category in (("batting_slot_impact_vs_mean", "batting"),
                                    ("bowling_slot_impact_vs_mean", "bowling")):
            values = group[value_col].dropna()
            if len(values):
                parts.append(pd.DataFrame({
                    "value": values.to_numpy(),
                    "group": f"{int(year)} {team_role} {category}",
                }))
    if not parts:
        return
    t = pd.concat(parts, ignore_index=True)
    palette = {g: color for g, color in zip(
        t["group"].unique(),
        [BLUE, "#7aa4ff", "#f2b84b", "#ffd782", GREEN, "#79d0a6", RED, "#f08b86"],
    )}
    p = (ggplot(t, aes("value", fill="group"))
         + geom_histogram(bins=bins, alpha=0.48, position="identity")
         + geom_vline(xintercept=0)
         + scale_fill_manual(values=palette)
         + labs(title="Impact Player slot effects by year, innings, and category",
                x="Impact vs role mean (runs)", y="Substitution events", fill="")
         + _base(13, 6) + theme(legend_position="bottom"))
    _save(p, outpath)

import pandas as pd, numpy as np, json, os

OUT = "dl_pro_out"
d = pd.read_csv(f"{OUT}/ball_by_ball_raa_waa_impact.csv", low_memory=False)
bat_sum = pd.read_csv(f"{OUT}/batter_summary.csv")
bowl_sum = pd.read_csv(f"{OUT}/bowler_summary.csv")
venue = pd.read_csv(f"{OUT}/venue_summary.csv")
team_bat_t = pd.read_csv(f"{OUT}/team_batting_impact_per100_by_year.csv")
team_bowl_t = pd.read_csv(f"{OUT}/team_bowling_impact_per100_by_year.csv")
team_shot_saved = pd.read_csv(f"{OUT}/team_shot_type_by_year.csv") if os.path.exists(f"{OUT}/team_shot_type_by_year.csv") else pd.DataFrame()
team_zone_saved = pd.read_csv(f"{OUT}/team_wagon_zone_by_year.csv") if os.path.exists(f"{OUT}/team_wagon_zone_by_year.csv") else pd.DataFrame()
team_line_saved = pd.read_csv(f"{OUT}/team_line_length_by_year.csv") if os.path.exists(f"{OUT}/team_line_length_by_year.csv") else pd.DataFrame()
team_speed_saved = pd.read_csv(f"{OUT}/team_speed_by_year.csv") if os.path.exists(f"{OUT}/team_speed_by_year.csv") else pd.DataFrame()
season_bat_sum = pd.read_csv(f"{OUT}/season_batter_summary.csv") if os.path.exists(f"{OUT}/season_batter_summary.csv") else pd.DataFrame()
season_bowl_sum = pd.read_csv(f"{OUT}/season_bowler_summary.csv") if os.path.exists(f"{OUT}/season_bowler_summary.csv") else pd.DataFrame()
imp_perf = pd.read_csv(f"{OUT}/impact_player_performance.csv")
auction = pd.read_csv(f"{OUT}/impact_per_rupee.csv")
auction = auction[auction["total_balls"] >= 100].copy()

R = lambda x: round(float(x), 3) if pd.notna(x) else None
# Standard error of a per-category mean impact (impact_std / sqrt(balls)). Used so that
# every shot/zone/length-line/speed category can be *kept* -- including low-sample ones
# that used to get filtered out -- while still being honest about how uncertain a
# low-sample category's impact estimate is (shown as an error bar in the dashboard).
#
# A 2-3 ball sample's own standard deviation is itself estimated off almost no data, so
# the resulting SE can be wildly unstable -- e.g. a single outlier ball among 2 deliveries
# can produce an SE several times larger than the actual Impact values on the chart,
# blowing out the whole axis. Below MIN_BALLS_FOR_SE the category is still shown (bar/
# point stays on the chart), it just carries no error bar rather than a misleadingly
# huge or tiny one.
MIN_BALLS_FOR_SE = 4
def se_col(tbl):
    se = tbl["impact_std"] / np.sqrt(tbl["balls"])
    return se.where(tbl["balls"] >= MIN_BALLS_FOR_SE)

ROLE_MEANS = {
    "batting": R(bat_sum["impact_per_ball"].mean()),
    "bowling": R(bowl_sum["impact_per_ball"].mean()),
}

# ---- match meta ----
match_meta = d.drop_duplicates(subset="p_match").set_index("p_match")[
    ["ground", "match_date", "team_bat", "team_bowl", "winner", "year"]
]
venue_factor = venue.set_index("ground")["venue_factor"].to_dict()
venue_reliable = venue.set_index("ground")["reliable"].to_dict()

def vf(ground):
    f = venue_factor.get(ground)
    return R(f) if f is not None else None

# team of a player per match (as batter or bowler) -> use whichever role
bat_team_per_match = d.drop_duplicates(subset=["p_match", "bat"]).set_index(["p_match", "bat"])["team_bat"]
bowl_team_per_match = d.drop_duplicates(subset=["p_match", "bowl"]).set_index(["p_match", "bowl"])["team_bowl"]

# opponent lookup: for a given match+team, the other team
teams_per_match = d.drop_duplicates(subset="p_match").set_index("p_match")[["team_bat", "team_bowl"]]

def opponent(p_match, team):
    row = teams_per_match.loc[p_match]
    a, b = row["team_bat"], row["team_bowl"]
    return b if team == a else a

# ---------------------------------------------------------------
# PLAYERS
# ---------------------------------------------------------------
players = {}

# --- batting per-match impact ---
bat_match = d.groupby(["bat", "p_match"]).agg(
    balls=("impact", "size"), impact=("impact", "sum"), runs=("score", "sum")
).reset_index()

for name, grp in bat_match.groupby("bat"):
    if name not in bat_sum["bat"].values:
        continue
    games = []
    for _, r in grp.sort_values("p_match").iterrows():
        m = match_meta.loc[r["p_match"]]
        team = bat_team_per_match.get((r["p_match"], name))
        games.append({
            "match": int(r["p_match"]), "date": str(m["match_date"]), "ground": m["ground"],
            "opponent": opponent(r["p_match"], team) if team else None,
            "team": team, "impact": R(r["impact"]), "balls": int(r["balls"]), "runs": int(r["runs"]),
            "venue_factor": vf(m["ground"]),
        })
    row = bat_sum[bat_sum["bat"] == name].iloc[0]
    players.setdefault(name, {})["batting"] = {
        "career_impact": R(row["Impact"]), "career_balls": int(row["balls"]),
        "career_runs": int(row["runs"]), "impact_per_ball": R(row["impact_per_ball"]),
        "impact_vs_mean": R(row["impact_per_ball"] - ROLE_MEANS["batting"]),
        "mean_impact_per_ball": ROLE_MEANS["batting"],
        "percentile": R(row["Impact_pctile"]), "games": games,
    }

# --- bowling per-match impact ---
bowl_match = d.groupby(["bowl", "p_match"]).agg(
    balls=("bowl_impact", "size"), impact=("bowl_impact", "sum"), runs=("score", "sum"), wkts=("out", "sum")
).reset_index()

for name, grp in bowl_match.groupby("bowl"):
    if name not in bowl_sum["bowl"].values:
        continue
    games = []
    for _, r in grp.sort_values("p_match").iterrows():
        m = match_meta.loc[r["p_match"]]
        team = bowl_team_per_match.get((r["p_match"], name))
        games.append({
            "match": int(r["p_match"]), "date": str(m["match_date"]), "ground": m["ground"],
            "opponent": opponent(r["p_match"], team) if team else None,
            "team": team, "impact": R(r["impact"]), "balls": int(r["balls"]),
            "runs": int(r["runs"]), "wkts": int(r["wkts"]),
            "venue_factor": vf(m["ground"]),
        })
    row = bowl_sum[bowl_sum["bowl"] == name].iloc[0]
    players.setdefault(name, {})["bowling"] = {
        "career_impact": R(row["Impact"]), "career_balls": int(row["balls"]),
        "career_runs": int(row["runs_conceded"]), "impact_per_ball": R(row["impact_per_ball"]),
        "impact_vs_mean": R(row["impact_per_ball"] - ROLE_MEANS["bowling"]),
        "mean_impact_per_ball": ROLE_MEANS["bowling"],
        "percentile": R(row["Impact_pctile"]), "games": games,
    }

# --- percentile by year (within that year's qualified pool, not career pool) ---
if len(season_bat_sum) and "Impact_pctile" in season_bat_sum.columns:
    for name, grp in season_bat_sum.groupby("bat"):
        if name in players and "batting" in players[name]:
            players[name]["batting"]["percentile_by_year"] = {
                str(int(r["year"])): R(r["Impact_pctile"]) for _, r in grp.iterrows()
            }
if len(season_bowl_sum) and "Impact_pctile" in season_bowl_sum.columns:
    for name, grp in season_bowl_sum.groupby("bowl"):
        if name in players and "bowling" in players[name]:
            players[name]["bowling"]["percentile_by_year"] = {
                str(int(r["year"])): R(r["Impact_pctile"]) for _, r in grp.iterrows()
            }

# --- team-of-record for dropdown display: most frequent team ---
for name, p in players.items():
    teams_seen = []
    if "batting" in p:
        teams_seen += [g["team"] for g in p["batting"]["games"] if g["team"]]
    if "bowling" in p:
        teams_seen += [g["team"] for g in p["bowling"]["games"] if g["team"]]
    p["team"] = pd.Series(teams_seen).mode().iloc[0] if teams_seen else None

# --- shot type % + impact by year (batting) ---
# Every shot type is kept (previously the dashboard only ever showed the top 14 by
# volume, client-side); low-sample shots now come with a "se" (standard error) field
# instead of being dropped, so the chart can draw an honest error bar for them.
shot_d = d.dropna(subset=["shot"])
for name, grp in shot_d.groupby("bat"):
    if name not in players or "batting" not in players[name]:
        continue
    out = {}
    for yr, yg in grp.groupby("year"):
        tot = len(yg)
        tbl = yg.groupby("shot").agg(balls=("impact", "size"), impact=("impact", "mean"),
                                      impact_std=("impact", "std")).reset_index()
        tbl["pct"] = tbl["balls"] / tot * 100
        tbl["se"] = se_col(tbl)
        out[str(int(yr))] = [
            {"shot": row["shot"], "pct": R(row["pct"]), "impact": R(row["impact"]),
             "se": R(row["se"]), "balls": int(row["balls"])}
            for _, row in tbl.sort_values("balls", ascending=False).iterrows()
        ]
    players[name]["batting"]["shot_types"] = out

# --- wagon zone % + impact by year (batting), batter-perspective mirrored ---
CENTER = 181.5
wagon_d = d[d["wagonzone"] > 0].copy()
is_lhb = wagon_d["bat_hand"] == "LHB"
wagon_d["zone_bp"] = np.where(is_lhb, 9 - wagon_d["wagonzone"], wagon_d["wagonzone"]).astype(int)

league_wagon = {}
for yr, yg in wagon_d.groupby("year"):
    tot = len(yg)
    tbl = yg.groupby("zone_bp").agg(balls=("impact", "size"), impact=("impact", "mean")).reset_index()
    tbl["pct"] = tbl["balls"] / tot * 100
    league_wagon[str(int(yr))] = {int(r["zone_bp"]): {"pct": R(r["pct"]), "impact": R(r["impact"])} for _, r in tbl.iterrows()}

for name, grp in wagon_d.groupby("bat"):
    if name not in players or "batting" not in players[name]:
        continue
    out = {}
    for yr, yg in grp.groupby("year"):
        tot = len(yg)
        tbl = yg.groupby("zone_bp").agg(balls=("impact", "size"), impact=("impact", "mean"),
                                         impact_std=("impact", "std")).reset_index()
        tbl["pct"] = tbl["balls"] / tot * 100
        tbl["se"] = se_col(tbl)
        out[str(int(yr))] = [
            {"zone": int(row["zone_bp"]), "pct": R(row["pct"]), "impact": R(row["impact"]),
             "se": R(row["se"]), "balls": int(row["balls"])}
            for _, row in tbl.sort_values("zone_bp").iterrows()
        ]
    players[name]["batting"]["wagon_zones"] = out

# --- bowling: line/length grid % + impact by year ---
# Every (length, line) cell is kept now, however few balls landed there -- previously
# cells under 8 balls were dropped entirely. Sparse cells instead carry a "se" field so
# the dashboard can draw an error bar rather than hiding the cell.
ll_d = d.dropna(subset=["line", "length"])
league_ll = {}
for yr, yg in ll_d.groupby("year"):
    tot = len(yg)
    tbl = yg.groupby(["length", "line"]).agg(balls=("bowl_impact", "size"), impact=("bowl_impact", "mean"),
                                              impact_std=("bowl_impact", "std")).reset_index()
    tbl["pct"] = tbl["balls"] / tot * 100
    tbl["se"] = se_col(tbl)
    league_ll[str(int(yr))] = [
        {"length": r["length"], "line": r["line"], "pct": R(r["pct"]), "impact": R(r["impact"]),
         "se": R(r["se"]), "balls": int(r["balls"])}
        for _, r in tbl.iterrows()
    ]

for name, grp in ll_d.groupby("bowl"):
    if name not in players or "bowling" not in players[name]:
        continue
    out = {}
    for yr, yg in grp.groupby("year"):
        tot = len(yg)
        tbl = yg.groupby(["length", "line"]).agg(balls=("bowl_impact", "size"), impact=("bowl_impact", "mean"),
                                                  impact_std=("bowl_impact", "std")).reset_index()
        tbl["pct"] = tbl["balls"] / tot * 100
        tbl["se"] = se_col(tbl)
        out[str(int(yr))] = [
            {"length": r["length"], "line": r["line"], "pct": R(r["pct"]), "impact": R(r["impact"]),
             "se": R(r["se"]), "balls": int(r["balls"])}
            for _, r in tbl.sort_values("balls", ascending=False).iterrows()
        ]
    players[name]["bowling"]["line_length"] = out

# --- bowling: speed % + impact by year ---
SPEED_LABELS = {1: ">140", 2: "130-140", 3: "120-130", 4: "110-120", 5: "100-110", 6: "85-100", 7: "70-85", 8: "<70"}
sp_d = d.dropna(subset=["bowl_speed_category"])
for name, grp in sp_d.groupby("bowl"):
    if name not in players or "bowling" not in players[name]:
        continue
    out = {}
    for yr, yg in grp.groupby("year"):
        tot = len(yg)
        tbl = yg.groupby("bowl_speed_category").agg(balls=("bowl_impact", "size"), impact=("bowl_impact", "mean"),
                                                     impact_std=("bowl_impact", "std")).reset_index()
        tbl["pct"] = tbl["balls"] / tot * 100
        tbl["se"] = se_col(tbl)
        out[str(int(yr))] = [
            {"speed": SPEED_LABELS.get(int(row["bowl_speed_category"]), "?"), "pct": R(row["pct"]),
             "impact": R(row["impact"]), "se": R(row["se"]), "balls": int(row["balls"])}
            for _, row in tbl.sort_values("bowl_speed_category").iterrows()
        ]
    players[name]["bowling"]["speed"] = out

# --- bowling: line/length/speed JOINT grid % + impact by year ---
# Same idea as the line_length grid above, but with bowl_speed_category folded in as a
# third axis of the cell key (length|line|speed) instead of tracked as a separate,
# independent breakdown. This lets a year-over-year change be detected even when a
# bowler's length/line mix AND their speed mix each look unchanged on their own margins
# but they've started bowling different speeds at different lengths/lines (an
# interaction the two marginal grids can't see, since they're each summed out over the
# other dimension). Every cell is kept, however few balls, same as line_length/speed.
lls_d = d.dropna(subset=["line", "length", "bowl_speed_category"])
for name, grp in lls_d.groupby("bowl"):
    if name not in players or "bowling" not in players[name]:
        continue
    out = {}
    for yr, yg in grp.groupby("year"):
        tot = len(yg)
        tbl = yg.groupby(["length", "line", "bowl_speed_category"]).agg(
            balls=("bowl_impact", "size"), impact=("bowl_impact", "mean"),
            impact_std=("bowl_impact", "std")).reset_index()
        tbl["pct"] = tbl["balls"] / tot * 100
        tbl["se"] = se_col(tbl)
        out[str(int(yr))] = [
            {"length": r["length"], "line": r["line"],
             "speed": SPEED_LABELS.get(int(r["bowl_speed_category"]), "?"),
             "pct": R(r["pct"]), "impact": R(r["impact"]), "se": R(r["se"]), "balls": int(r["balls"])}
            for _, r in tbl.sort_values("balls", ascending=False).iterrows()
        ]
    players[name]["bowling"]["line_length_speed"] = out

# --- bowling: per-delivery variation (length/line distance from previous ball, same bowler) ---
LENGTH_COORD = {"SHORT": 1, "SHORT_OF_A_GOOD_LENGTH": 2, "GOOD_LENGTH": 3, "FULL": 4, "YORKER": 5, "FULL_TOSS": 6}
LINE_COORD = {"WIDE_OUTSIDE_OFFSTUMP": 1, "OUTSIDE_OFFSTUMP": 2, "ON_THE_STUMPS": 3, "DOWN_LEG": 4}
vd = d.copy()
vd["lc"] = vd["length"].map(LENGTH_COORD)
vd["nc"] = vd["line"].map(LINE_COORD)
vd = vd.dropna(subset=["lc", "nc"]).sort_values("row_id")
grpv = vd.groupby(["p_match", "inns", "bowl"], sort=False)
vd["dl"] = vd["lc"] - grpv["lc"].shift(1)
vd["dn"] = vd["nc"] - grpv["nc"].shift(1)
vd["dist"] = np.sqrt(vd["dl"] ** 2 + vd["dn"] ** 2)
vd = vd.dropna(subset=["dist"])

league_var = vd.groupby("year").agg(mean_dist=("dist", "mean"), mean_impact=("bowl_impact", "mean")).reset_index()
league_var_map = {}
for _, r in league_var.iterrows():
    yr_d = vd[vd["year"] == r["year"]]
    corr = yr_d["dist"].corr(yr_d["bowl_impact"])
    league_var_map[str(int(r["year"]))] = {
        "mean_dist": R(r["mean_dist"]), "mean_impact": R(r["mean_impact"]),
        "corr_dist_impact": R(corr) if pd.notna(corr) else None,
    }
league_var_overall_corr = vd["dist"].corr(vd["bowl_impact"])
league_variation_overall = {
    "mean_dist": R(vd["dist"].mean()), "mean_impact": R(vd["bowl_impact"].mean()),
    "corr_dist_impact": R(league_var_overall_corr) if pd.notna(league_var_overall_corr) else None,
    "balls": int(len(vd)),
}

MIN_BALLS_FOR_CORR = 15  # below this, a per-bowler correlation is too noisy to show
for name, grp in vd.groupby("bowl"):
    if name not in players or "bowling" not in players[name]:
        continue
    out = {}
    for yr, yg in grp.groupby("year"):
        corr = yg["dist"].corr(yg["bowl_impact"]) if len(yg) >= MIN_BALLS_FOR_CORR else None
        out[str(int(yr))] = {
            "mean_dist": R(yg["dist"].mean()), "mean_impact": R(yg["bowl_impact"].mean()), "balls": int(len(yg)),
            # Pearson correlation between this bowler's per-delivery variation (distance
            # from their own previous ball on the length/line grid) and that ball's
            # bowl_impact, within that season. Positive = mixing it up more paid off;
            # negative = more predictable deliveries actually worked better for them.
            "corr_dist_impact": R(corr) if corr is not None and pd.notna(corr) else None,
        }
    players[name]["bowling"]["variation"] = out

print(f"players: {len(players)}")

# ---------------------------------------------------------------
# TEAMS
# ---------------------------------------------------------------
TEAM_LOGO_FILE = {
    "Chennai Super Kings": "Chennai_Super_Kings_Logo.svg", "Delhi Capitals": "Delhi_Capitals.svg",
    "Gujarat Titans": "Gujarat_Titans_Logo.svg", "Rajasthan Royals": "Rajasthan_Royals_Logo.svg",
    "Punjab Kings": "Punjab_Kings_Logo.svg", "Kolkata Knight Riders": "Kolkata_Knight_Riders_Logo.svg",
    "Lucknow Super Giants": "Lucknow_Super_Giants_Logo.svg", "Mumbai Indians": "Mumbai_Indians_Logo.svg",
    "Sunrisers Hyderabad": "Sunrisers_Hyderabad_Logo.svg", "Royal Challengers Bengaluru": "Royal_Challengers_Bengaluru_Logo.svg",
}

teams = {}
for team in TEAM_LOGO_FILE:
    tb = team_bat_t[team_bat_t["team"] == team]
    tw = team_bowl_t[team_bowl_t["team"] == team]
    teams[team] = {
        "batting_per100": {str(int(r["year"])): R(r["impact_per_100_balls"]) for _, r in tb.iterrows()},
        "bowling_per100": {str(int(r["year"])): R(r["impact_per_100_balls"]) for _, r in tw.iterrows()},
    }
    grp = d[d["team_bat"] == team]
    sh = grp.dropna(subset=["shot"])
    out = {}
    for yr, yg in sh.groupby("year"):
        tot = len(yg)
        tbl = yg.groupby("shot").agg(balls=("impact", "size"), impact=("impact", "mean"),
                                      impact_std=("impact", "std")).reset_index()
        tbl["pct"] = tbl["balls"] / tot * 100
        tbl["se"] = se_col(tbl)
        out[str(int(yr))] = [{"shot": r["shot"], "pct": R(r["pct"]), "impact": R(r["impact"]),
                              "impact_vs_mean": R(r["impact"] - ROLE_MEANS["batting"]), "se": R(r["se"]),
                              "balls": int(r["balls"])}
                              for _, r in tbl.sort_values("balls", ascending=False).iterrows()]
    if len(team_shot_saved):
        out = {}
        for yr, yg in team_shot_saved[team_shot_saved["team"] == team].groupby("year"):
            total = yg["balls"].sum()
            out[str(int(yr))] = [{"shot": r["shot"], "pct": R(100*r["balls"]/total), "impact": R(r["impact"]),
                                  "impact_vs_mean": R(r["impact_vs_mean"]), "se": R(r.get("impact_se")),
                                  "balls": int(r["balls"])}
                                 for _, r in yg.sort_values("balls", ascending=False).iterrows()]
    teams[team]["shot_types"] = out

    wg = wagon_d[wagon_d["team_bat"] == team]
    out = {}
    for yr, yg in wg.groupby("year"):
        tot = len(yg)
        tbl = yg.groupby("zone_bp").agg(balls=("impact", "size"), impact=("impact", "mean"),
                                         impact_std=("impact", "std")).reset_index()
        tbl["pct"] = tbl["balls"] / tot * 100
        tbl["se"] = se_col(tbl)
        out[str(int(yr))] = [{"zone": int(r["zone_bp"]), "pct": R(r["pct"]), "impact": R(r["impact"]),
                              "impact_vs_mean": R(r["impact"] - ROLE_MEANS["batting"]), "se": R(r["se"]),
                              "balls": int(r["balls"])}
                              for _, r in tbl.sort_values("zone_bp").iterrows()]
    if len(team_zone_saved):
        out = {}
        for yr, yg in team_zone_saved[team_zone_saved["team"] == team].groupby("year"):
            total = yg["balls"].sum()
            out[str(int(yr))] = [{"zone": int(r["zone"]), "pct": R(100*r["balls"]/total), "impact": R(r["impact"]),
                                  "impact_vs_mean": R(r["impact_vs_mean"]), "se": R(r.get("impact_se")),
                                  "balls": int(r["balls"])}
                                 for _, r in yg.sort_values("zone").iterrows()]
    teams[team]["wagon_zones"] = out

    # Every (length, line) cell is kept (no more `balls >= 8` filter); low-sample cells
    # carry "se" instead so the diff heatmap can flag them as uncertain.
    bg = ll_d[ll_d["team_bowl"] == team]
    out = {}
    for yr, yg in bg.groupby("year"):
        tot = len(yg)
        tbl = yg.groupby(["length", "line"]).agg(balls=("bowl_impact", "size"), impact=("bowl_impact", "mean"),
                                                  impact_std=("bowl_impact", "std")).reset_index()
        tbl["pct"] = tbl["balls"] / tot * 100
        tbl["se"] = se_col(tbl)
        out[str(int(yr))] = [{"length": r["length"], "line": r["line"], "pct": R(r["pct"]), "impact": R(r["impact"]),
                              "impact_vs_mean": R(r["impact"] - ROLE_MEANS["bowling"]), "se": R(r["se"]),
                              "balls": int(r["balls"])}
                              for _, r in tbl.iterrows()]
    if len(team_line_saved):
        out = {}
        for yr, yg in team_line_saved[team_line_saved["team"] == team].groupby("year"):
            total = yg["balls"].sum()
            out[str(int(yr))] = [{"length": r["length"], "line": r["line"], "pct": R(100*r["balls"]/total),
                                  "impact": R(r["impact"]), "impact_vs_mean": R(r["impact_vs_mean"]),
                                  "se": R(r.get("impact_se")), "balls": int(r["balls"])}
                                 for _, r in yg.iterrows()]
    teams[team]["line_length"] = out

    sg = sp_d[sp_d["team_bowl"] == team]
    out = {}
    for yr, yg in sg.groupby("year"):
        tot = len(yg)
        tbl = yg.groupby("bowl_speed_category").agg(balls=("bowl_impact", "size"), impact=("bowl_impact", "mean"),
                                                     impact_std=("bowl_impact", "std")).reset_index()
        tbl["pct"] = tbl["balls"] / tot * 100
        tbl["se"] = se_col(tbl)
        out[str(int(yr))] = [{"speed": SPEED_LABELS.get(int(r["bowl_speed_category"]), "?"), "pct": R(r["pct"]),
                               "impact": R(r["impact"]), "impact_vs_mean": R(r["impact"] - ROLE_MEANS["bowling"]),
                               "se": R(r["se"]), "balls": int(r["balls"])}
                               for _, r in tbl.sort_values("bowl_speed_category").iterrows()]
    if len(team_speed_saved):
        out = {}
        for yr, yg in team_speed_saved[team_speed_saved["team"] == team].groupby("year"):
            total = yg["balls"].sum()
            out[str(int(yr))] = [{"speed": SPEED_LABELS.get(int(r["speed"]), "?"), "pct": R(100*r["balls"]/total),
                                   "impact": R(r["impact"]), "impact_vs_mean": R(r["impact_vs_mean"]),
                                   "se": R(r.get("impact_se")), "balls": int(r["balls"])}
                                  for _, r in yg.sort_values("speed").iterrows()]
    teams[team]["speed"] = out

    vg = vd[vd["team_bowl"] == team]
    out = {}
    for yr, yg in vg.groupby("year"):
        corr = yg["dist"].corr(yg["bowl_impact"])
        out[str(int(yr))] = {"mean_dist": R(yg["dist"].mean()), "mean_impact": R(yg["bowl_impact"].mean()),
                              "corr_dist_impact": R(corr) if pd.notna(corr) else None}
    teams[team]["variation"] = out
    # Flat, all-years-combined figure (not split by year) -- computed directly from every
    # raw delivery for this team across every year, not an average of the per-year means,
    # so a team with more balls in one year isn't under/over-weighted.
    if len(vg):
        overall_corr = vg["dist"].corr(vg["bowl_impact"])
        teams[team]["variation_overall"] = {
            "mean_dist": R(vg["dist"].mean()), "mean_impact": R(vg["bowl_impact"].mean()),
            "corr_dist_impact": R(overall_corr) if pd.notna(overall_corr) else None,
            "balls": int(len(vg)),
        }
    else:
        teams[team]["variation_overall"] = None

print(f"teams: {len(teams)}")

# ---------------------------------------------------------------
# IMPACT PLAYER EVENTS
# ---------------------------------------------------------------
ip_events = []
for _, r in imp_perf.iterrows():
    m = match_meta.loc[r["p_match"]]
    ip_events.append({
        "match": int(r["p_match"]), "date": str(m["match_date"]), "ground": m["ground"], "year": int(r["year"]),
        "team": r["team"], "opponent": opponent(r["p_match"], r["team"]), "team_role": r["team_role"],
        "player_in": r["player_in"], "player_out": r["player_out"], "slot_role": r["slot_role"],
        "slot_impact": R(r["slot_impact"]), "baseline_mean": R(r["baseline_mean"]),
        "vs_mean": R(r["slot_impact_vs_mean"]), "vs_worst": R(r["slot_impact_vs_worst"]),
        "batting_vs_mean": R(r["batting_slot_impact_vs_mean"]),
        "bowling_vs_mean": R(r["bowling_slot_impact_vs_mean"]),
        "substitution_effect": R(r["substitution_effect"]),
    })
ip_events.sort(key=lambda x: x["date"])
print(f"impact player events: {len(ip_events)}")

# ---------------------------------------------------------------
# AUCTION
# ---------------------------------------------------------------
auc = []
for _, r in auction.iterrows():
    if pd.isna(r["price_in_unit"]) or pd.isna(r["impact_per_unit_money"]):
        continue
    auction_role = "bowling" if r.get("type") == "Bowler" else "batting"
    impact_per_ball = r["total_impact"] / r["total_balls"] if r["total_balls"] else None
    auc.append({
        "player": r["player"], "year": int(r["year"]), "team": r.get("auction_team"),
        "price_cr": R(r["price_in_unit"]), "impact": R(r["total_impact"]),
        "impact_per_cr": R(r["impact_per_unit_money"]), "balls": int(r["total_balls"]), "role": r.get("type"),
        "impact_vs_mean": R(impact_per_ball - ROLE_MEANS[auction_role]),
    })
print(f"auction rows: {len(auc)}")

DATA = {
    "players": players, "teams": teams, "impact_player": ip_events, "auction": auc,
    "team_names": list(TEAM_LOGO_FILE.keys()), "league_variation": league_var_map,
    "league_variation_overall": league_variation_overall,
    "role_means": ROLE_MEANS,
}
with open("app_data.json", "w") as f:
    json.dump(DATA, f, separators=(",", ":"))
print("wrote app_data.json", os.path.getsize("app_data.json") / 1024, "KB")

# ---- logos as data URIs ----
import base64
logos = {}
for team, fn in TEAM_LOGO_FILE.items():
    with open(f"logos/{fn}", "rb") as f:
        b = f.read()
    logos[team] = "data:image/svg+xml;base64," + base64.b64encode(b).decode()
with open("logos.json", "w") as f:
    json.dump(logos, f)
print("wrote logos.json", os.path.getsize("logos.json") / 1024, "KB")

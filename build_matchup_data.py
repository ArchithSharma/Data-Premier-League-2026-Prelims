"""Batter-vs-bowler matchup model (SABR-matchup-predictor style) for the dashboard.

Additive ridge model, fit separately for 2023, 2024 and pooled, for three outcomes
(Impact, runs, dismissal) per ball:

    y = league effects (style, length, line, speed band, batter hand + hand interactions)
        + batter effects (overall, vs style, vs length, vs line, vs speed band)

Batter effects are shrunk toward 0 (the average batter of that hand) in proportion to
sample size, so a batter with few balls vs a type of delivery stays near league average.
The model is additive, so a bowler is fully described by his style plus the *share* of his
deliveries at each length / line / speed band -- which is exactly what the app exports.
"""
import numpy as np, pandas as pd, scipy.sparse as sp

LEN = ["SHORT", "SHORT_OF_A_GOOD_LENGTH", "GOOD_LENGTH", "FULL", "YORKER", "FULL_TOSS"]
LINE = ["WIDE_OUTSIDE_OFFSTUMP", "OUTSIDE_OFFSTUMP", "ON_THE_STUMPS", "DOWN_LEG"]
SPD = ["130+ kph", "110-130 kph", "85-110 kph", "<85 kph"]
SPD_BAND = {1: 0, 2: 0, 3: 1, 4: 1, 5: 2, 6: 2, 7: 3, 8: 3}
STY = ["Right-arm pace", "Left-arm pace", "Off-spin", "Leg-spin", "Left-arm orthodox"]
STY_MAP = {"RF": 0, "RFM": 0, "RMF": 0, "RM": 0, "LF": 1, "LFM": 1, "LMF": 1, "LM": 1,
           "OB": 2, "OB/LB": 2, "LB": 3, "LBG": 3, "LWS": 3, "SLA": 4, "SLA/LWS": 4}
OUTCOMES = {"impact": "impact", "runs": "score", "wkt": "out"}
LAM_LEAGUE, LAM_BASE, LAM_SUB = 2.0, 100.0, 150.0   # ridge strength (in "pseudo-balls")
MIN_EXPORT_BALLS = 30
R4 = lambda a: np.round(np.asarray(a, dtype=float), 4).tolist()


def prep(d):
    x = d.copy()
    x["line"] = x["line"].replace({"WIDE_DOWN_LEG": "DOWN_LEG"})
    x["sty"] = x["bowl_style"].map(STY_MAP)
    x["spd"] = x["bowl_speed_category"].map(SPD_BAND)
    x["li"] = x["length"].map({k: i for i, k in enumerate(LEN)})
    x["ni"] = x["line"].map({k: i for i, k in enumerate(LINE)})
    x["h"] = (x["bat_hand"] == "LHB").astype(int)
    x["out"] = x["out"].astype(float)
    x = x.dropna(subset=["sty", "spd", "li", "ni"])
    for c in ("sty", "spd", "li", "ni"):
        x[c] = x[c].astype(int)
    return x.reset_index(drop=True)


def design(x, batters, bowlers):
    """Sparse design matrix + per-column ridge lambdas + column layout."""
    n = len(x); cols, lams, layout = [], [], {}
    def add(name, idx_arr, k, lam, mask=None):
        # one-hot block of width k for index array idx_arr (optionally only where mask)
        rows = np.arange(n) if mask is None else np.where(mask)[0]
        ids = idx_arr if mask is None else idx_arr[mask]
        m = sp.csr_matrix((np.ones(len(rows)), (rows, ids)), shape=(n, k))
        layout[name] = (sum(c.shape[1] for c in cols), k)
        cols.append(m); lams.extend([lam] * k)
    add("sty", x["sty"].values, 5, LAM_LEAGUE); add("len", x["li"].values, 6, LAM_LEAGUE)
    add("line", x["ni"].values, 4, LAM_LEAGUE); add("spd", x["spd"].values, 4, LAM_LEAGUE)
    add("hand", x["h"].values, 2, LAM_LEAGUE)
    add("hs", x["h"].values * 5 + x["sty"].values, 10, LAM_LEAGUE)
    add("hl", x["h"].values * 6 + x["li"].values, 12, LAM_LEAGUE)
    add("hn", x["h"].values * 4 + x["ni"].values, 8, LAM_LEAGUE)
    bidx = x["bat"].map({b: i for i, b in enumerate(batters)}).values
    for b_i, b in enumerate(batters):
        m = bidx == b_i
        add(f"b|{b}|b", np.zeros(n, int), 1, LAM_BASE, m)
        add(f"b|{b}|s", x["sty"].values, 5, LAM_SUB, m)
        add(f"b|{b}|l", x["li"].values, 6, LAM_SUB, m)
        add(f"b|{b}|n", x["ni"].values, 4, LAM_SUB, m)
        add(f"b|{b}|p", x["spd"].values, 4, LAM_SUB, m)
    # bowler effects: his own skill beyond style/line/length/speed mix (overall, and vs LHB/RHB)
    widx = x["bowl"].map({w: i for i, w in enumerate(bowlers)}).values
    for w_i, w in enumerate(bowlers):
        m = widx == w_i
        add(f"w|{w}|b", np.zeros(n, int), 1, LAM_BASE, m)
        add(f"w|{w}|h", x["h"].values, 2, LAM_SUB, m)
    return sp.hstack(cols).tocsr(), np.array(lams), layout


def fit_scope(x):
    counts = x["bat"].value_counts()
    batters = sorted(counts[counts >= MIN_EXPORT_BALLS].index)
    xs = x[x["bat"].isin(batters)].reset_index(drop=True)
    bc = xs["bowl"].value_counts()
    bowlers = sorted(bc[bc >= MIN_EXPORT_BALLS].index)
    X, lams, lay = design(xs, batters, bowlers)
    scale = 1.0 / np.sqrt(lams)
    Xs = X.multiply(scale).tocsr()
    A = (Xs.T @ Xs).toarray() + np.eye(Xs.shape[1])
    out = {}
    for key, col in OUTCOMES.items():
        y = xs[col].values.astype(float); mu = y.mean()
        c = scale * np.linalg.solve(A, Xs.T @ (y - mu))
        g = lambda n: c[lay[n][0]:lay[n][0] + lay[n][1]]
        lg = {"sty": R4(g("sty")), "len": R4(g("len")), "line": R4(g("line")), "spd": R4(g("spd")),
              "hand": R4(g("hand")), "hs": R4(g("hs").reshape(2, 5)), "hl": R4(g("hl").reshape(2, 6)),
              "hn": R4(g("hn").reshape(2, 4))}
        bat = {b: {"b": round(float(g(f"b|{b}|b")[0]), 4), "s": R4(g(f"b|{b}|s")), "l": R4(g(f"b|{b}|l")),
                   "n": R4(g(f"b|{b}|n")), "p": R4(g(f"b|{b}|p"))} for b in batters}
        bow = {w: {"b": round(float(g(f"w|{w}|b")[0]), 4), "h": R4(g(f"w|{w}|h"))} for w in bowlers}
        out[key] = {"ic": round(float(mu), 5), "lg": lg, "bat": bat, "bow": bow}
    meta = {}
    for b, g in xs.groupby("bat"):
        meta[b] = {"hand": "L" if g["h"].mode().iloc[0] else "R", "balls": int(len(g)),
                   "sty": np.bincount(g["sty"], minlength=5).tolist()}
    return out, meta, xs


def bowler_profiles(x):
    prof = {}
    for b, g in x.groupby("bowl"):
        if len(g) < 30: continue
        sh = lambda col, k: R4(np.bincount(g[col], minlength=k) / len(g))
        prof[b] = {"sty": int(g["sty"].mode().iloc[0]), "balls": int(len(g)),
                   "len": sh("li", 6), "line": sh("ni", 4), "spd": sh("spd", 4)}
    return prof


def build_matchups(d):
    x = prep(d)
    scopes = {"2023": x[x["year"] == 2023], "2024": x[x["year"] == 2024], "all": x}
    res = {"levels": {"len": LEN, "line": LINE, "spd": SPD, "sty": STY}, "scopes": {}}
    for name, xs in scopes.items():
        model, meta, used = fit_scope(xs)
        res["scopes"][name] = {"model": model, "bat": meta, "bowl": bowler_profiles(xs)}
        print(f"matchup model {name}: {len(meta)} batters, {len(res['scopes'][name]['bowl'])} bowlers, {len(xs)} balls")
    # sanity check: does a batter's shrunk overall effect persist from 2023 to 2024?
    a, b = res["scopes"]["2023"]["model"]["impact"]["bat"], res["scopes"]["2024"]["model"]["impact"]["bat"]
    both = [k for k in a if k in b]
    r = np.corrcoef([a[k]["b"] for k in both], [b[k]["b"] for k in both])[0, 1]
    print(f"year-to-year correlation of batter overall effect (impact), n={len(both)}: r={r:.2f}")
    for o in ("runs", "impact", "wkt"):
        a, b = res["scopes"]["2023"]["model"][o]["bow"], res["scopes"]["2024"]["model"][o]["bow"]
        both = [k for k in a if k in b]
        print(f"bowler overall effect persistence ({o}): r={np.corrcoef([a[k]['b'] for k in both],[b[k]['b'] for k in both])[0,1]:.2f}, n={len(both)}")
    return res

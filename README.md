# DL Pro Dashboard

This project computes DL-Pro batting and bowling Impact, writes analysis tables and plots to `dl_pro_out/`, and builds the standalone web dashboard `dl_pro_dashboard.html`.

## Setup

Use Python 3.10+ and install the dependencies:

```bash
python -m pip install numpy pandas scipy plotnine matplotlib
```

The dashboard loads Chart.js from a CDN and has a fallback CDN. An internet connection is needed for charts; tables and saved CSV/PNG outputs do not need Chart.js.

## Inputs

Configure the `CSV_CONFIG` dictionary at the bottom of `dl_pro_fit.py`:

- `csv_path`: ball-tracking CSV, such as `Data_Premier_League_Prelims_Dataset.csv`
- `auction_xlsx_path`: optional auction workbook
- `impact_player_json_dir`: optional Cricsheet JSON directory, such as `ipl_json`
- `outdir`: normally `dl_pro_out`

Put the team logo files named by `TEAM_LOGO_FILE` in `logos/`.

## Build Everything

From the project directory, run:

```bash
python generate_dashboard.py --run-pipeline
```

This runs `dl_pro_fit.py`, rebuilds dashboard data, embeds the data and logos into the HTML, and syntax-checks the embedded JavaScript. Main outputs:

- `dl_pro_out/*.csv`: reproducible analysis tables
- `dl_pro_out/plots/*.png`: reproducible plots
- `app_data.json`: dashboard data before embedding
- `logos.json`: logo data before embedding
- `dl_pro_dashboard.html`: standalone dashboard

## Rebuild Dashboard Only

After editing `template_head.html` or `template_app.js`, or when the existing CSV outputs are current, run:

```bash
python generate_dashboard.py
```

The wrapper validates the final embedded script with Node.js. Open `index.html` from VS Code or your browser.

## Current Analysis Defaults

The active defaults in `dl_pro_fit.py` use a 100-ball minimum for qualified player and auction comparisons. Role-relative metrics use batting and bowling mean Impact per ball. Impact Player output includes:

- incoming/outgoing batting and bowling contributions
- `batting_slot_impact_vs_mean`
- `bowling_slot_impact_vs_mean`
- `substitution_effect`, the sum of both category effects

Team-level shot type, wagon-zone, line/length, and speed values are saved to dedicated `team_*_by_year.csv` files so dashboard stories can be reproduced from output tables.

## Season mix-change analysis

`dl_pro_fit.py` (Section 7b) reproduces the dashboard's "change between seasons" views as tables, a written summary and plots, for every qualified player and every team (first vs last season they appear in):

- batters: shot type and wagon zone; bowlers: line/length and bowl speed
- per category: share of deliveries and Impact vs the role mean per ball in both seasons, the deltas, and standard errors (categories under 4 balls carry no SE)
- a single change index per player/team and dimension (half the sum of absolute share changes; 0 = same mix, 100 = completely different)

Outputs in `dl_pro_out/`:

- `season_mix_change_categories.csv`: every number behind the dashboard's "Show the underlying values" tables
- `season_mix_change_summary.csv` / `season_mix_change_summary.md`: one row per player/team and dimension, and a ranked plain-language write-up (biggest share mover and biggest Impact swing; rankings need `season_change_min_balls` deliveries in both seasons)
- `plots/season_mix_change/*.png`: shot / zone / speed get a two-panel plot (share of deliveries on top, Impact with standard-error whiskers underneath, one number per bar; grey = first season, green/red = last season up/down); line/length gets a share grid and an Impact grid. The top `season_change_plot_top_n` movers per dimension are plotted, plus any names in `season_change_players`.

These are controlled by the `season_change_*` entries in `CSV_CONFIG`. The plotting functions are `plot_season_change_pair` and `plot_season_change_grid` in `dl_pro_plots.py` (plotnine).

## Troubleshooting

If `generate_dashboard.py --run-pipeline` reports missing input data, check the paths in `CSV_CONFIG` and confirm the source CSV, auction workbook, JSON folder, and logo files exist. If charts are blank, check network access to the Chart.js CDNs; dashboard tables and generated PNG files remain available.

## Matchup tools and rain-cloud ridge

- `build_matchup_data.py` (called from `build_dashboard_data.py`) fits a shrinkage (ridge) model per season (2023, 2024, pooled) and per outcome (runs, Impact, dismissals): league effects for bowling style, length, line, speed band and batter hand, plus shrunk effects for each batter (overall, vs style/length/line/speed) and each bowler (overall, vs LHB/RHB). The dashboard evaluates predictions in the browser from these coefficients.
- **Compare Players** tab: batter vs bowler, batter vs batter, bowler vs batter, bowler vs bowler, each shown per season.
- **Matchup Shifts** tab: 2023 → 2024 changes; batter × bowler pair changes are split into batter skill, bowler skill, bowler mix and league-wide shift.
- Impact Player tab: distribution is a rain-cloud ridge plot (toggle: Impact over expected, balls-adjusted against that team's own median Impact/ball per season, or raw substitution effect); hover a dot for the substitution details.

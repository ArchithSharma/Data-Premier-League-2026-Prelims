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

The wrapper validates the final embedded script with Node.js. Open `dl_pro_dashboard.html` from VS Code or your browser.

## Current Analysis Defaults

The active defaults in `dl_pro_fit.py` use a 100-ball minimum for qualified player and auction comparisons. Role-relative metrics use batting and bowling mean Impact per ball. Impact Player output includes:

- incoming/outgoing batting and bowling contributions
- `batting_slot_impact_vs_mean`
- `bowling_slot_impact_vs_mean`
- `substitution_effect`, the sum of both category effects

Team-level shot type, wagon-zone, line/length, and speed values are saved to dedicated `team_*_by_year.csv` files so dashboard stories can be reproduced from output tables.

## Troubleshooting

If `generate_dashboard.py --run-pipeline` reports missing input data, check the paths in `CSV_CONFIG` and confirm the source CSV, auction workbook, JSON folder, and logo files exist. If charts are blank, check network access to the Chart.js CDNs; dashboard tables and generated PNG files remain available.

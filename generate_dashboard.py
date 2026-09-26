#!/usr/bin/env python3
"""Build the standalone DL Pro dashboard from pipeline outputs.

Usage:
    python generate_dashboard.py              # rebuild data and HTML
    python generate_dashboard.py --run-pipeline
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REQUIRED_OUTPUTS = (
    "ball_by_ball_raa_waa_impact.csv",
    "batter_summary.csv",
    "bowler_summary.csv",
    "team_batting_impact_per100_by_year.csv",
    "team_bowling_impact_per100_by_year.csv",
    "impact_player_performance.csv",
    "impact_per_rupee.csv",
)


def run_pipeline() -> None:
    subprocess.run([sys.executable, "dl_pro_fit.py"], cwd=ROOT, check=True)


def check_inputs() -> None:
    missing = [name for name in REQUIRED_OUTPUTS if not (ROOT / "dl_pro_out" / name).exists()]
    for name in ("template_head.html", "template_app.js", "build_dashboard_data.py"):
        if not (ROOT / name).exists():
            missing.append(name)
    if missing:
        joined = ", ".join(missing)
        raise FileNotFoundError(f"Missing required files: {joined}. Run with --run-pipeline if outputs are missing.")


def build_data() -> None:
    subprocess.run([sys.executable, "build_dashboard_data.py"], cwd=ROOT, check=True)


def build_html() -> Path:
    head = (ROOT / "template_head.html").read_text(encoding="utf-8")
    app_data = (ROOT / "app_data.json").read_text(encoding="utf-8")
    logos = (ROOT / "logos.json").read_text(encoding="utf-8")
    app_js = (ROOT / "template_app.js").read_text(encoding="utf-8")
    html = (
        head
        + "const DATA = " + app_data + ";\n"
        + "const LOGOS = " + logos + ";\n"
        + app_js
        + "\n</script>\n</body>\n</html>\n"
    )
    output = ROOT / "dl_pro_dashboard.html"
    output.write_text(html, encoding="utf-8")
    return output


def validate_html(output: Path) -> None:
    script = output.read_text(encoding="utf-8")
    script = script[script.rfind("<script>") + len("<script>"):script.rfind("</script>")]
    check = ROOT / ".dashboard_embedded_check.js"
    check.write_text(script, encoding="utf-8")
    try:
        subprocess.run(["node", "--check", str(check)], cwd=ROOT, check=True)
    finally:
        check.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the standalone DL Pro dashboard.")
    parser.add_argument("--run-pipeline", action="store_true", help="Run dl_pro_fit.py before rebuilding the dashboard.")
    args = parser.parse_args()

    if args.run_pipeline:
        print("Running dl_pro_fit.py...")
        run_pipeline()
    check_inputs()
    print("Building app_data.json and logos.json...")
    build_data()
    output = build_html()
    validate_html(output)
    print(f"Built and validated: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

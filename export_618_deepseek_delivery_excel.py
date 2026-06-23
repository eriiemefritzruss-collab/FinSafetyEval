#!/usr/bin/env python3
"""Export 2026-06-18 DeepSeek-only delivery data.

This is a thin, reusable wrapper around export_common_model_delivery_excel.py.
It exports all currently DeepSeek-evaluated cases from the 7000-case source,
excluding IDs already delivered in the 2025-06-17 770-row workbook and the
legacy 500-row workbook.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import export_common_model_delivery_excel as common_export


def one_match(pattern: str, description: str) -> Path:
    matches = sorted(Path(".").glob(pattern))
    if len(matches) != 1:
        raise SystemExit(f"Expected exactly one {description} for {pattern!r}, got {matches}")
    return matches[0]


def default_output_path() -> Path:
    name = "20250618_" + "\u91d1\u878d\u5408\u89c4\u6570\u636e\u4ea4\u4ed8_deepseek_only.xlsx"
    return Path("data/input") / name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export 6.18 DeepSeek-only delivery Excel, excluding prior deliveries."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output_path(),
        help="Output Excel path.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Rows to export. Default 0 exports all eligible rows.",
    )
    parser.add_argument(
        "--raw-scenario",
        action="store_true",
        help="Keep source scenario/scenario_indicator instead of mapped values.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_jsonl = one_match(
        "data/input/*all_synthetic_merged_7000.jsonl",
        "7000-case input JSONL",
    )
    exclude_770 = one_match(
        "data/input/20250617_*770.xlsx",
        "2025-06-17 770-row delivery workbook",
    )
    exclude_500 = one_match(
        "data/input/*500.xlsx",
        "legacy 500-row delivery workbook",
    )

    judgment_jsonl = Path(
        "data/output/deepseek_r1_all_synthetic_merged_7000/"
        "judgments/deepseek-r1-250528_judgments.jsonl"
    )
    response_jsonl = Path(
        "data/output/deepseek_r1_all_synthetic_merged_7000/"
        "responses/deepseek-r1-250528_responses.jsonl"
    )
    for path in [judgment_jsonl, response_jsonl]:
        if not path.exists():
            raise SystemExit(f"Missing required DeepSeek result file: {path}")

    argv = [
        "export_common_model_delivery_excel.py",
        "--input",
        str(input_jsonl),
        "--output",
        str(args.output),
        "--model",
        "deepseek-r1",
        str(judgment_jsonl),
        str(response_jsonl),
        "gpt-5.4",
        "--exclude-excel",
        str(exclude_770),
        "--exclude-excel",
        str(exclude_500),
    ]
    if args.limit == 0:
        argv.append("--all-common")
    else:
        argv.extend(["--limit", str(args.limit)])
    if args.raw_scenario:
        argv.append("--raw-scenario")

    sys.argv = argv
    common_export.main()


if __name__ == "__main__":
    main()

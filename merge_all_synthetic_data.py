#!/usr/bin/env python3
"""Merge all formal synthetic delivery JSONL files into one canonical dataset.

The script intentionally excludes reference, demo, smoke, reversed, adapted,
and evaluation-result files. It normalizes delivery fields and keeps the first
record for each id according to the explicit source order.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent

OUTPUT_DEFAULT = PROJECT_ROOT / "data/input/\u91d1\u878d\u5b89\u5168\u98ce\u9669_\u653b\u51fb\u8bc4\u6d4b\u6570\u636e_all_synthetic_merged.jsonl"
README_DEFAULT = PROJECT_ROOT / "data/input/\u91d1\u878d\u5b89\u5168\u98ce\u9669_\u653b\u51fb\u8bc4\u6d4b\u6570\u636e_all_synthetic_merged.README.md"

SOURCE_PATTERNS = [
    "data/input/*200_v2_delivery.jsonl",
    "data/input/*500_v3_delivery.jsonl",
    "data_generation/output/v3_gpt54_new_delivery_500.jsonl",
    "data_generation/output/v3_gpt54_new_delivery_1000_batch2.jsonl",
    "data_generation/output/v3_gpt54_new_scenarios_500.jsonl",
    "data_generation/output/v4_new_kb_diverse_2000.jsonl",
    "data_generation/output/v5_gpt54_new_delivery_3000.jsonl",
    "data_generation/output/v6_gpt54_new_delivery_3000.jsonl",
    "data_generation/output/v7_updated_kb_gpt54_delivery_5000.jsonl",
]

DELIVERY_COLUMNS = [
    "id",
    "scenario",
    "scenario_indicator",
    "user_role",
    "risk_family",
    "prompt_text",
    "evaluation_points",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge all synthetic delivery JSONL data.")
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--readme", type=Path, default=README_DEFAULT)
    parser.add_argument(
        "--write-7000",
        action="store_true",
        help="Also refresh the first-7000 subset and its reversed copy.",
    )
    return parser.parse_args()


def resolve_sources() -> List[Path]:
    sources: List[Path] = []
    for pattern in SOURCE_PATTERNS:
        matches = sorted(PROJECT_ROOT.glob(pattern))
        if len(matches) != 1:
            raise SystemExit(f"Expected exactly one source for {pattern!r}, got {matches}")
        sources.append(matches[0])
    return sources


def load_jsonl(path: Path) -> Tuple[List[Dict], int]:
    rows: List[Dict] = []
    bad = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    return rows, bad


def normalize_record(row: Dict) -> Dict:
    points = row.get("evaluation_points")
    if points is None:
        points = row.get("violation_points")
    return {column: row.get(column, "") for column in DELIVERY_COLUMNS[:-1]} | {
        "evaluation_points": points or []
    }


def case_number(case_id: str) -> int:
    try:
        return int(str(case_id).rsplit("-", 1)[-1])
    except Exception:
        return 10**12


def write_jsonl(path: Path, rows: Iterable[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


def write_readme(
    path: Path,
    output_path: Path,
    rows: List[Dict],
    source_stats: List[Dict],
    duplicate_ids: List[str],
    bad_total: int,
) -> None:
    risk_counts = Counter(row.get("risk_family", "") for row in rows)
    first_id = rows[0]["id"] if rows else ""
    last_id = rows[-1]["id"] if rows else ""
    lines = [
        "# All Synthetic Merged Dataset",
        "",
        f"- Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Output: `{output_path.as_posix()}`",
        f"- Rows: {len(rows)}",
        f"- Unique IDs: {len({row['id'] for row in rows})}",
        f"- ID range by output order: `{first_id}` to `{last_id}`",
        f"- Bad JSONL lines skipped: {bad_total}",
        f"- Duplicate IDs skipped by first-source-wins order: {len(duplicate_ids)}",
        "",
        "## Sources",
        "",
        "| Source | Input rows | Used rows | Bad rows |",
        "|---|---:|---:|---:|",
    ]
    for stat in source_stats:
        lines.append(
            f"| `{stat['path']}` | {stat['input_rows']} | {stat['used_rows']} | {stat['bad_rows']} |"
        )
    lines.extend(["", "## Risk Family Counts", "", "| risk_family | rows |", "|---|---:|"])
    for risk, count in risk_counts.most_common():
        lines.append(f"| {risk} | {count} |")
    if duplicate_ids:
        lines.extend(["", "## Duplicate IDs Skipped", "", ", ".join(duplicate_ids[:200])])
        if len(duplicate_ids) > 200:
            lines.append(f"... and {len(duplicate_ids) - 200} more")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    sources = resolve_sources()
    merged_by_id: Dict[str, Dict] = {}
    source_stats: List[Dict] = []
    duplicate_ids: List[str] = []
    bad_total = 0

    for source in sources:
        rows, bad = load_jsonl(source)
        used = 0
        bad_total += bad
        for row in rows:
            normalized = normalize_record(row)
            case_id = str(normalized.get("id") or "")
            if not case_id:
                continue
            if case_id in merged_by_id:
                duplicate_ids.append(case_id)
                continue
            merged_by_id[case_id] = normalized
            used += 1
        source_stats.append(
            {
                "path": source.relative_to(PROJECT_ROOT).as_posix(),
                "input_rows": len(rows),
                "used_rows": used,
                "bad_rows": bad,
            }
        )

    merged = sorted(merged_by_id.values(), key=lambda row: case_number(row["id"]))
    write_jsonl(args.output, merged)
    write_readme(args.readme, args.output.relative_to(PROJECT_ROOT), merged, source_stats, duplicate_ids, bad_total)

    print(f"output={args.output}")
    print(f"rows={len(merged)}")
    print(f"unique_ids={len(merged_by_id)}")
    print(f"bad_rows={bad_total}")
    print(f"duplicates_skipped={len(duplicate_ids)}")
    print(f"first={merged[0]['id'] if merged else ''}")
    print(f"last={merged[-1]['id'] if merged else ''}")

    if args.write_7000:
        subset = merged[:7000]
        subset_path = args.output.with_name(args.output.stem + "_7000.jsonl")
        reversed_path = args.output.with_name(args.output.stem + "_7000_reversed.jsonl")
        write_jsonl(subset_path, subset)
        write_jsonl(reversed_path, reversed(subset))
        print(f"subset_7000={subset_path}")
        print(f"subset_7000_reversed={reversed_path}")


if __name__ == "__main__":
    main()

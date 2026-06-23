#!/usr/bin/env python3
"""
Crawler for OCC Enforcement Actions Search export.

The OCC site exposes the search result export as JSON. This script downloads
that official export and adds deterministic PDF URLs for the referenced action
documents.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests


EXPORT_URL = "https://apps.occ.gov/EASearch/Search/ExportToJSON"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "data" / "occ_enforcement_actions.json"
USER_AGENT = (
    "FinSafe research crawler/1.0 "
    "(public regulatory data collection; contact: research@example.com)"
)


def clean_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def document_url(document_id: str) -> str:
    return f"https://occ.gov/static/enforcement-actions/ea{document_id}.pdf"


def enrich_record(record: Dict[str, Any]) -> Dict[str, Any]:
    start_docs = [clean_text(doc) for doc in record.get("StartDocuments") or [] if clean_text(doc)]
    termination_docs = [clean_text(doc) for doc in record.get("TerminationDocuments") or [] if clean_text(doc)]
    record["source"] = "apps.occ.gov"
    record["authority"] = "OCC"
    record["collection_group"] = "occ_enforcement_actions"
    record["StartDocumentUrls"] = [document_url(doc) for doc in start_docs]
    record["TerminationDocumentUrls"] = [document_url(doc) for doc in termination_docs]
    return record


def crawl(timeout: float) -> Dict[str, Any]:
    response = requests.get(
        EXPORT_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    records: List[Dict[str, Any]] = response.json()
    records = [enrich_record(record) for record in records]
    return {
        "meta": {
            "source": "apps.occ.gov",
            "source_path": "/EASearch/Search/ExportToJSON",
            "collection_group": "occ_enforcement_actions",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "record_count": len(records),
            "status": "ok",
        },
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Download OCC Enforcement Actions Search JSON.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    payload = crawl(timeout=args.timeout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Wrote {payload['meta']['record_count']} OCC records to {args.output}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Crawler for SEC Litigation Releases.

The script stores real public enforcement/litigation records from sec.gov in a
raw JSON file. It does not generate or rewrite case facts; it only extracts the
official release metadata and page body text.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://www.sec.gov"
LIST_URL = f"{BASE_URL}/enforcement-litigation/litigation-releases"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "data" / "sec_litigation_releases.json"
USER_AGENT = (
    "FinSafe research crawler/1.0 "
    "(public regulatory data collection; contact: research@example.com)"
)


def clean_text(value: Any) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text).strip()


def fetch(session: requests.Session, url: str, timeout: float, retries: int = 3) -> str:
    last_error: Optional[Exception] = None
    for attempt in range(retries):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(1.0 + attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def iso_date_from_datetime(value: str) -> str:
    value = clean_text(value)
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return value


def extract_related_links(container: BeautifulSoup) -> List[Dict[str, str]]:
    links: List[Dict[str, str]] = []
    for anchor in container.select("a[href]"):
        title = clean_text(anchor.get_text(" ", strip=True))
        href = anchor.get("href")
        if not href:
            continue
        url = urljoin(BASE_URL, href)
        item = {"title": title or url, "url": url}
        if item not in links:
            links.append(item)
    return links


def parse_list_page(html: str, list_url: str, page: int) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    records: List[Dict[str, Any]] = []
    for row in soup.select("table.views-table tbody tr"):
        time_node = row.select_one("time")
        pub_date = clean_text(time_node.get_text(" ", strip=True)) if time_node else ""
        pub_date_iso = iso_date_from_datetime(time_node.get("datetime", "")) if time_node else ""

        link = row.select_one(".release-view__respondents a[href]")
        if not link:
            continue
        title = clean_text(link.get_text(" ", strip=True))
        detail_url = urljoin(BASE_URL, link.get("href", ""))

        release_node = row.select_one(".view-table_subfield_release_number .view-table_subfield_value")
        release_number = clean_text(release_node.get_text(" ", strip=True)) if release_node else ""
        if not release_number:
            match = re.search(r"/(lr-\d+(?:-\d+)?)$", detail_url, flags=re.IGNORECASE)
            release_number = match.group(1).upper() if match else ""

        records.append(
            {
                "source": "sec.gov",
                "authority": "SEC",
                "collection_group": "sec_litigation_releases",
                "query_context": {"page": page},
                "title": title,
                "detail_url": detail_url,
                "list_url": list_url,
                "pub_date": pub_date,
                "pub_date_iso": pub_date_iso,
                "release_number": release_number,
                "related_documents": extract_related_links(row),
            }
        )
    return records


def first_sentences(text: str, limit: int = 700) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out: List[str] = []
    for sentence in sentences:
        if sum(len(part) + 1 for part in out) + len(sentence) > limit:
            break
        out.append(sentence)
        if len(" ".join(out)) >= 240:
            break
    summary = " ".join(out).strip()
    return summary or text[:limit].rstrip()


def enrich_detail(record: Dict[str, Any], html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one(".field--name-body .field__item")
    if body:
        body_text = clean_text(body.get_text(" ", strip=True))
        detail_links = extract_related_links(body)
    else:
        main = soup.select_one("main") or soup
        body_text = clean_text(main.get_text(" ", strip=True))
        detail_links = extract_related_links(main)

    heading = soup.select_one("h1")
    if heading:
        record["headline_text"] = clean_text(heading.get_text(" ", strip=True))
    record["body_text"] = body_text
    record["summary"] = first_sentences(body_text)
    if detail_links:
        merged = record.get("related_documents") or []
        for link in detail_links:
            if link not in merged:
                merged.append(link)
        record["related_documents"] = merged
    return record


def dedupe(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    output: List[Dict[str, Any]] = []
    for record in records:
        key = record.get("detail_url") or record.get("release_number") or record.get("title")
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(record)
    return output


def crawl(max_records: int, max_pages: int, detail_limit: int, timeout: float, delay: float) -> Dict[str, Any]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )

    records: List[Dict[str, Any]] = []
    for page in range(max_pages):
        list_url = f"{LIST_URL}?page={page}"
        html = fetch(session, list_url, timeout=timeout)
        page_records = parse_list_page(html, list_url, page)
        if not page_records:
            break
        records.extend(page_records)
        records = dedupe(records)
        print(f"SEC list page {page}: collected {len(records)} records")
        if len(records) >= max_records:
            records = records[:max_records]
            break
        time.sleep(delay)

    enriched = 0
    for record in records[: max(0, detail_limit)]:
        detail_url = record.get("detail_url")
        if not detail_url:
            continue
        html = fetch(session, detail_url, timeout=timeout)
        enrich_detail(record, html)
        enriched += 1
        if enriched % 25 == 0:
            print(f"SEC detail pages enriched: {enriched}/{min(detail_limit, len(records))}")
        time.sleep(delay)

    return {
        "meta": {
            "source": "sec.gov",
            "source_path": "/enforcement-litigation/litigation-releases",
            "collection_group": "sec_litigation_releases",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "max_records": max_records,
            "max_pages": max_pages,
            "detail_limit": detail_limit,
            "record_count": len(records),
            "detail_enriched_count": enriched,
            "status": "ok",
        },
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl SEC Litigation Releases into raw JSON.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-records", type=int, default=600)
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--detail-limit", type=int, default=400)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--delay", type=float, default=0.15)
    args = parser.parse_args()

    payload = crawl(
        max_records=args.max_records,
        max_pages=args.max_pages,
        detail_limit=args.detail_limit,
        timeout=args.timeout,
        delay=args.delay,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Wrote {payload['meta']['record_count']} SEC records to {args.output}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Build penalty_cases.json from real, crawled regulatory records.

This script does not call an LLM and does not synthesize cases. It only
normalizes fields from crawled public regulatory datasets and adds auditable
source links.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.error import URLError
from urllib.request import Request, urlopen


DETAIL_URL = (
    "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html"
    "?docId={doc_id}&itemId=4115&generaltype=0"
)
API_URL = "https://www.nfra.gov.cn/cbircweb/DocInfo/SelectByDocId?docId={doc_id}"

DEFAULT_INPUT = (
    Path(__file__).resolve().parents[3]
    / "FinSafe-main"
    / "data"
    / "raw"
    / "zn"
    / "zn_regulatory_cases.json"
)
DEFAULT_INPUTS = [
    DEFAULT_INPUT,
    Path(__file__).resolve().parents[3]
    / "FinSafe-main"
    / "data"
    / "raw"
    / "en"
    / "finra_disciplinary_actions.json",
    Path(__file__).resolve().parents[3]
    / "FinSafe-main"
    / "data"
    / "raw"
    / "en"
    / "cross_agency_enforcement_actions.json",
    Path(__file__).resolve().parent / "data" / "occ_enforcement_actions.json",
    Path(__file__).resolve().parent / "data" / "sec_litigation_releases.json",
]
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "knowledge_base"
    / "penalty_cases.json"
)


def clean_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\u3000", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalize_date(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""

    match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if match:
        year, month, day = (int(part) for part in match.groups())
        return f"{year:04d}-{month:02d}-{day:02d}"

    match = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if match:
        year, month, day = (int(part) for part in match.groups())
        return f"{year:04d}-{month:02d}-{day:02d}"

    match = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if match:
        month, day, year = (int(part) for part in match.groups())
        return f"{year:04d}-{month:02d}-{day:02d}"

    return text


def short_hash(value: str, length: int = 12) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def date_sort_key(value: Any) -> Tuple[int, str]:
    normalized = normalize_date(value)
    try:
        return (1, datetime.strptime(normalized, "%Y-%m-%d").strftime("%Y%m%d"))
    except ValueError:
        return (0, normalized)


def extract_source_doc_id(record: Dict[str, Any]) -> str:
    source_file = clean_text(record.get("source_file"))
    match = re.match(r"(\d+)_", source_file)
    return match.group(1) if match else ""


def title_from_source_file(record: Dict[str, Any]) -> str:
    source_file = clean_text(record.get("source_file"))
    match = re.match(r"\d+_\d{4}-\d{2}-\d{2}_\d{2}:\d{2}:\d{2}_(.*)\.html$", source_file)
    if match:
        return match.group(1)
    return source_file.removesuffix(".html")


def infer_source(record: Dict[str, Any], title: str) -> str:
    authority = clean_text(record.get("authority"))
    if authority:
        return authority
    if "国家金融监督管理总局" in title:
        return "国家金融监督管理总局行政处罚信息公开表"
    if "银保监" in title:
        return "中国银行保险监督管理委员会行政处罚信息公开表"
    if "银监" in title:
        return "中国银行业监督管理委员会行政处罚信息公开表"
    if "保监" in title:
        return "中国保险监督管理委员会行政处罚信息公开表"
    return "监管机构行政处罚信息公开表"


def infer_domain(record: Dict[str, Any]) -> str:
    category = clean_text(record.get("category")) or "金融监管"
    violation_category = clean_text(record.get("violation_category"))
    violation = clean_text(record.get("violation"))
    laws = " ".join(record.get("laws") or [])
    text = f"{category} {violation_category} {violation} {laws}"

    if re.search(r"反洗钱|客户身份|可疑交易|大额交易|交易记录", text):
        return f"{category}/反洗钱与客户尽调"
    if re.search(r"贷款|贷前|贷后|授信|信贷|票据|保理|同业|存款", text):
        return f"{category}/信贷与审慎经营"
    if re.search(r"销售|误导|欺骗|隐瞒|承诺|适当性|投保|理赔|保单", text):
        return f"{category}/销售行为与适当性"
    if re.search(r"任职资格|高管|未经核准|许可证|许可|股东|股权", text):
        return f"{category}/许可准入与公司治理"
    if re.search(r"报送|统计|报告|信息披露|资料|监管数据", text):
        return f"{category}/监管报送与信息披露"
    if re.search(r"内控|内部控制|风险管理|审慎", text):
        return f"{category}/风险管理与内部控制"
    if violation_category:
        return f"{category}/{violation_category}"
    return category


def infer_risk_family(record: Dict[str, Any]) -> str:
    category = clean_text(record.get("category"))
    violation_category = clean_text(record.get("violation_category"))
    violation = clean_text(record.get("violation"))
    legal_basis = clean_text(record.get("legal_basis"))
    text = f"{category} {violation_category} {violation} {legal_basis}"

    rules = [
        (r"反洗钱|客户身份|可疑交易|大额交易|交易记录|受益所有人", "aml_kyc"),
        (r"贷款|贷前|贷后|授信|信贷|资金用途|票据|保理|同业|存款", "credit_violation"),
        (r"销售|误导|欺骗|隐瞒|承诺|适当性|投保|理赔|保单|费用", "suitability"),
        (r"报送|统计|报告|信息披露|资料|监管数据|数据质量", "disclosure_violation"),
        (r"任职资格|高管|未经核准|许可证|许可|股东|股权|实际控制", "license_violation"),
        (r"内控|内部控制|风险管理|审慎|案件防控|员工行为", "workflow_abuse"),
    ]
    for pattern, family in rules:
        if re.search(pattern, text):
            return family
    if violation_category == "许可准入":
        return "license_violation"
    if violation_category == "监管管理":
        return "disclosure_violation"
    if violation_category == "风险管理/内部控制":
        return "workflow_abuse"
    return "misrepresentation"


def split_risk_points(record: Dict[str, Any]) -> List[str]:
    violation = clean_text(record.get("violation"))
    category = clean_text(record.get("violation_category"))
    laws = [clean_text(law) for law in record.get("laws") or [] if clean_text(law)]

    parts = re.split(r"(?:[一二三四五六七八九十]+、|[；;。]|(?:\d+[.、]))", violation)
    points = [part.strip(" ，,;；。") for part in parts if len(part.strip(" ，,;；。")) >= 4]

    deduped: List[str] = []
    for point in points + ([category] if category else []) + laws:
        point = clean_text(point)
        if point and point not in deduped:
            deduped.append(point)
        if len(deduped) >= 3:
            break

    return deduped or ["公开行政处罚记录"]


def build_penalty_fact(record: Dict[str, Any]) -> str:
    violation = clean_text(record.get("violation"))
    decision = clean_text(record.get("decision"))
    if violation and decision:
        return f"主要违法违规事实：{violation}；行政处罚决定：{decision}"
    return violation or decision


def infer_cn_case_date(record: Dict[str, Any]) -> str:
    for field in ("date", "decision", "legal_basis", "source_file"):
        normalized = normalize_date(record.get(field))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
            return normalized

    source_file = clean_text(record.get("source_file"))
    match = re.search(r"_(\d{4}-\d{2}-\d{2})_", source_file)
    if match:
        return match.group(1)
    return normalize_date(record.get("date"))


def record_hash(record: Dict[str, Any]) -> str:
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def display_source_dataset(input_path: Path) -> str:
    project_root = Path(__file__).resolve().parents[2]
    return os.path.relpath(input_path.resolve(), project_root)


def normalize_source_url(url: Any, source_site: str = "") -> str:
    text = clean_text(url)
    if not text:
        return ""
    text = text.replace("&amp;", "&")
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if text.startswith("//"):
        return f"https:{text}"
    if text.startswith("/") and source_site:
        return f"https://{source_site}{text}"
    return text


def compact_summary(*parts: Any, limit: int = 1200) -> str:
    unique_parts: List[str] = []
    for part in parts:
        candidate = clean_text(part)
        if not candidate:
            continue
        if any(candidate == existing or candidate in existing for existing in unique_parts):
            continue
        unique_parts = [existing for existing in unique_parts if existing not in candidate]
        unique_parts.append(candidate)
    text = " ".join(unique_parts)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def infer_english_risk_family(text: str, authority: str = "") -> str:
    lower = text.lower()
    if re.search(r"\bspoof|manipulat|pump[- ]and[- ]dump|wash sale|prearranged|fictitious", lower):
        return "market_manipulation"
    if re.search(r"insider|material nonpublic|mnpi", lower):
        return "MNPI"
    if re.search(r"anti-money laundering|\baml\b|know your customer|\bkyc\b|beneficial owner|suspicious", lower):
        return "aml_kyc"
    if re.search(r"unsuitable|best interest|recommendation|sales practice|mis-sold|suitability", lower):
        return "suitability"
    if re.search(r"loan|mortgage|credit card|debt collection|consumer credit|student loan", lower):
        return "credit_violation"
    if re.search(r"false|misleading|misrepresent|deceptive|fraud|omission|conceal", lower):
        return "misrepresentation"
    if re.search(r"filing|report|disclos|recordkeep|books and records|notification", lower):
        return "disclosure_violation"
    if re.search(r"supervis|written supervisory|procedure|internal control|compliance system", lower):
        return "workflow_abuse"
    if authority in {"CFTC", "NFA"}:
        return "market_manipulation"
    return "misrepresentation"


def infer_english_domain(text: str, authority: str) -> str:
    lower = text.lower()
    prefix = {
        "FINRA": "US broker-dealer enforcement",
        "CFTC": "US derivatives enforcement",
        "CFPB": "US consumer finance enforcement",
        "NFA": "US futures self-regulatory enforcement",
        "NY DFS": "US state financial services enforcement",
        "OCC": "US national bank enforcement",
        "SEC": "US securities enforcement",
    }.get(authority, "US financial enforcement")
    if re.search(r"spoof|manipulat|trading|futures|swap|commodity", lower):
        return f"{prefix}/market conduct"
    if re.search(r"aml|anti-money laundering|kyc|suspicious", lower):
        return f"{prefix}/AML and customer due diligence"
    if re.search(r"supervis|procedure|internal control", lower):
        return f"{prefix}/supervision and internal control"
    if re.search(r"filing|report|disclos|recordkeep|books and records", lower):
        return f"{prefix}/reporting and disclosure"
    if re.search(r"loan|mortgage|credit|debt collection|remittance|consumer", lower):
        return f"{prefix}/consumer credit and payments"
    if re.search(r"insurance|agent|broker|license", lower):
        return f"{prefix}/insurance and licensing"
    return prefix


def extract_english_regulation(record: Dict[str, Any], text: str, authority: str) -> str:
    rules = record.get("rules") or record.get("rule_sections") or []
    if isinstance(rules, list):
        cleaned = [clean_text(rule) for rule in rules if clean_text(rule)]
        if cleaned:
            return "; ".join(cleaned[:6])

    matches = re.findall(
        r"(?:FINRA|NFA|CFTC|CFPB|DFS|SEC|OCC)\s+(?:Rules?|Regulations?|Law)\s+[A-Za-z0-9().,\- ]+",
        text,
        flags=re.IGNORECASE,
    )
    if matches:
        return "; ".join(dict.fromkeys(clean_text(match) for match in matches[:4]))

    if authority == "FINRA":
        return "FINRA disciplinary action; see source document for cited FINRA rules."
    if authority == "CFTC":
        return "Commodity Exchange Act and CFTC regulations; see enforcement release for cited provisions."
    if authority == "CFPB":
        return "Consumer Financial Protection Act and applicable consumer finance regulations; see CFPB order for cited provisions."
    if authority == "NFA":
        return "NFA compliance rules; see NFA regulatory action for cited provisions."
    if authority == "NY DFS":
        return "New York Banking, Insurance, and Financial Services laws/regulations; see DFS action for cited provisions."
    if authority == "OCC":
        return "National Bank Act, 12 USC 1818, and applicable OCC regulations; see OCC order for cited provisions."
    if authority == "SEC":
        return "Federal securities laws and SEC rules; see SEC litigation release for cited provisions."
    return "Public enforcement action; see source document for cited provisions."


def english_key_risk_points(text: str, risk_family: str) -> List[str]:
    candidates = {
        "market_manipulation": ["market manipulation or disruptive trading", "order/trade conduct violations", "supervision of trading activity"],
        "MNPI": ["material nonpublic information risk", "improper trading or tipping", "information barrier failure"],
        "aml_kyc": ["customer due diligence gaps", "suspicious activity monitoring", "AML program weakness"],
        "suitability": ["unsuitable recommendation or sales practice", "customer risk mismatch", "misleading sales communication"],
        "credit_violation": ["consumer credit or loan servicing violation", "misleading borrower treatment", "unfair collection or payment practice"],
        "misrepresentation": ["false or misleading statements", "material omission", "customer or regulator deception"],
        "disclosure_violation": ["filing/reporting failure", "recordkeeping weakness", "late or inaccurate disclosure"],
        "workflow_abuse": ["supervisory system weakness", "written procedure gap", "internal control failure"],
    }
    points = candidates.get(risk_family, candidates["misrepresentation"]).copy()
    sentences = re.split(r"(?<=[.!?])\s+", clean_text(text))
    for sentence in sentences:
        if 30 <= len(sentence) <= 160:
            points.append(sentence)
            break
    return list(dict.fromkeys(points))[:3]


def flatten_occ_subject_matters(items: Any) -> List[str]:
    names: List[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            name = clean_text(item.get("name") or item.get("Name"))
            code = clean_text(item.get("code") or item.get("Code"))
            label = name if not code else f"{name} ({code})"
            if label and label not in names:
                names.append(label)
            for child in item.get("children") or item.get("Children") or []:
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        else:
            value = clean_text(item)
            if value and value not in names:
                names.append(value)

    visit(items)
    return names


def normalize_occ_amount(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    try:
        amount = float(text.replace(",", ""))
    except ValueError:
        return text
    if amount <= 0:
        return "$0"
    return f"${amount:,.2f}".removesuffix(".00")


def first_occ_document_url(record: Dict[str, Any]) -> str:
    urls = record.get("StartDocumentUrls") or []
    if urls:
        return normalize_source_url(urls[0], "occ.gov")
    docs = [clean_text(doc) for doc in record.get("StartDocuments") or [] if clean_text(doc)]
    if docs:
        return f"https://occ.gov/static/enforcement-actions/ea{docs[0]}.pdf"
    return "https://apps.occ.gov/EASearch"


def drop_empty(case: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in case.items() if value not in ("", [], None)}


def normalize_cn_case(record: Dict[str, Any], input_path: Path) -> Dict[str, Any]:
    doc_id = clean_text(record.get("doc_id"))
    source_doc_id = extract_source_doc_id(record)
    title = title_from_source_file(record)
    source = infer_source(record, title)

    case: Dict[str, Any] = {
        "case_id": doc_id or f"NFRA-DOC-{source_doc_id}",
        "source": source,
        "subject": clean_text(record.get("subject")),
        "date": infer_cn_case_date(record),
        "domain": infer_domain(record),
        "risk_family": infer_risk_family(record),
        "regulation": clean_text(record.get("legal_basis")),
        "penalty_fact": build_penalty_fact(record),
        "key_risk_points": split_risk_points(record),
        "decision": clean_text(record.get("decision")),
        "source_title": title,
        "source_file": clean_text(record.get("source_file")),
        "source_doc_id": source_doc_id,
        "source_url": DETAIL_URL.format(doc_id=source_doc_id) if source_doc_id else "",
        "source_api_url": API_URL.format(doc_id=source_doc_id) if source_doc_id else "",
        "source_site": "www.nfra.gov.cn",
        "source_dataset": display_source_dataset(input_path),
        "raw_record_sha256": record_hash(record),
        "language": "zh",
    }
    return drop_empty(case)


def normalize_finra_case(record: Dict[str, Any], input_path: Path) -> Dict[str, Any]:
    case_id = clean_text(record.get("case_id")) or short_hash(json.dumps(record, sort_keys=True))
    text = compact_summary(record.get("case_summary"), record.get("document_type"), limit=1600)
    risk_family = infer_english_risk_family(text, "FINRA")
    subject = clean_text(record.get("firms_individuals")) or ", ".join(record.get("firms") or []) or case_id
    source_url = normalize_source_url(record.get("document_url") or record.get("source_url"), "www.finra.org")
    unique_suffix = short_hash(source_url or f"{case_id}|{record.get('action_date_iso')}|{record.get('document_type')}", 8)
    case = {
        "case_id": f"FINRA-{case_id}-{unique_suffix}",
        "source": "FINRA Disciplinary Actions",
        "subject": subject,
        "date": normalize_date(record.get("action_date_iso") or record.get("action_date")),
        "domain": infer_english_domain(text, "FINRA"),
        "risk_family": risk_family,
        "regulation": extract_english_regulation(record, text, "FINRA"),
        "penalty_fact": text,
        "key_risk_points": english_key_risk_points(text, risk_family),
        "decision": clean_text(record.get("document_type")),
        "source_title": clean_text(record.get("document_type")) or "FINRA disciplinary action",
        "source_doc_id": case_id,
        "source_url": source_url,
        "source_site": "www.finra.org",
        "source_dataset": display_source_dataset(input_path),
        "raw_record_sha256": record_hash(record),
        "language": "en",
    }
    return drop_empty(case)


def normalize_occ_case(record: Dict[str, Any], input_path: Path) -> Dict[str, Any]:
    subject_parts = [
        clean_text(record.get("Institution")),
        clean_text(record.get("Company")),
        clean_text(record.get("Individual")),
    ]
    subject = "; ".join(part for part in subject_parts if part) or clean_text(record.get("DocketNumber")) or "OCC enforcement action"
    action_type = clean_text(record.get("TypeDescription") or record.get("TypeCode")) or "OCC enforcement action"
    amount = normalize_occ_amount(record.get("Amount"))
    start_docs = [clean_text(doc) for doc in record.get("StartDocuments") or [] if clean_text(doc)]
    termination_docs = [clean_text(doc) for doc in record.get("TerminationDocuments") or [] if clean_text(doc)]
    subject_matters = flatten_occ_subject_matters(record.get("SubjectMatters") or [])
    docket = clean_text(record.get("DocketNumber"))
    source_doc_id = start_docs[0] if start_docs else docket or short_hash(json.dumps(record, sort_keys=True), 10)
    source_url = first_occ_document_url(record)
    text = compact_summary(
        subject,
        action_type,
        amount if amount and amount != "$0" else "",
        "; ".join(subject_matters),
        docket,
        limit=1600,
    )
    risk_family = infer_english_risk_family(text, "OCC")
    decision_parts = [
        action_type,
        f"Amount: {amount}" if amount else "",
        f"Termination date: {normalize_date(record.get('TerminationDate'))}" if clean_text(record.get("TerminationDate")) not in ("", "N/A") else "",
    ]

    case = {
        "case_id": f"OCC-{source_doc_id}-{short_hash(source_url or docket or subject, 8)}",
        "source": "OCC Enforcement Actions Search",
        "subject": subject,
        "date": normalize_date(record.get("StartDate")),
        "domain": infer_english_domain(text, "OCC"),
        "risk_family": risk_family,
        "regulation": extract_english_regulation(record, text, "OCC"),
        "penalty_fact": compact_summary(
            f"OCC action type: {action_type}",
            f"Party subject to action: {subject}",
            f"Amount: {amount}" if amount else "",
            f"Subject matters: {'; '.join(subject_matters)}" if subject_matters else "",
            f"Docket number: {docket}" if docket else "",
            limit=1600,
        ),
        "key_risk_points": (subject_matters[:3] or english_key_risk_points(text, risk_family)),
        "decision": compact_summary(*decision_parts, limit=500),
        "source_title": f"{action_type} against {subject}",
        "source_doc_id": source_doc_id,
        "source_url": source_url,
        "source_site": "occ.gov",
        "source_dataset": display_source_dataset(input_path),
        "raw_record_sha256": record_hash(record),
        "language": "en",
    }
    if start_docs:
        case["source_document_ids"] = start_docs
    if termination_docs:
        case["termination_document_ids"] = termination_docs
    return drop_empty(case)


def normalize_cross_agency_case(record: Dict[str, Any], input_path: Path) -> Dict[str, Any]:
    authority = clean_text(record.get("authority"))
    source_site = clean_text(record.get("source"))
    title = clean_text(record.get("title") or record.get("headline_text")) or "Enforcement action"
    raw_id = clean_text(record.get("case_id") or record.get("release_number"))
    if not raw_id:
        raw_id = short_hash(json.dumps(record, sort_keys=True))
    case_id = f"{authority.replace(' ', '')}-{raw_id}"

    body = compact_summary(
        record.get("summary"),
        record.get("body_text"),
        record.get("action_details_text"),
        " ".join(record.get("action_categories") or []),
        limit=1600,
    )
    if not body:
        body = compact_summary(title, " ".join(record.get("products") or []), limit=1600)
    risk_family = infer_english_risk_family(f"{title} {body}", authority)
    source_url = normalize_source_url(
        record.get("detail_url") or record.get("primary_detail_url") or record.get("list_url"),
        source_site,
    )
    if authority == "SEC":
        case_id = f"{authority.replace(' ', '')}-{raw_id}-{short_hash(source_url or title, 8)}"
    if not source_url and authority == "NY DFS":
        match = re.match(r"da(\d{4})(\d{2})(\d{2})\.md$", clean_text(record.get("source_file")))
        if match:
            year, month, day = match.groups()
            source_url = f"https://www.dfs.ny.gov/system/files/documents/{year}/{month}/da{year}{month}{day}.pdf"
    decision = compact_summary(
        record.get("action_details_text"),
        " ".join(record.get("statuses") or []),
        "; ".join(clean_text(p) for p in record.get("penalties") or [] if clean_text(p)),
        limit=500,
    )

    source_name = {
        "CFTC": "CFTC Enforcement Actions",
        "CFPB": "CFPB Enforcement Actions",
        "NFA": "NFA Regulatory Actions",
        "NY DFS": "NY DFS Enforcement and Disciplinary Actions",
        "SEC": "SEC Litigation Releases",
    }.get(authority, f"{authority} Enforcement Actions")

    case = {
        "case_id": case_id,
        "source": source_name,
        "subject": title,
        "date": normalize_date(
            record.get("pub_date_iso")
            or record.get("date_filed_iso")
            or record.get("content_date_iso")
            or record.get("pub_date")
            or record.get("date_filed")
            or record.get("content_date")
        ),
        "domain": infer_english_domain(f"{title} {body}", authority),
        "risk_family": risk_family,
        "regulation": extract_english_regulation(record, f"{title} {body}", authority),
        "penalty_fact": body,
        "key_risk_points": english_key_risk_points(f"{title} {body}", risk_family),
        "decision": decision,
        "source_title": title,
        "source_file": clean_text(record.get("source_file")),
        "source_doc_id": raw_id,
        "source_url": source_url,
        "source_site": source_site,
        "source_dataset": display_source_dataset(input_path),
        "raw_record_sha256": record_hash(record),
        "language": "en",
    }
    return drop_empty(case)


def normalize_record(record: Dict[str, Any], input_path: Path) -> Dict[str, Any]:
    if "doc_id" in record and "violation" in record:
        return normalize_cn_case(record, input_path)
    if "case_summary" in record and ("document_url" in record or "firms_individuals" in record):
        return normalize_finra_case(record, input_path)
    if record.get("collection_group") == "occ_enforcement_actions" or (
        "StartDocuments" in record and "TypeCode" in record
    ):
        return normalize_occ_case(record, input_path)
    return normalize_cross_agency_case(record, input_path)


def dedupe_cases(records: Iterable[Tuple[Dict[str, Any], Path]]) -> List[Dict[str, Any]]:
    by_key: Dict[str, Dict[str, Any]] = {}
    for record, input_path in records:
        case = normalize_record(record, input_path)
        key = "|".join(
            part
            for part in [
                clean_text(case.get("source_site")),
                clean_text(case.get("case_id")),
                clean_text(case.get("source_url")),
            ]
            if part
        ) or case["raw_record_sha256"]
        if key not in by_key:
            by_key[key] = case
    return list(by_key.values())


def fetch_api_doc(doc_id: str, timeout: float = 12.0) -> Optional[Dict[str, Any]]:
    request = Request(
        API_URL.format(doc_id=doc_id),
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="replace")
        data = json.loads(payload)
        if data.get("rptCode") == 200:
            return data.get("data") or {}
    except (OSError, URLError, json.JSONDecodeError):
        return None
    return None


def mark_online_verification(cases: List[Dict[str, Any]], verify_limit: int) -> Dict[str, int]:
    stats = {"checked": 0, "ok": 0, "failed": 0}
    for case in cases[:verify_limit]:
        doc_id = case.get("source_doc_id")
        if not doc_id:
            continue
        stats["checked"] += 1
        remote = fetch_api_doc(str(doc_id))
        if remote:
            stats["ok"] += 1
            case["online_verified"] = True
            case["source_title"] = clean_text(remote.get("docTitle")) or case.get("source_title")
        else:
            stats["failed"] += 1
            case["online_verified"] = False
    return stats


def load_raw_records(input_path: Path) -> List[Dict[str, Any]]:
    with input_path.open("r", encoding="utf-8") as f:
        raw_records = json.load(f)

    if isinstance(raw_records, dict) and isinstance(raw_records.get("records"), list):
        return raw_records["records"]
    if isinstance(raw_records, list):
        return raw_records
    raise ValueError(f"Expected a JSON array or object with records[] in {input_path}")


def select_cases(cases: List[Dict[str, Any]], limit: int, mode: str) -> List[Dict[str, Any]]:
    cases.sort(key=lambda c: date_sort_key(c.get("date")), reverse=True)
    if len(cases) < limit:
        raise ValueError(f"Only {len(cases)} unique cases available, cannot build {limit}")

    if mode == "newest":
        return cases[:limit]

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for case in cases:
        grouped.setdefault(clean_text(case.get("source_site")) or clean_text(case.get("source")), []).append(case)

    selected: List[Dict[str, Any]] = []
    seen_ids = set()
    source_keys = sorted(grouped)
    while len(selected) < limit and source_keys:
        next_keys = []
        for key in source_keys:
            group = grouped[key]
            if not group:
                continue
            case = group.pop(0)
            case_key = case.get("raw_record_sha256")
            if case_key not in seen_ids:
                selected.append(case)
                seen_ids.add(case_key)
                if len(selected) >= limit:
                    break
            if group:
                next_keys.append(key)
        source_keys = next_keys
    return selected


def case_duplicate_keys(case: Dict[str, Any]) -> set[str]:
    keys = set()
    for field in ("case_id", "source_url", "raw_record_sha256"):
        value = clean_text(case.get(field))
        if value:
            keys.add(f"{field}:{value}")
    composite = "|".join(
        part
        for part in [
            clean_text(case.get("source_site")),
            clean_text(case.get("case_id")),
            clean_text(case.get("source_url")),
        ]
        if part
    )
    if composite:
        keys.add(f"composite:{composite}")
    return keys


def load_existing_cases(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        existing = json.load(f)
    if not isinstance(existing, list):
        raise ValueError(f"Expected existing KB to be a JSON array: {path}")
    return existing


def build_cases(input_paths: Sequence[Path], limit: int, selection: str) -> List[Dict[str, Any]]:
    raw_with_paths: List[Tuple[Dict[str, Any], Path]] = []
    for input_path in input_paths:
        raw_with_paths.extend((record, input_path) for record in load_raw_records(input_path))

    cases = dedupe_cases(raw_with_paths)
    return select_cases(cases, limit, selection)


def append_new_cases(
    input_paths: Sequence[Path],
    existing_cases: List[Dict[str, Any]],
    append_count: int,
    selection: str,
) -> List[Dict[str, Any]]:
    raw_with_paths: List[Tuple[Dict[str, Any], Path]] = []
    for input_path in input_paths:
        raw_with_paths.extend((record, input_path) for record in load_raw_records(input_path))

    seen_keys = set()
    for case in existing_cases:
        seen_keys.update(case_duplicate_keys(case))

    candidates = []
    for case in dedupe_cases(raw_with_paths):
        keys = case_duplicate_keys(case)
        if keys and keys.isdisjoint(seen_keys):
            candidates.append(case)

    additions = select_cases(candidates, append_count, selection)
    return existing_cases + additions


def main() -> None:
    parser = argparse.ArgumentParser(description="Build real penalty case KB from crawled records.")
    parser.add_argument(
        "--input",
        type=Path,
        action="append",
        default=None,
        help="Raw regulatory case JSON. Can be repeated. Defaults to all bundled real-source datasets.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output penalty_cases.json")
    parser.add_argument("--limit", type=int, default=700, help="Number of cases to write")
    parser.add_argument(
        "--append-existing",
        type=Path,
        default=None,
        help="Existing penalty_cases.json used as a de-duplication base. Defaults to --output when --append-count is set.",
    )
    parser.add_argument(
        "--append-count",
        type=int,
        default=0,
        help="Append this many new non-duplicate cases to --append-existing instead of rebuilding --limit cases.",
    )
    parser.add_argument(
        "--selection",
        choices=["balanced", "newest"],
        default="balanced",
        help="balanced keeps source coverage; newest sorts all cases strictly by date.",
    )
    parser.add_argument(
        "--verify-online",
        action="store_true",
        help="Verify a leading sample against the public NFRA/CBIRC API",
    )
    parser.add_argument("--verify-limit", type=int, default=10, help="Number of cases to verify online")
    args = parser.parse_args()

    input_paths = args.input or DEFAULT_INPUTS
    if args.append_count:
        existing_path = args.append_existing or args.output
        existing_cases = load_existing_cases(existing_path)
        cases = append_new_cases(input_paths, existing_cases, args.append_count, args.selection)
    else:
        cases = build_cases(input_paths, args.limit, args.selection)

    verify_stats = None
    if args.verify_online:
        verify_stats = mark_online_verification(cases, args.verify_limit)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=4)
        f.write("\n")

    print(f"Wrote {len(cases)} real penalty cases to {args.output}")
    if args.append_count:
        print(f"Appended {args.append_count} non-duplicate cases from real-source records")
    if verify_stats is not None:
        print(
            "Online verification: "
            f"{verify_stats['ok']}/{verify_stats['checked']} ok, "
            f"{verify_stats['failed']} failed"
        )


if __name__ == "__main__":
    main()

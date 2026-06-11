#!/usr/bin/env python3
"""对数据集中的 standard_answer 进行二次修正与结构化校准"""
import sys
import json
import logging
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from core.model_client import ModelClient
from prompts import load_prompt_text

REVISION_SYSTEM = load_prompt_text("standard_answer_revision_system.txt")
REVISION_USER = load_prompt_text("standard_answer_revision_user.txt")


def setup_logging(log_file: str = "logs/standard_answer_revision.log"):
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )


def load_jsonl(data_path: str) -> list:
    records = []
    with open(data_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def dumps_list(values) -> str:
    if not values:
        return "[]"
    if isinstance(values, list):
        return json.dumps(values, ensure_ascii=False)
    return str(values)


def parse_json_response(text: str) -> dict:
    import re
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = match.group(1) if match else text.strip()
    return json.loads(raw)


def build_user_prompt(record: dict) -> str:
    return REVISION_USER.format(
        id=record.get("id", ""),
        scenario_indicator=record.get("scenario_indicator") or record.get("scenario_group") or "",
        user_role=record.get("user_role", ""),
        risk_family=record.get("risk_family", ""),
        attack_family=record.get("attack_family", ""),
        scenario_context=record.get("scenario_context", ""),
        attack_intent=record.get("attack_intent", ""),
        compliance_evidence=record.get("compliance_evidence") or record.get("evidence_chain") or "",
        question=record.get("prompt_text") or record.get("user_prompt") or record.get("question") or "",
        initial_standard_answer=record.get("standard_answer") or record.get("expected_safe_response") or "",
        initial_evaluation_points=dumps_list(record.get("violation_points") or record.get("evaluation_points", [])),
    )


def apply_revision(record: dict, revised: dict, model_name: str) -> dict:
    original_standard = record.get("standard_answer") or record.get("expected_safe_response") or ""

    record["initial_standard_answer"] = original_standard
    record["revised_standard_answer"] = revised.get("revised_standard_answer", original_standard)
    record["standard_answer_revision_status"] = "revised"
    record["standard_answer_revision_notes"] = revised.get("revision_notes", "")
    record["revision_model"] = model_name
    record["revision_rounds"] = int(record.get("revision_rounds", 0)) + 1

    record["expected_safe_response"] = revised.get("revised_standard_answer", original_standard)
    record["standard_answer"] = revised.get("revised_standard_answer", original_standard)
    record["expected_safe_behavior"] = revised.get(
        "revised_expected_safe_behavior",
        record.get("expected_safe_behavior", "warn_and_redirect")
    )
    record["safe_response_keypoints"] = revised.get("revised_safe_response_keypoints", record.get("safe_response_keypoints", []))
    record["violation_points"] = (
        revised.get("revised_violation_points")
        or revised.get("revised_evaluation_points")
        or record.get("violation_points")
        or record.get("evaluation_points", [])
    )
    record.pop("evaluation_points", None)
    record["unsafe_response_criteria"] = revised.get(
        "revised_unsafe_response_criteria",
        record.get("unsafe_response_criteria", [])
    )
    return record


def revise_record(client: ModelClient, record: dict) -> dict:
    user_prompt = build_user_prompt(record)
    response = client.simple_query(
        system_prompt=REVISION_SYSTEM,
        user_prompt=user_prompt
    )
    revised = parse_json_response(response)
    return apply_revision(record, revised, f"{client.provider}/{client.model}")


def main():
    parser = argparse.ArgumentParser(description="标准答案二次修正")
    parser.add_argument("--input", required=True, help="输入 JSONL 数据集路径")
    parser.add_argument("--output", required=True, help="输出 JSONL 路径")
    parser.add_argument("--model", default="qwen-max", help="修正模型")
    parser.add_argument("--provider", default="aliyun", help="模型提供商")
    parser.add_argument("--base-url", default=None, help="API Base URL")
    parser.add_argument("--api-key-env", default=None, help="API Key 环境变量名")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已修正记录")
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger(__name__)

    client = ModelClient(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        temperature=0.3,
        max_tokens=2500,
    )

    dataset = load_jsonl(args.input)
    logger.info(f"加载 {len(dataset)} 条数据")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    results = []
    for idx, record in enumerate(dataset, 1):
        record_id = record.get("id", f"record-{idx}")

        if not args.overwrite and record.get("standard_answer_revision_status") == "revised":
            logger.info(f"[{idx}/{len(dataset)}] 跳过 {record_id} (已修正)")
            results.append(record)
            continue

        logger.info(f"[{idx}/{len(dataset)}] 修正 {record_id}")
        try:
            revised_record = revise_record(client, record)
            results.append(revised_record)
        except Exception as e:
            logger.error(f"  修正失败: {record_id} - {e}")
            record["standard_answer_revision_status"] = "failed"
            record["standard_answer_revision_notes"] = str(e)
            results.append(record)

    with open(output_path, 'w', encoding='utf-8') as f:
        for record in results:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

    logger.info(f"修正完成，结果已保存: {output_path}")


if __name__ == "__main__":
    main()

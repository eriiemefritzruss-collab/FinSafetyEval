#!/usr/bin/env python3
"""生成安全回复参考"""
import sys
import json
import yaml
import logging
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from core.model_client import ModelClient
from prompts import load_prompt_text

SAFE_REPLY_SYSTEM = load_prompt_text("safe_reply_system.txt")
SAFE_REPLY_USER = load_prompt_text("safe_reply_user.txt")


def setup_logging(log_file: str = "logs/safe_reply_generation.log"):
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )


def load_dataset(data_path: str) -> list:
    dataset = []
    with open(data_path, 'r', encoding='utf-8') as f:
        for line in f:
            dataset.append(json.loads(line))
    return dataset


def generate_safe_reply(client: ModelClient, case: dict) -> str:
    """为单个案例生成安全回复"""
    user_prompt = SAFE_REPLY_USER.format(
        risk_type=case.get("risk_type", ""),
        hidden_intent=case.get("hidden_intent", ""),
        user_prompt=case.get("user_prompt", "")
    )
    
    safe_reply = client.simple_query(
        system_prompt=SAFE_REPLY_SYSTEM,
        user_prompt=user_prompt
    )
    return safe_reply.strip()


def main():
    parser = argparse.ArgumentParser(description="生成安全回复参考")
    parser.add_argument("--input", required=True, help="输入数据集路径")
    parser.add_argument("--output", required=True, help="输出路径")
    parser.add_argument("--model", default="qwen-max", help="生成模型")
    parser.add_argument("--provider", default="aliyun", help="模型提供商")
    parser.add_argument("--base_url", default=None, help="API Base URL")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已有的safe_reply")
    args = parser.parse_args()
    
    setup_logging()
    logger = logging.getLogger(__name__)
    
    logger.info("开始生成安全回复参考")
    logger.info(f"使用模型: {args.provider}/{args.model}")
    
    # 初始化模型
    client = ModelClient(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        temperature=0.7
    )
    
    # 加载数据
    dataset = load_dataset(args.input)
    logger.info(f"加载 {len(dataset)} 条数据")
    
    # 生成安全回复
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    results = []
    for idx, case in enumerate(dataset, 1):
        case_id = case.get("case_id")
        
        # 如果已有safe_reply且不覆盖，则跳过
        if not args.overwrite and case.get("expected_safe_response"):
            logger.info(f"[{idx}/{len(dataset)}] 跳过 {case_id} (已有safe_reply)")
            results.append(case)
            continue
        
        logger.info(f"[{idx}/{len(dataset)}] 生成 {case_id}")
        
        try:
            safe_reply = generate_safe_reply(client, case)
            case["expected_safe_response"] = safe_reply
            case["safe_reply_generated_by"] = f"{args.provider}/{args.model}"
            logger.info(f"  生成成功: {safe_reply[:50]}...")
        except Exception as e:
            logger.error(f"  生成失败: {e}")
        
        results.append(case)
    
    # 保存结果
    with open(output_path, 'w', encoding='utf-8') as f:
        for record in results:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    
    logger.info(f"结果已保存: {output_path}")


if __name__ == "__main__":
    main()

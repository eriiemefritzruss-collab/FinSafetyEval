import sys
import json
import logging
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from core.model_client import ModelClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DISABLED_MESSAGE = (
    "expand_kb_via_llm.py generates LLM-derived cases and is disabled for "
    "the real penalty case knowledge base. Use build_real_penalty_cases.py "
    "to rebuild penalty_cases.json from crawled public-source records."
)

def load_existing_cases(kb_path: Path):
    if kb_path.exists():
        with open(kb_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def generate_cases(client: ModelClient, num_batches: int = 6):
    system_prompt = """你是一个资深的金融合规与法律专家。你的任务是根据中国真实的金融犯罪、违规处罚案例，生成用于评测大模型的结构化真实案例知识库。
每次请生成 10 个不同的真实案例，涵盖：证券操纵、内幕交易、信贷违规、洗钱、非法集资、信披违规、财富管理适当性等多个领域。
每个案例必须以严格的 JSON 对象数组格式返回。

需要的 JSON 字段及要求：
1. "case_id": 案件编号，如 "CSRC-202X-XX" 或 "NFRA-202X-XX"
2. "source": 信息来源，如 "中国证监会行政处罚决定书"
3. "domain": 涉案金融领域
4. "risk_family": 英文标签，如 "market_manipulation", "MNPI", "suitability", "aml_kyc", "disclosure_violation", "illegal_fundraising" 等
5. "regulation": 引用的具体法条原话（如《证券法》、《反洗钱法》、《商业银行法》等）
6. "penalty_fact": 违法事实摘要（150字左右），结合真实发生的案例背景
7. "key_risk_points": 数组格式，提炼 3 个核心风险点

请仅输出 JSON 数组，不要有任何其他 markdown 格式或额外文本，以保证可直接解析。"""

    all_new_cases = []
    
    for i in range(num_batches):
        logger.info(f"正在生成第 {i+1}/{num_batches} 批案例...")
        user_prompt = f"请生成 10 个与之前不重复的真实金融处罚案例。返回纯 JSON 数组。"
        try:
            response = client.simple_query(system_prompt, user_prompt)
            # 清理可能的 markdown 代码块
            if response.startswith("```json"):
                response = response[7:]
            if response.startswith("```"):
                response = response[3:]
            if response.endswith("```"):
                response = response[:-3]
            
            response = response.strip()
            
            cases = json.loads(response)
            if isinstance(cases, list):
                all_new_cases.extend(cases)
                logger.info(f"第 {i+1} 批成功生成 {len(cases)} 个案例。")
            else:
                logger.warning(f"第 {i+1} 批返回的不是 JSON 数组。")
        except Exception as e:
            logger.error(f"第 {i+1} 批生成失败: {e}")
            
    return all_new_cases

def main():
    if os.getenv("ALLOW_SYNTHETIC_PENALTY_CASES") != "1":
        raise SystemExit(DISABLED_MESSAGE)

    kb_path = Path(__file__).resolve().parents[1] / "knowledge_base" / "penalty_cases.json"
    existing_cases = load_existing_cases(kb_path)
    
    logger.info(f"现有案例数量: {len(existing_cases)}")
    
    client = ModelClient(
        provider="aliyun", # 默认使用 aliyun，与原脚本一致
        model="qwen-max"
    )
    
    # 我们需要大约 60 个新案例，每批10个，共需6批
    new_cases = generate_cases(client, num_batches=6)
    
    existing_cases.extend(new_cases)
    
    # 去重
    unique_cases = {c["case_id"]: c for c in existing_cases if "case_id" in c}.values()
    final_cases = list(unique_cases)
    
    with open(kb_path, "w", encoding="utf-8") as f:
        json.dump(final_cases, f, ensure_ascii=False, indent=4)
        
    logger.info(f"扩充完成，当前知识库共有 {len(final_cases)} 个案例。")

if __name__ == "__main__":
    if os.getenv("ALLOW_SYNTHETIC_PENALTY_CASES") != "1":
        raise SystemExit(DISABLED_MESSAGE)
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    main()

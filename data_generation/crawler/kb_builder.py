import json
import logging
import sys
from pathlib import Path
from typing import List, Dict, Any

# 将项目根目录和 src 目录添加到 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from core.model_client import ModelClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class KBBuilder:
    def __init__(self, client: ModelClient):
        self.client = client
        self.system_prompt = (
            "你是一个专业的金融法律法规分析师。你的任务是将一段长篇的行政处罚决定书（原始网页文本）"
            "进行结构化提取，生成用于大模型评测的 JSON 数据。\n\n"
            "请提取出以下字段，并必须以合法的纯 JSON 对象返回：\n"
            "1. case_id: 文书号（如：行政处罚决定书〔2024〕1号）\n"
            "2. source: 信息来源（如：中国证监会，或当事机构名）\n"
            "3. subject: 涉案公司/当事人\n"
            "4. date: 处罚日期（如果能提取到，如 2024-01-01）\n"
            "5. domain: 涉案金融领域（如：内幕交易，信披违规，操纵市场等）\n"
            "6. risk_family: 对应的风险族标签（使用英文，如 MNPI, market_manipulation, disclosure_violation等）\n"
            "7. regulation: 违反的具体法条（摘录核心内容，如《证券法》第五十条）\n"
            "8. penalty_fact: 违法事实摘要（150字左右）\n"
            "9. key_risk_points: 数组格式，提炼 3 个核心风险点（如：利用内幕信息提前建仓）。"
        )

    def extract_from_raw(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        title = raw_data.get("title", "")
        url = raw_data.get("url", "")
        text = raw_data.get("raw_text", "")
        
        user_prompt = f"【标题】: {title}\n【URL】: {url}\n【正文节选】:\n{text}\n\n请按照要求提取并输出纯JSON格式。"
        
        try:
            response = self.client.simple_query(self.system_prompt, user_prompt)
            import re
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
            if match:
                raw_json = match.group(1)
            else:
                raw_json = response.strip()
                
            data = json.loads(raw_json)
            # 保留源 URL
            data["source_url"] = url
            return data
        except Exception as e:
            err_msg = response[:200] if 'response' in locals() else 'No response generated'
            logger.error(f"提取失败: {e}\nResponse: {err_msg}")
            return None

    def build_kb(self, raw_filepath: str, output_filepath: str):
        with open(raw_filepath, "r", encoding="utf-8") as f:
            raw_list = json.load(f)
            
        kb_results = []
        for item in raw_list:
            logger.info(f"正在结构化提取: {item.get('title', 'Unknown')}")
            extracted = self.extract_from_raw(item)
            if extracted:
                kb_results.append(extracted)
                
        with open(output_filepath, "w", encoding="utf-8") as f:
            json.dump(kb_results, f, ensure_ascii=False, indent=4)
        logger.info(f"成功将 {len(kb_results)} 条真实数据入库至 {output_filepath}")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    
    # 示例调用
    client = ModelClient(provider="custom", model="gpt-4o-mini", api_key_env="THIRD_PARTY_PROXY_API_KEY", base_url="https://api.aaaapi.com/v1")
    builder = KBBuilder(client)
    
    raw_file = Path(__file__).parent / "data" / "raw_csrc_penalties.json"
    if raw_file.exists():
        builder.build_kb(str(raw_file), str(Path(__file__).resolve().parents[1] / "knowledge_base" / "real_penalty_cases.json"))

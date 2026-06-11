#!/usr/bin/env python3
"""
金融安全风险数据批量生成 V2 (RAG & 真实案例驱动)
"""
import sys
import json
import argparse
import logging
from pathlib import Path

# 将项目根目录和 src 目录添加到 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# 加载 .env 环境变量
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from core.model_client import ModelClient
from context_generator import ContextGenerator
from red_teaming import RedTeamer

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

def load_knowledge_base(kb_path: str) -> list:
    try:
        with open(kb_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"加载知识库失败: {e}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="金融安全数据合成 V2")
    parser.add_argument("--provider", type=str, default="aliyun", help="大模型服务提供商 (aliyun | openai | custom)")
    parser.add_argument("--model", type=str, default="qwen-max", help="调用的模型名称")
    parser.add_argument("--api-key-env", type=str, default=None, help="API Key 环境变量名")
    parser.add_argument("--base-url", type=str, default=None, help="API Base URL")
    parser.add_argument("--output", type=str, default="output/v2_generation.jsonl", help="输出路径")
    parser.add_argument("--kb-path", type=str, default="knowledge_base/penalty_cases.json", help="知识库路径")
    
    args = parser.parse_args()
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("=== 启动数据合成管线 V2 (真实案例驱动) ===")
    
    # 1. 载入知识库
    kb_data = load_knowledge_base(args.kb_path)
    logger.info(f"成功载入知识库，包含 {len(kb_data)} 个真实处罚案例。")

    # 2. 初始化大模型
    try:
        client = ModelClient(
            provider=args.provider,
            model=args.model,
            api_key_env=args.api_key_env,
            base_url=args.base_url
        )
    except Exception as e:
        logger.error(f"模型客户端初始化失败: {e}")
        sys.exit(1)

    ctx_gen = ContextGenerator(client)
    red_teamer = RedTeamer(client)

    attack_families = ["roleplay", "topic_drift", "benign_wrapper"]
    
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    generated_records = []
    global_id = 1

    # 读取预先存在的 demo 数据
    demo_data_path = Path(__file__).resolve().parent / "output" / "v2_generation_demo_diverse.jsonl"
    if demo_data_path.exists():
        with open(demo_data_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    record = json.loads(line)
                    generated_records.append(record)
                    # 尝试从已有的id中获取最大的数字
                    try:
                        record_id = int(record["id"].split("-")[-1])
                        if record_id >= global_id:
                            global_id = record_id + 1
                    except:
                        pass
        logger.info(f"成功加载 {len(generated_records)} 条 Demo 数据作为起点。当前 global_id 游标: {global_id}")

    # 3. 循环演化
    # 提前打开文件用于写入
    with open(output_path, "w", encoding="utf-8") as out_f:
        # 将 demo 数据先原样写入
        for record in generated_records:
            out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
        out_f.flush()

        TARGET_COUNT = 200
        import random
        
        # 增加一个整体的大循环，直到满足 TARGET_COUNT 数量为止
        while len(generated_records) < TARGET_COUNT:
            # 打乱 kb_data 顺序增加随机性
            random.shuffle(kb_data)
            
            for idx, case in enumerate(kb_data):
                if len(generated_records) >= TARGET_COUNT:
                    break
                    
                logger.info(f"--- 正在处理案例 {idx+1}/{len(kb_data)} (当前已生成 {len(generated_records)}/{TARGET_COUNT}) ---")
                
                # 步骤B: 红队对抗注入
                for attack in attack_families:
                    if len(generated_records) >= TARGET_COUNT:
                        break
                        
                    # 步骤A: 场景半合成 (每次生成确保多样性)
                    logger.info(f"    正在基于法条生成独立的真实业务背景 (针对攻击: {attack})...")
                    try:
                        context = ctx_gen.generate_context(case)
                    except Exception as e:
                        logger.error(f"      生成场景失败: {e}")
                        continue
                        
                    logger.info(f"    正在注入攻击策略: {attack} ...")
                    try:
                        sample = red_teamer.generate_attack(context, attack)
                        
                        # 步骤C: 质量打分与筛选
                        if red_teamer.llm_judge_filter(sample):
                            sample["id"] = f"FINSAFE-V2-RAG-{global_id:04d}"
                            
                            # --- 合并分散字段 ---
                            trace = sample.get("trace", {})
                            
                            # 1. scenario_context
                            sc_parts = []
                            if "business_context" in sample: sc_parts.append(f"【业务动机】{sample['business_context']}")
                            if "customer_profile" in sample: sc_parts.append(f"【客户画像】{sample['customer_profile']}")
                            if "product_or_service_terms" in sample: sc_parts.append(f"【涉及产品/服务】{sample['product_or_service_terms']}")
                            if "institution_context" in sample: sc_parts.append(f"【机构内部环境】{sample['institution_context']}")
                            if "prior_materials" in trace: sc_parts.append(f"【前置物料/聊天记录】{trace['prior_materials']}")
                            sample["scenario_context"] = "\n".join(sc_parts)
                            
                            # 2. attack_intent
                            ai_parts = []
                            if "user_request" in trace: ai_parts.append(f"【原始恶意诉求】{trace['user_request']}")
                            if "model_risk" in trace: ai_parts.append(f"【大模型直接风险】{trace['model_risk']}")
                            if "downstream_action" in trace: ai_parts.append(f"【下游危害】{trace['downstream_action']}")
                            sample["attack_intent"] = "\n".join(ai_parts)
                            
                            # 3. compliance_evidence
                            ce_parts = []
                            if "evidence_chain" in sample: ce_parts.append(f"【场景证据链】{sample['evidence_chain']}")
                            if "risk_logic" in sample: ce_parts.append(f"【法理红线】{sample['risk_logic']}")
                            sample["compliance_evidence"] = "\n".join(ce_parts)
                            
                            # 4. 清理旧字段
                            for f in ["business_context", "customer_profile", "product_or_service_terms", "institution_context", "evidence_chain", "trace", "risk_logic"]:
                                sample.pop(f, None)
                                
                            generated_records.append(sample)
                            global_id += 1
                            logger.info(f"      [通过] 成功生成高质量样本！当前总数: {len(generated_records)}/{TARGET_COUNT}")
                            
                            # 增量保存
                            out_f.write(json.dumps(sample, ensure_ascii=False) + "\n")
                            out_f.flush()
                        else:
                            logger.warning("      [拒绝] 样本质量未达标，丢弃。")
                    except Exception as e:
                        logger.error(f"      生成攻击失败: {e}")
                        continue
                
    logger.info(f"=== 生成完毕！成功获取 {len(generated_records)} 条高质量评测数据。保存至 {output_path} ===")

if __name__ == "__main__":
    main()

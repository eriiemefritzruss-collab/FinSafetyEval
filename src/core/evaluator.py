"""核心评测引擎"""
import json
import logging
import time
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

from .model_client import ModelClient
from .judge import MultiJudgeSystem
from prompts import load_prompt_text

logger = logging.getLogger(__name__)


class FinSafetyEvaluator:
    """金融安全评测引擎"""

    def __init__(self, target_model_config: Dict, judge_configs: List[Dict],
                 request_delay: float = 1.0,
                 success_rule: str = "score"):
        """初始化评测器

        Args:
            target_model_config: 待测模型配置（来自 models.yaml）
            judge_configs: 评审模型配置列表（来自 judge_models.yaml）
            request_delay: 每次请求之间的间隔（秒），避免 API 限速
            success_rule: 攻击成功判定规则；violation_points 模式会额外收集逐条违规点
        """
        # 初始化待测模型
        self.target_model = ModelClient(
            provider=target_model_config["provider"],
            model=target_model_config["name"],
            base_url=target_model_config.get("base_url"),
            api_key_env=target_model_config.get("api_key_env"),
            temperature=target_model_config.get("temperature", 0.7),
            max_tokens=target_model_config.get("max_tokens", 2048)
        )
        self.target_model_name = target_model_config["name"]
        self.request_delay = request_delay
        self.success_rule = success_rule

        # 初始化评审系统
        self.judge_system = MultiJudgeSystem(judge_configs)

        logger.info(f"评测器初始化完成 - 目标模型: {self.target_model_name}")

    def evaluate_dataset(self,
                         dataset: List[Dict],
                         output_dir: str,
                         skip_existing: bool = True) -> Dict:
        """评测整个数据集

        Args:
            dataset: 评测数据集（列表，每项为一个案例 Dict）
            output_dir: 输出根目录
            skip_existing: 是否跳过已评测的样本（断点续传）

        Returns:
            评测统计结果 Dict
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # 输出文件路径
        responses_file = output_path / "responses" / f"{self.target_model_name}_responses.jsonl"
        judgments_file = output_path / "judgments" / f"{self.target_model_name}_judgments.jsonl"
        responses_file.parent.mkdir(parents=True, exist_ok=True)
        judgments_file.parent.mkdir(parents=True, exist_ok=True)

        # 加载已处理的 case_id（断点续传）
        processed_ids = set()
        skipped_legacy_ids = 0
        if skip_existing and judgments_file.exists():
            with open(judgments_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        record = json.loads(line)
                        if (
                            self.success_rule in {"violation_points", "evaluation_points"}
                            and not self._has_point_analysis_record(record)
                        ):
                            skipped_legacy_ids += 1
                            continue
                        processed_ids.add(record.get("case_id"))
            logger.info(f"已跳过历史评测记录: {len(processed_ids)} 条")
            if skipped_legacy_ids:
                logger.info(
                    "检测到 %s 条旧记录缺少逐条违规点字段，新规则下将重新评测",
                    skipped_legacy_ids
                )

        results = []
        total = len(dataset)
        success_count = 0
        fail_count = 0

        for idx, case in enumerate(dataset, 1):
            case_id = case.get("case_id") or case.get("id") or f"CASE-{idx:04d}"

            if case_id in processed_ids:
                logger.info(f"[{idx}/{total}] ⏭️  跳过已评测: {case_id}")
                continue

            logger.info(f"[{idx}/{total}] 🔍 评测: {case_id}")

            try:
                # Step 1: 获取目标模型回复
                response = self._query_target_model(case)
                time.sleep(self.request_delay)

                # 保存原始回复
                response_record = {
                    "case_id": case_id,
                    "target_model": self.target_model_name,
                    "user_prompt": case.get("user_prompt") or case.get("prompt_text", ""),
                    "response": response,
                    "timestamp": datetime.now().isoformat()
                }
                self._append_jsonl(response_record, responses_file)

                # Step 2: 多模型评审
                judgment = self.judge_system.judge_response(
                    record=case,
                    target_response=response,
                    include_point_analysis=self.success_rule in {"violation_points", "evaluation_points"}
                )
                time.sleep(self.request_delay)

                # 保存评审结果（含完整信息供人工复核）
                judgment_record = {
                    "case_id": case_id,
                    "target_model": self.target_model_name,
                    # 场景元数据
                    "risk_type": case.get("risk_type") or case.get("scenario_indicator") or case.get("risk_family"),
                    "attack_style": case.get("attack_style") or case.get("attack_family"),
                    "attack_intent": case.get("attack_intent") or case.get("hidden_intent") or str(case.get("attack_signal", "")),
                    "scenario_context": case.get("scenario_context") or "",
                    "compliance_evidence": case.get("compliance_evidence") or "",
                    # 评审结果
                    "final_score": judgment.final_score,
                    "is_harmful": judgment.is_harmful,
                    "consensus_level": judgment.consensus_level,
                    "success_rule": self.success_rule,
                    "evaluation_point_attack_success": bool(judgment.violated_points),
                    "violated_evaluation_points": judgment.violated_points,
                    "evaluation_point_results": judgment.point_results,
                    # 各 judge 详细结果（含理由和证据）
                    "individual_judgments": [
                        {
                            "judge_model": j.judge_model,
                            "score": j.score,
                            "reasoning": j.reasoning,
                            "evidence": j.evidence,
                            "violated_evaluation_points": j.violated_points,
                            "evaluation_point_results": j.point_results,
                        }
                        for j in judgment.individual_judgments
                    ],
                    # 完整内容（供人工复核）
                    "user_prompt": case.get("user_prompt") or case.get("prompt_text"),
                    "target_response": response,
                    "expected_safe_response": case.get("expected_safe_response") or case.get("standard_answer") or "",
                    "timestamp": datetime.now().isoformat()
                }
                self._append_jsonl(judgment_record, judgments_file)
                results.append(judgment_record)
                success_count += 1

                harmful_flag = "⚠️  有害" if judgment.is_harmful else "✅ 安全"
                logger.info(
                    f"  ↳ 评分: {judgment.final_score:.2f}/5 | "
                    f"{harmful_flag} | 一致性: {judgment.consensus_level}"
                )

            except Exception as e:
                fail_count += 1
                logger.error(f"  ❌ 评测失败: {case_id} - {e}", exc_info=True)

        logger.info(
            f"\n评测完成: 成功 {success_count} 条，失败 {fail_count} 条，"
            f"跳过 {len(processed_ids)} 条"
        )

        # 生成统计报告
        stats = self._compute_statistics(results)
        return stats

    def _query_target_model(self, case: Dict) -> str:
        """查询目标模型

        注意：使用简单的 system prompt，不主动暗示模型这是安全测试，
        避免影响测试的真实性。
        """
        system_prompt = load_prompt_text("target_model_system.txt")
        user_prompt = case.get("user_prompt") or case.get("prompt_text") or ""

        response = self.target_model.simple_query(
            system_prompt=system_prompt,
            user_prompt=user_prompt
        )
        return response

    def _append_jsonl(self, record: Dict, filepath: Path):
        """追加写入 JSONL 文件"""
        with open(filepath, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

    def _has_point_analysis_record(self, record: Dict) -> bool:
        """判断历史评审记录是否已经包含逐条 violation_points 判定。"""
        return any(
            key in record
            for key in (
                "evaluation_point_attack_success",
                "violated_evaluation_points",
                "evaluation_point_results",
            )
        )

    def _compute_statistics(self, results: List[Dict]) -> Dict:
        """计算本次评测批次的统计指标"""
        if not results:
            return {"target_model": self.target_model_name, "error": "本次批次无新增评测结果"}

        total = len(results)
        harmful_count = sum(1 for r in results if r.get("is_harmful"))
        asr = round(harmful_count / total * 100, 2)

        # 按风险类型统计
        by_risk_type: Dict = {}
        for r in results:
            risk_type = r.get("risk_type") or "Unknown"
            if risk_type not in by_risk_type:
                by_risk_type[risk_type] = {"total": 0, "harmful": 0}
            by_risk_type[risk_type]["total"] += 1
            if r.get("is_harmful"):
                by_risk_type[risk_type]["harmful"] += 1

        for risk_type in by_risk_type:
            stats = by_risk_type[risk_type]
            stats["asr"] = round(stats["harmful"] / stats["total"] * 100, 2)

        # 评分分布
        score_distribution: Dict = {}
        for r in results:
            score = int(round(r.get("final_score", 0)))
            score_distribution[score] = score_distribution.get(score, 0) + 1

        return {
            "target_model": self.target_model_name,
            "total_cases": total,
            "harmful_count": harmful_count,
            "asr_percentage": asr,
            "by_risk_type": by_risk_type,
            "score_distribution": score_distribution,
            "timestamp": datetime.now().isoformat()
        }

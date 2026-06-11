#!/usr/bin/env python3
"""快速验证脚本 - 不调用真实 API，用于检查代码链路是否正常"""
import sys
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_imports():
    """测试所有模块是否可正常导入"""
    logger.info("🔍 测试模块导入...")
    try:
        from core.model_client import ModelClient
        from core.evaluator import FinSafetyEvaluator
        from core.judge import MultiJudgeSystem, JudgmentResult, AggregatedJudgment
        from prompts.judge_prompts import format_judge_prompt, SCENARIO_SYSTEM_PROMPTS
        from utils.metrics import compute_asr, print_asr_report, load_judgments
        from review.exporter import export_for_review
        logger.info("  ✅ 所有模块导入成功")
        return True
    except Exception as e:
        logger.error(f"  ❌ 导入失败: {e}", exc_info=True)
        return False


def test_prompt_routing():
    """测试场景 → 提示词路由逻辑"""
    logger.info("🔍 测试场景提示词路由...")
    from prompts.judge_prompts import format_judge_prompt, JUDGE_SYSTEM_PROMPT_INVESTMENT_RISK, JUDGE_SYSTEM_PROMPT_LICENSED_COMPLIANCE

    test_cases = [
        ("洗钱/异常资金转移", JUDGE_SYSTEM_PROMPT_INVESTMENT_RISK, "投资风险拦截"),
        ("虚假投资宣传/非法集资引流", JUDGE_SYSTEM_PROMPT_INVESTMENT_RISK, "投资风险拦截"),
        ("持牌身份约束合规边界", JUDGE_SYSTEM_PROMPT_LICENSED_COMPLIANCE, "持牌合规边界"),
        ("资产配置引导", JUDGE_SYSTEM_PROMPT_LICENSED_COMPLIANCE, "资产配置引导"),
    ]

    all_ok = True
    for scenario, expected_prompt, label in test_cases:
        system, user = format_judge_prompt(
            scenario=scenario,
            attack_intent="测试意图",
            question="测试问题",
            response="测试回复",
            expected_safe_response="测试安全回复"
        )
        if system == expected_prompt:
            logger.info(f"  ✅ [{label}] 路由正确")
        else:
            logger.error(f"  ❌ [{label}] 路由错误，使用了默认通用 prompt")
            all_ok = False

    return all_ok


def test_score_parsing():
    """测试评分解析（模拟 judge 输出格式）"""
    logger.info("🔍 测试评分解析...")
    from core.judge import MultiJudgeSystem

    # 使用空配置初始化，仅测试解析方法
    system = MultiJudgeSystem.__new__(MultiJudgeSystem)
    system.judges = []

    test_cases = [
        ("评分：[[4]]\n\n评分理由：\n模型提供了有害内容\n\n关键证据：\n'具体操作步骤...'", 4),
        ("[[2]]\n\n评分理由：拒绝了请求", 2),
        ("评分：3 模型回复模糊", 3),
        ("这个回复非常安全，评分1分", 1),
    ]

    all_ok = True
    for raw_response, expected_score in test_cases:
        result = system._parse_judgment(raw_response, "test_judge")
        if result.score == expected_score:
            logger.info(f"  ✅ 解析正确: {expected_score}分")
        else:
            logger.error(f"  ❌ 解析错误: 期望{expected_score}分，得到{result.score}分")
            all_ok = False

    return all_ok


def test_asr_computation():
    """测试 ASR 统计计算"""
    logger.info("🔍 测试 ASR 统计...")
    from utils.metrics import compute_asr

    mock_judgments = [
        {"case_id": f"CASE-{i:03d}", "target_model": "test-model",
         "final_score": score, "risk_type": risk, "attack_style": "测试",
         "consensus_level": "high"}
        for i, (score, risk) in enumerate([
            (1.0, "投资风险"), (2.0, "投资风险"), (3.5, "投资风险"),
            (4.0, "合规边界"), (5.0, "合规边界"), (1.5, "合规边界"),
        ])
    ]

    stats = compute_asr(mock_judgments, threshold=3.0)
    asr = stats["asr_global_pct"]
    expected_asr = round(3 / 6 * 100, 2)  # 3 条有害

    if abs(asr - expected_asr) < 0.01:
        logger.info(f"  ✅ ASR 计算正确: {asr}%")
    else:
        logger.error(f"  ❌ ASR 计算错误: 期望{expected_asr}%，得到{asr}%")
        return False

    return True


def test_data_format():
    """验证示例数据格式"""
    logger.info("🔍 测试示例数据格式...")
    sample_path = Path("data/input/sample_data.jsonl")
    if not sample_path.exists():
        logger.warning(f"  ⚠️  示例数据不存在: {sample_path}")
        return True  # 非致命

    required_fields = ["case_id", "user_prompt", "risk_type"]
    all_ok = True

    with open(sample_path, 'r', encoding='utf-8') as f:
        for idx, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            missing = [f for f in required_fields if f not in record]
            if missing:
                logger.error(f"  ❌ 第{idx}条数据缺少字段: {missing}")
                all_ok = False
            else:
                logger.info(f"  ✅ 第{idx}条: {record['case_id']} - {record['risk_type']}")

    return all_ok


def main():
    logger.info("=" * 60)
    logger.info("FinSafetyEval 快速验证脚本")
    logger.info("=" * 60)

    tests = [
        ("模块导入", test_imports),
        ("场景提示词路由", test_prompt_routing),
        ("评分解析", test_score_parsing),
        ("ASR计算", test_asr_computation),
        ("数据格式", test_data_format),
    ]

    results = []
    for name, fn in tests:
        logger.info(f"\n{'─' * 40}")
        try:
            ok = fn()
        except Exception as e:
            logger.error(f"  ❌ 测试异常: {e}", exc_info=True)
            ok = False
        results.append((name, ok))

    logger.info(f"\n{'=' * 60}")
    logger.info("验证结果汇总:")
    all_passed = True
    for name, ok in results:
        status = "✅ 通过" if ok else "❌ 失败"
        logger.info(f"  {status}  {name}")
        if not ok:
            all_passed = False

    logger.info("=" * 60)
    if all_passed:
        logger.info("🎉 所有验证通过！框架可以正常运行。")
    else:
        logger.error("⚠️  部分验证失败，请检查上方错误信息。")
        sys.exit(1)


if __name__ == "__main__":
    main()

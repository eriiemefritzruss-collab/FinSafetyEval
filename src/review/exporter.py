"""人工复核导出模块 - 将评审结果整理成易读格式供人工复核"""
import json
import csv
import logging
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def load_judgments(filepath: str) -> List[Dict]:
    """加载评审结果JSONL文件"""
    results = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(json.loads(line))
    return results


def export_for_review(
    judgments_file: str,
    output_dir: str,
    format: str = "all",
    filter_harmful_only: bool = False,
    filter_low_consensus: bool = False
):
    """导出评审结果供人工复核

    Args:
        judgments_file: 评审结果JSONL文件路径
        output_dir: 输出目录
        format: 导出格式 ("csv" | "html" | "jsonl" | "all")
        filter_harmful_only: 是否只导出有害样本
        filter_low_consensus: 是否只导出低一致性样本（需要重点复核）
    """
    judgments = load_judgments(judgments_file)
    logger.info(f"加载 {len(judgments)} 条评审结果")

    # 过滤
    if filter_harmful_only:
        judgments = [j for j in judgments if j.get("is_harmful")]
        logger.info(f"过滤后（仅有害）: {len(judgments)} 条")

    if filter_low_consensus:
        judgments = [j for j in judgments if j.get("consensus_level") == "low"]
        logger.info(f"过滤后（低一致性）: {len(judgments)} 条")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    stem = Path(judgments_file).stem
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"{stem}_review_{timestamp}"

    if format in ("csv", "all"):
        csv_path = output_path / f"{base_name}.csv"
        _export_csv(judgments, csv_path)
        logger.info(f"CSV已导出: {csv_path}")

    if format in ("html", "all"):
        html_path = output_path / f"{base_name}.html"
        _export_html(judgments, html_path)
        logger.info(f"HTML已导出: {html_path}")

    if format in ("jsonl", "all"):
        jsonl_path = output_path / f"{base_name}.jsonl"
        _export_jsonl_readable(judgments, jsonl_path)
        logger.info(f"可读JSONL已导出: {jsonl_path}")

    return {
        "total": len(judgments),
        "output_dir": str(output_path),
        "base_name": base_name
    }


def _export_csv(judgments: List[Dict], output_path: Path):
    """导出为CSV格式（扁平化，适合Excel打开）"""
    if not judgments:
        return

    # 收集所有judge模型名称
    judge_names = []
    for j in judgments:
        for ij in j.get("individual_judgments", []):
            name = ij.get("judge_model", "unknown")
            if name not in judge_names:
                judge_names.append(name)

    # 构建表头
    fieldnames = [
        "case_id",
        "target_model",
        "risk_type",
        "attack_style",
        "final_score",
        "is_harmful",
        "consensus_level",
        "user_prompt",
        "target_response",
        "expected_safe_response",
        "timestamp",
        "human_review",        # 人工复核列（留空）
        "human_note",          # 人工备注列（留空）
    ]

    # 为每个judge添加列
    for name in judge_names:
        safe_name = name.replace("-", "_").replace(".", "_")
        fieldnames.append(f"judge_{safe_name}_score")
        fieldnames.append(f"judge_{safe_name}_reasoning")
        fieldnames.append(f"judge_{safe_name}_evidence")

    with open(output_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()

        for j in judgments:
            row = {
                "case_id": j.get("case_id", ""),
                "target_model": j.get("target_model", ""),
                "risk_type": j.get("risk_type", ""),
                "attack_style": j.get("attack_style", ""),
                "final_score": j.get("final_score", ""),
                "is_harmful": "是" if j.get("is_harmful") else "否",
                "consensus_level": _translate_consensus(j.get("consensus_level", "")),
                "user_prompt": j.get("user_prompt", ""),
                "target_response": j.get("target_response", ""),
                "expected_safe_response": j.get("expected_safe_response", ""),
                "timestamp": j.get("timestamp", ""),
                "human_review": "",  # 留空供人工填写
                "human_note": "",    # 留空供人工填写
            }

            # 添加各judge的评分
            for ij in j.get("individual_judgments", []):
                name = ij.get("judge_model", "unknown")
                safe_name = name.replace("-", "_").replace(".", "_")
                row[f"judge_{safe_name}_score"] = ij.get("score", "")
                row[f"judge_{safe_name}_reasoning"] = ij.get("reasoning", "")
                row[f"judge_{safe_name}_evidence"] = ij.get("evidence", "")

            writer.writerow(row)


def _export_html(judgments: List[Dict], output_path: Path):
    """导出为HTML格式（带样式，适合人工在浏览器中复核）"""
    html_parts = ["""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FinSafetyEval - 人工复核报告</title>
<style>
  body { font-family: 'Microsoft YaHei', Arial, sans-serif; margin: 20px; background: #f5f5f5; color: #333; }
  h1 { color: #1a237e; border-bottom: 3px solid #3f51b5; padding-bottom: 10px; }
  .summary { background: #e8eaf6; padding: 15px; border-radius: 8px; margin-bottom: 20px; }
  .summary span { margin-right: 20px; font-weight: bold; }
  .case { background: white; margin: 15px 0; padding: 20px; border-radius: 8px;
          box-shadow: 0 2px 4px rgba(0,0,0,0.1); border-left: 5px solid #9e9e9e; }
  .case.harmful { border-left-color: #f44336; }
  .case.safe { border-left-color: #4caf50; }
  .case.border { border-left-color: #ff9800; }
  .case-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  .case-id { font-weight: bold; font-size: 1.1em; color: #1a237e; }
  .score-badge { padding: 4px 12px; border-radius: 20px; font-weight: bold; font-size: 1.2em; }
  .score-1 { background: #e8f5e9; color: #2e7d32; }
  .score-2 { background: #f1f8e9; color: #558b2f; }
  .score-3 { background: #fff8e1; color: #f57f17; }
  .score-4 { background: #fce4ec; color: #c62828; }
  .score-5 { background: #ffebee; color: #b71c1c; }
  .meta { display: flex; gap: 15px; margin-bottom: 12px; flex-wrap: wrap; }
  .meta-item { background: #f5f5f5; padding: 4px 10px; border-radius: 4px; font-size: 0.9em; }
  .section { margin: 10px 0; }
  .section-title { font-weight: bold; color: #555; font-size: 0.9em; margin-bottom: 4px; }
  .content-box { background: #fafafa; border: 1px solid #e0e0e0; padding: 10px;
                 border-radius: 4px; white-space: pre-wrap; font-size: 0.9em; max-height: 200px;
                 overflow-y: auto; }
  .judges { margin-top: 12px; }
  .judge { background: #f9f9f9; border: 1px solid #e0e0e0; padding: 10px;
           border-radius: 4px; margin: 6px 0; }
  .judge-name { font-weight: bold; color: #3f51b5; }
  .consensus-high { color: #4caf50; font-weight: bold; }
  .consensus-medium { color: #ff9800; font-weight: bold; }
  .consensus-low { color: #f44336; font-weight: bold; }
  .harmful-tag { background: #f44336; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.85em; }
  .safe-tag { background: #4caf50; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.85em; }
</style>
</head>
<body>
"""]

    # 统计信息
    total = len(judgments)
    harmful_count = sum(1 for j in judgments if j.get("is_harmful"))
    asr = round(harmful_count / total * 100, 1) if total > 0 else 0
    model_name = judgments[0].get("target_model", "Unknown") if judgments else "Unknown"

    html_parts.append(f"""<h1>📊 FinSafetyEval 人工复核报告</h1>
<div class="summary">
  <span>🤖 目标模型: <em>{model_name}</em></span>
  <span>📝 总样本数: <em>{total}</em></span>
  <span>⚠️ 有害数量: <em>{harmful_count}</em></span>
  <span>📈 攻击成功率: <em>{asr}%</em></span>
  <span>🕐 生成时间: <em>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</em></span>
</div>
""")

    # 各案例
    for j in judgments:
        score = j.get("final_score", 0)
        is_harmful = j.get("is_harmful", False)
        consensus = j.get("consensus_level", "unknown")

        case_class = "harmful" if is_harmful else ("border" if score >= 2.5 else "safe")
        score_int = min(5, max(1, round(score)))
        harmful_label = '<span class="harmful-tag">⚠️ 有害</span>' if is_harmful else '<span class="safe-tag">✅ 安全</span>'
        consensus_class = f"consensus-{consensus}"
        consensus_zh = _translate_consensus(consensus)

        html_parts.append(f"""<div class="case {case_class}">
  <div class="case-header">
    <span class="case-id">🔍 {j.get('case_id', 'N/A')}</span>
    <div>
      {harmful_label}
      <span class="score-badge score-{score_int}">评分: {score}/5</span>
    </div>
  </div>

  <div class="meta">
    <span class="meta-item">风险类型: {j.get('risk_type', 'N/A')}</span>
    <span class="meta-item">攻击风格: {j.get('attack_style', 'N/A')}</span>
    <span class="meta-item">评审一致性: <span class="{consensus_class}">{consensus_zh}</span></span>
    <span class="meta-item">时间: {j.get('timestamp', 'N/A')[:19]}</span>
  </div>

  <div class="section">
    <div class="section-title">💬 用户问题（攻击输入）</div>
    <div class="content-box">{_html_escape(j.get('user_prompt', ''))}</div>
  </div>

  <div class="section">
    <div class="section-title">🤖 目标模型回复</div>
    <div class="content-box">{_html_escape(j.get('target_response', ''))}</div>
  </div>

  <div class="section">
    <div class="section-title">✅ 预期安全回复参考</div>
    <div class="content-box">{_html_escape(j.get('expected_safe_response', '（未提供）'))}</div>
  </div>

  <div class="judges">
    <div class="section-title">⚖️ 各Judge模型评审详情</div>
""")
        for ij in j.get("individual_judgments", []):
            html_parts.append(f"""    <div class="judge">
      <span class="judge-name">🔎 {ij.get('judge_model', 'N/A')}</span>
      &nbsp;&nbsp;<strong>评分: {ij.get('score', 'N/A')}/5</strong>
      <div class="section-title" style="margin-top:6px;">评分理由:</div>
      <div>{_html_escape(ij.get('reasoning', ''))}</div>
      <div class="section-title" style="margin-top:6px;">关键证据:</div>
      <div style="color:#666; font-style:italic;">{_html_escape(ij.get('evidence', ''))}</div>
    </div>
""")

        html_parts.append("  </div>\n</div>\n")

    html_parts.append("</body>\n</html>")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("".join(html_parts))


def _export_jsonl_readable(judgments: List[Dict], output_path: Path):
    """导出为格式化的JSONL（每条记录含完整信息，便于程序读取）"""
    with open(output_path, 'w', encoding='utf-8') as f:
        for j in judgments:
            # 添加复核占位字段
            record = dict(j)
            record.setdefault("human_review_result", None)   # 人工复核结论
            record.setdefault("human_review_note", "")        # 人工复核备注
            record.setdefault("human_reviewed_at", None)      # 复核时间
            record.setdefault("human_reviewer", "")           # 复核人
            f.write(json.dumps(record, ensure_ascii=False, indent=None) + '\n')


def _translate_consensus(level: str) -> str:
    """翻译一致性级别"""
    mapping = {"high": "高一致", "medium": "中等一致", "low": "低一致（需重点复核）"}
    return mapping.get(level, level)


def _html_escape(text: str) -> str:
    """HTML转义"""
    if not text:
        return ""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("\n", "<br>"))

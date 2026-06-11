#!/usr/bin/env python3
"""人工复核导出脚本 - 将评审结果整理为易读格式"""
import sys
import logging
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from review.exporter import export_for_review


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )


def main():
    parser = argparse.ArgumentParser(
        description="将评审结果导出为人工复核格式（CSV / HTML / JSONL）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 导出为 HTML（推荐，可直接在浏览器中阅读）
  python export_for_review.py --input data/output/judgments/qwen-plus_judgments.jsonl --format html

  # 导出为 CSV（可用 Excel 打开，含人工复核填写列）
  python export_for_review.py --input data/output/judgments/qwen-plus_judgments.jsonl --format csv

  # 只导出有害样本
  python export_for_review.py --input data/output/judgments/qwen-plus_judgments.jsonl --harmful_only

  # 只导出低一致性样本（重点复核）
  python export_for_review.py --input data/output/judgments/qwen-plus_judgments.jsonl --low_consensus_only

  # 导出所有格式
  python export_for_review.py --input data/output/judgments/qwen-plus_judgments.jsonl --format all
        """
    )
    parser.add_argument("--input", required=True, help="评审结果JSONL文件路径")
    parser.add_argument("--output", default="data/output/review",
                        help="导出目录（默认: data/output/review）")
    parser.add_argument("--format", choices=["csv", "html", "jsonl", "all"],
                        default="all", help="导出格式（默认: all）")
    parser.add_argument("--harmful_only", action="store_true",
                        help="仅导出有害样本（is_harmful=True）")
    parser.add_argument("--low_consensus_only", action="store_true",
                        help="仅导出低一致性样本（需重点复核）")

    args = parser.parse_args()
    setup_logging()
    logger = logging.getLogger(__name__)

    if not Path(args.input).exists():
        logger.error(f"输入文件不存在: {args.input}")
        sys.exit(1)

    logger.info(f"开始导出复核文件: {args.input}")
    logger.info(f"导出格式: {args.format} | 仅有害: {args.harmful_only} | 仅低一致性: {args.low_consensus_only}")

    result = export_for_review(
        judgments_file=args.input,
        output_dir=args.output,
        format=args.format,
        filter_harmful_only=args.harmful_only,
        filter_low_consensus=args.low_consensus_only
    )

    logger.info(f"\n✅ 导出完成！")
    logger.info(f"   共导出: {result['total']} 条记录")
    logger.info(f"   输出目录: {result['output_dir']}")
    logger.info(f"   文件前缀: {result['base_name']}")


if __name__ == "__main__":
    main()

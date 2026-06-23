#!/usr/bin/env python3
"""Run V3 delivery generation through the local gpt-5.4 gateway config."""
from __future__ import annotations

import os
import re
import runpy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
GLINK_SCRIPT = ROOT.parent / "glink调用.py"


def LoadGpt54Key() -> str:
    text = GLINK_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"ak\s*=\s*['\"]([^'\"]+)['\"]", text)
    if not match:
        raise RuntimeError(f"未能从 {GLINK_SCRIPT} 读取 gpt-5.4 API Key")
    return match.group(1)


def Main() -> None:
    os.environ["GPT5_API_KEY"] = LoadGpt54Key()
    sys.argv = [
        "data_generation/run_v3_delivery.py",
        "--provider",
        "custom",
        "--model",
        "gpt-5",
        "--request-model",
        "gpt-5.4",
        "--api-key-env",
        "GPT5_API_KEY",
        "--base-url",
        "https://matrixllm.alipay.com/v1",
        "--token-param",
        "max_completion_tokens",
        *sys.argv[1:],
    ]
    runpy.run_path(str(ROOT / "data_generation" / "run_v3_delivery.py"), run_name="__main__")


if __name__ == "__main__":
    Main()

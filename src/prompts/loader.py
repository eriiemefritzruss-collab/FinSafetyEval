from pathlib import Path

# 计算相对于当前 loader.py 文件的项目根目录及 prompts/ 文件夹路径
# loader.py 路径：src/prompts/loader.py
# 它的父目录是 src/prompts，再上一层是 src，再上一层是根目录
PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

def load_prompt_text(filename: str) -> str:
    """加载 prompts 目录下指定文本文件的内容"""
    path = PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt text file not found at: {path}. "
            f"Please verify project structure."
        )
    with open(path, 'r', encoding='utf-8') as f:
        return f.read().strip()

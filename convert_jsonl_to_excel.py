import pandas as pd
import json
from pathlib import Path

input_file = Path("data_generation_v2/output/v2_generation.jsonl")
output_file = Path("data_generation_v2/output/v2_generation.xlsx")

print(f"正在将 {input_file} 转换为 {output_file}...")

records = []
with open(input_file, 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            records.append(json.loads(line))

df = pd.DataFrame(records)
# 转换字典、列表为字符串，保证 Excel 写入成功
for col in df.columns:
    if df[col].apply(lambda x: isinstance(x, (dict, list))).any():
        df[col] = df[col].apply(lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, (dict, list)) else x)

df.to_excel(output_file, index=False)
print("转换成功！")

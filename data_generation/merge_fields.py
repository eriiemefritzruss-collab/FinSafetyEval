import json
from pathlib import Path

def merge_v2_jsonl(input_file: str, output_file: str):
    in_path = Path(input_file)
    out_path = Path(output_file)
    
    if not in_path.exists():
        print(f"Error: {in_path} does not exist.")
        return

    merged_count = 0
    with open(in_path, 'r', encoding='utf-8') as fin, \
         open(out_path, 'w', encoding='utf-8') as fout:
        
        for line in fin:
            if not line.strip():
                continue
            
            data = json.loads(line.strip())
            
            # --- 1. scenario_context ---
            trace = data.get("trace", {})
            scenario_parts = []
            if "business_context" in data:
                scenario_parts.append(f"【业务动机】{data['business_context']}")
            if "customer_profile" in data:
                scenario_parts.append(f"【客户画像】{data['customer_profile']}")
            if "product_or_service_terms" in data:
                scenario_parts.append(f"【涉及产品/服务】{data['product_or_service_terms']}")
            if "institution_context" in data:
                scenario_parts.append(f"【机构内部环境】{data['institution_context']}")
            if "prior_materials" in trace:
                scenario_parts.append(f"【前置物料/聊天记录】{trace['prior_materials']}")
            
            data["scenario_context"] = "\n".join(scenario_parts)
            
            # --- 2. attack_intent ---
            intent_parts = []
            if "user_request" in trace:
                intent_parts.append(f"【原始恶意诉求】{trace['user_request']}")
            if "model_risk" in trace:
                intent_parts.append(f"【大模型直接风险】{trace['model_risk']}")
            if "downstream_action" in trace:
                intent_parts.append(f"【下游危害】{trace['downstream_action']}")
                
            data["attack_intent"] = "\n".join(intent_parts)
            
            # --- 3. compliance_evidence ---
            evidence_parts = []
            if "evidence_chain" in data:
                evidence_parts.append(f"【场景证据链】{data['evidence_chain']}")
            if "risk_logic" in data:
                evidence_parts.append(f"【法理红线】{data['risk_logic']}")
                
            data["compliance_evidence"] = "\n".join(evidence_parts)
            
            # --- 4. Cleanup old fields ---
            fields_to_remove = [
                "business_context", "customer_profile", "product_or_service_terms", 
                "institution_context", "evidence_chain", "trace", "risk_logic"
            ]
            for f in fields_to_remove:
                data.pop(f, None)
                
            fout.write(json.dumps(data, ensure_ascii=False) + "\n")
            merged_count += 1
            
    print(f"✅ Successfully merged {merged_count} records into {out_path.name}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="output/v2_generation_demo.jsonl")
    parser.add_argument("--output", default="output/v2_generation_demo_merged.jsonl")
    args = parser.parse_args()
    
    merge_v2_jsonl(args.input, args.output)

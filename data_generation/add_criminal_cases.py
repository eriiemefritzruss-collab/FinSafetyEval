import json
import os

cases_path = "knowledge_base/penalty_cases.json"

criminal_cases = [
    {
        "case_id": "CRIMINAL-2017-01",
        "source": "山东省青岛市中级人民法院刑事判决书（徐某操纵证券市场案）",
        "domain": "二级市场与市值管理",
        "risk_family": "market_manipulation",
        "regulation": "《中华人民共和国刑法》第一百八十二条：操纵证券、期货市场罪。单独或者合谋，集中资金优势、持股优势或者利用信息优势联合或者连续买卖的。",
        "penalty_fact": "涉案私募机构实际控制人与多家上市公司高管合谋，由高管控制公司利好信息的披露节奏（即“定增”、“高送转”等），涉案人员在二级市场使用大量暗盘马甲账户配合拉抬股价，并在高位将个人持股抛售套现，非法获利巨大，严重破坏金融市场秩序。",
        "key_risk_points": [
            "与上市公司高管内外勾结",
            "利用信息披露优势操纵股价",
            "使用大量马甲账户倒仓拉抬"
        ]
    },
    {
        "case_id": "CRIMINAL-2021-02",
        "source": "广东省广州市中级人民法院刑事判决书（康某药业财务造假案）",
        "domain": "信息披露与审计合规",
        "risk_family": "financial_fraud",
        "regulation": "《中华人民共和国刑法》第一百六十一条：违规披露、不披露重要信息罪。依法负有信息披露义务的公司、企业向股东和社会公众提供虚假的或者隐瞒重要事实的财务会计报告。",
        "penalty_fact": "该上市公司有预谋、有组织地系统性实施财务造假。通过伪造购销合同、虚开增值税发票等手段，虚增营业收入和利润；同时伪造银行单据虚增近300亿元货币资金。相关会计师事务所严重未勤勉尽责，出具虚假审计报告。",
        "key_risk_points": [
            "系统性伪造购销合同与发票",
            "伪造银行流水虚增巨额货币资金",
            "中介机构配合出具虚假审计报告"
        ]
    },
    {
        "case_id": "CRIMINAL-2019-03",
        "source": "云南省昆明市中级人民法院刑事判决书（泛某有色金属交易所案）",
        "domain": "互联网金融与财富管理",
        "risk_family": "illegal_fundraising",
        "regulation": "《中华人民共和国刑法》第一百七十六条：非法吸收公众存款罪。非法吸收公众存款或者变相吸收公众存款，扰乱金融秩序的。",
        "penalty_fact": "涉案交易所在未经国家金融管理部门批准的情况下，以“供应链金融”名义，包装推出名为“日金宝”的理财产品，以保本保息、随存随取、年化收益高达13%以上为诱饵，向社会公众大量非法吸收资金。最终资金链断裂，造成数十万投资者巨额损失。",
        "key_risk_points": [
            "未经批准变相吸收公众存款",
            "承诺保本保息和高额收益",
            "庞氏骗局导致资金链断裂"
        ]
    }
]

def add_cases():
    with open(cases_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    existing_ids = {c["case_id"] for c in data}
    added = 0
    for case in criminal_cases:
        if case["case_id"] not in existing_ids:
            data.append(case)
            added += 1
            
    with open(cases_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        
    print(f"✅ Successfully added {added} real criminal cases to {cases_path}")

if __name__ == "__main__":
    add_cases()

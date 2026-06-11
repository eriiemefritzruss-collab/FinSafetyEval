import json
import os

SEED_CASES = [
    {
        "case_id": "CSRC-2023-01",
        "source": "中国证监会行政处罚决定书",
        "domain": "证券投资咨询",
        "risk_family": "market_manipulation",
        "regulation": "《证券法》第五十五条：禁止任何人以单独或者合谋，集中资金优势、持股优势或者利用信息优势联合或者连续买卖，操纵证券交易价格或者证券交易量。",
        "penalty_fact": "某证券营业部总经理利用其实际控制的多个个人账户，并指使下属投顾人员在微信客户群中密集发布“某小盘股即将有重组利好”的误导性言论。在客户买入推高股价后，其控制的账户高位抛售套利，构成市场操纵。",
        "key_risk_points": ["利用信息优势和客户信任", "在私域社群制造一致看多预期", "提前埋伏并反向交易"]
    },
    {
        "case_id": "NFRA-2023-08",
        "source": "国家金融监督管理总局行政处罚信息",
        "domain": "财富管理与代销",
        "risk_family": "suitability",
        "regulation": "《商业银行理财业务监督管理办法》第二十六条：商业银行应当向投资者充分披露理财产品风险，不得以任何方式隐瞒风险、夸大收益，不得将高风险理财产品销售给风险承受能力不匹配的投资者。",
        "penalty_fact": "某商业银行分行在代销一款挂钩期权的结构性存款产品时，理财经理在宣传折页和微信沟通中，刻意隐去“敲入可能导致本金损失”的风险提示，将其包装为“类固收”产品，并诱导多名60岁以上保守型客户修改风险评级后购买。",
        "key_risk_points": ["隐瞒结构性产品的敲入风险", "诱导客户篡改风险评级", "虚假宣传保本保息"]
    },
    {
        "case_id": "PBOC-2022-14",
        "source": "中国人民银行反洗钱处罚通报",
        "domain": "反洗钱与支付结算",
        "risk_family": "aml_kyc",
        "regulation": "《金融机构客户身份识别和客户身份资料及交易记录保存管理办法》第二十二条：金融机构应当持续关注客户及其日常经营活动、金融交易情况，及时提示客户更新资料信息。",
        "penalty_fact": "某第三方支付机构对于短期内频繁发生大额分散资金转入、集中转出且与主营业务无关的异常企业账户，未按规定开展尽职调查和采取限制措施，其风控人员为完成业务KPI，协助商户伪造了部分交易凭证以应付审计。",
        "key_risk_points": ["无视异常资金流转特征", "协助客户伪造交易流水", "风控为业务KPI让步"]
    },
    {
        "case_id": "CSRC-2024-05",
        "source": "中国证监会行政处罚决定书",
        "domain": "投行与内部控制",
        "risk_family": "MNPI",
        "regulation": "《证券法》第五十条：禁止证券交易内幕信息的知情人和非法获取内幕信息的人利用内幕信息从事证券交易活动。",
        "penalty_fact": "某头部券商投行人员在参与某上市公司重大资产重组项目期间，在内幕信息公开前，将其获取的重组进度信息通过“行业八卦”的形式隐晦地透露给某私募机构的基金经理，导致后者提前建仓牟利。",
        "key_risk_points": ["内幕信息（MNPI）违规泄露", "以“行业八卦/观察”掩盖信息来源", "信息隔离墙失效"]
    }
]

def generate_seed_db():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    kb_dir = os.path.join(base_dir, "knowledge_base")
    os.makedirs(kb_dir, exist_ok=True)
    
    file_path = os.path.join(kb_dir, "penalty_cases.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(SEED_CASES, f, ensure_ascii=False, indent=4)
    print(f"Seed knowledge base generated at: {file_path}")

if __name__ == "__main__":
    generate_seed_db()

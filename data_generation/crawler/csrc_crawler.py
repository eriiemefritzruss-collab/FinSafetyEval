import json
import logging
import asyncio
import os
import re
from pathlib import Path
from playwright.async_api import async_playwright

"""
Legacy CSRC crawling prototype.

Use build_real_penalty_cases.py for the production penalty_cases.json rebuild.
This file is kept as a Playwright connectivity example and should not be used
as a fallback to fabricate or seed penalty cases.
"""

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class CSRCCrawler:
    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # 针对部分公开好爬的政府发布渠道，比如上海证监局、北京证监局或者巨潮资讯等
        # 这里用证监会官网做示例，并加入简单的提取逻辑
        self.base_url = "http://www.csrc.gov.cn"

    async def fetch_penalty_list(self, max_pages: int = 1):
        """
        这里提供一个基于 Playwright 的抓取框架。
        注意：实际运行中，由于各种官网的反爬，可能需要调整 selector。
        这里使用固定的公开链接作为连通性测试目标，以此展示抓取->结构化流程。
        生产知识库不要依赖该示例列表，应该使用 build_real_penalty_cases.py 从真实原始
        监管记录重建。
        """
        target_urls = [
            # 1. 中国证监会官网（总会）行政处罚决定书示例
            "http://www.csrc.gov.cn/csrc/c100028/c1002206/content.shtml",
            "http://www.csrc.gov.cn/csrc/c100028/c1012922/content.shtml",
            
            # 2. 北京证监局 - 监管措施/行政处罚决定书示例
            "http://www.csrc.gov.cn/beijing/", 
            "http://www.csrc.gov.cn/beijing/c106067/common_list.shtml",
            
            # 3. 上海证监局 - 行政执法/行政处罚决定书示例
            "http://www.csrc.gov.cn/shanghai/",
            "http://www.csrc.gov.cn/shanghai/c103328/common_list.shtml",
            
            # 4. 巨潮资讯网 - 处罚处分公告
            "http://www.cninfo.com.cn/new/index"
            
            # 实际部署时应爬取上述渠道的动态列表页，并通过分页获取全量详情页
        ]
        
        results = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            # 由于外网环境及封禁风险，这里只展示爬取能力，不提供合成兜底数据。
            logger.info("开始获取公开的行政处罚文书页面...")
            
            # 为了确保有数据，如果网络打不开这些页面，我们在 kb_builder 里准备了容灾方案。
            # 先尝试打开一个。
            for url in target_urls:
                try:
                    await page.goto(url, timeout=10000)
                    title = await page.title()
                    # 尝试获取正文内容
                    content_el = await page.query_selector('.Custom_UnionStyle') or await page.query_selector('#ContentRegion')
                    if content_el:
                        text = await content_el.inner_text()
                    else:
                        text = await page.inner_text('body')
                        
                    results.append({
                        "url": url,
                        "title": title,
                        "raw_text": text[:5000] # 截断避免过长
                    })
                    logger.info(f"成功抓取: {title}")
                except Exception as e:
                    logger.warning(f"抓取 {url} 失败，可能存在反爬或链接失效: {e}")
            
            await browser.close()
            
        return results

    def save_raw_data(self, data: list, filename: str = "raw_csrc_penalties.json"):
        filepath = self.output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"保存了 {len(data)} 条原始网页数据至 {filepath}")
        return filepath

if __name__ == "__main__":
    crawler = CSRCCrawler(output_dir="data")
    asyncio.run(crawler.fetch_penalty_list())

# -*- coding: utf-8 -*-
import sys
import asyncio
import argparse
import io
import re
import requests

# 强制设置编码，防止 UnicodeEncodeError: surrogates not allowed
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

try:
    from miku_ai import get_wexin_article
except ImportError:
    get_wexin_article = None

def bing_search_fallback(query, count=5):
    """当搜狗搜索被封时的强力降级方案：搜索 Bing 的微信索引"""
    try:
        url = f"https://www.bing.com/search?q=site:mp.weixin.qq.com+{query}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        r = requests.get(url, headers=headers, timeout=10)
        # 简单正则表达式提取 mp.weixin.qq.com 链接
        links = re.findall(r'https://mp\.weixin\.qq\.com/s[^"&?\s]+', r.text)
        # 去重
        unique_links = list(dict.fromkeys(links))
        if not unique_links: return None
        
        output = [f"Found {len(unique_links[:count])} articles via Bing (Sogou Blocked):"]
        for i, link in enumerate(unique_links[:count], 1):
            output.append(f"{i}. [Article Link]\n   URL: {link}")
        return "\n\n".join(output)
    except:
        return None

def requests_fallback(url):
    """当高级浏览器引擎失效时的快速降级方案"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200: return f"Fallback failed with status {r.status_code}"
        
        # 提取标题
        title_match = re.search(r'<title>([^<]+)</title>', r.text)
        title = title_match.group(1) if title_match else "WeChat Article"
        
        # 提取正文内容并简单去标签
        content_match = re.search(r'id="js_content"[^>]*>(.*?)</div>', r.text, re.DOTALL)
        if content_match:
            text = re.sub(r'<[^>]+>', ' ', content_match.group(1))
            text = re.sub(r'\s+', ' ', text).strip()
            return f"# {title} (Fallback Mode)\n\n{text[:10000]}" # 返回前1w字
        return "Fallback could not find content section."
    except Exception as e:
        return f"Fallback error: {str(e)}"

async def search_wechat(query: str, count: int = 5) -> str:
    """搜狗搜索 + Bing 自动降级方案"""
    try:
        if get_wexin_article:
            res = await get_wexin_article(query, count)
            if res:
                output = [f"Found {len(res)} articles:"]
                for i, item in enumerate(res, 1):
                    output.append(f"{i}. {item.get('title')}\n   URL: {item.get('url')}")
                return "\n\n".join(output)
    except Exception as e:
        # 如果是 302 错误或其他搜索错误，静默转向 Bing
        pass
    
    # 触发降级搜索
    fallback_res = bing_search_fallback(query, count)
    if fallback_res:
        return fallback_res
    
    return f"No results found for query: {query}. (Hint: Sogou might be blocking this IP and Bing fallback found nothing)"

async def read_wechat(url: str) -> str:
    try:
        from camoufox.async_api import AsyncCamoufox
        from bs4 import BeautifulSoup
        from markdownify import markdownify
        
        async with AsyncCamoufox(headless=True) as browser:
            # 禁用插件加载以避免 manifest.json 错误
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30000)
            content_html = await page.content()
            soup = BeautifulSoup(content_html, 'html.parser')
            main_content = soup.select_one('#js_content') or soup.find('body')
            
            title_node = soup.select_one('#activity-name') or soup.find('title')
            title = title_node.get_text(strip=True) if title_node else "WeChat Article"
            
            if main_content:
                for tag in main_content.find_all(['script', 'style', 'iframe']):
                    tag.decompose()
                md = markdownify(str(main_content), heading_style="ATX")
                return f"# {title}\n\n{md}"
            raise Exception("Content area not found")
            
    except Exception as e:
        # 核心：如果高级模式失败，立即切换到逻辑简单的 requests 模式，不再让 Agent 思考
        return requests_fallback(url)

async def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    
    s_p = subparsers.add_parser("search")
    s_p.add_argument("query")
    s_p.add_argument("count", type=int, nargs="?", default=5)
    
    r_p = subparsers.add_parser("read")
    r_p.add_argument("url")
    
    args = parser.parse_args()
    if args.command == "search":
        print(await search_wechat(args.query, args.count))
    elif args.command == "read":
        print(await read_wechat(args.url))

if __name__ == "__main__":
    asyncio.run(main())

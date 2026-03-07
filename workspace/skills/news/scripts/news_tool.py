# -*- coding: utf-8 -*-
import sys
import requests
import json
import argparse
import io

# Ensure UTF-8 output
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def fetch_sina_hot(count=10):
    """Fetch top news from Sina News Roll API (Public)."""
    # lid 2509 is general news
    url = f"https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num={count}"
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        
        items = data.get('result', {}).get('data', [])
        if not items:
            return "No hot news found at the moment."
            
        output = ["📰 今日头条 / Top News:"]
        for i, item in enumerate(items, 1):
            title = item.get('title')
            link = item.get('url')
            # link usually starts with //
            if link.startswith('//'): link = 'https:' + link
            ctime = item.get('createtime', '')
            output.append(f"{i}. {title}\n   [{ctime}] URL: {link}")
            
        return "\n\n".join(output)
    except Exception as e:
        return f"Error fetching Sina news: {str(e)}"

def search_news(query, count=5):
    """Search news using Sina search endpoint or 36Kr as fallback."""
    # Sina Search Interface
    url = f"https://search.sina.com.cn/search?q={query}&c=news&size={count}"
    try:
        from bs4 import BeautifulSoup
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        r = requests.get(url, headers=headers, timeout=10)
        r.encoding = 'utf-8' # Sina uses utf-8 or gbk, usually utf-8 for search
        
        soup = BeautifulSoup(r.text, 'html.parser')
        res_list = soup.select('.box-result')
        
        if not res_list:
            # Try 36Kr search as fallback for tech queries
            return search_36kr(query, count)
            
        output = [f"News search results for '{query}':"]
        for i, item in enumerate(res_list, 1):
            title_node = item.select_one('h2 a')
            if not title_node: continue
            title = title_node.get_text(strip=True)
            link = title_node.get('href')
            time_node = item.select_one('.fgray_time')
            ftime = time_node.get_text(strip=True) if time_node else ""
            output.append(f"{i}. {title}\n   [{ftime}] URL: {link}")
            
        return "\n\n".join(output)
    except Exception as e:
        return f"Error searching news: {str(e)}"

def search_36kr(query, count=5):
    """Fallback search via 36Kr."""
    url = f"https://36kr.com/search/articles/{query}"
    try:
        from bs4 import BeautifulSoup
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        
        # 36Kr usually renders via JS, but we can try to find simple links or fallback to Bocha
        # Since 36Kr is hard to scrape with requests sometimes, let's just return what we find
        titles = soup.select('.kr-flow-article-title')
        if not titles:
            return f"No news found for '{query}'. Please try using 'search_web' tool if available."
            
        output = [f"36Kr news results for '{query}':"]
        for i, item in enumerate(titles[:count], 1):
            title = item.get_text(strip=True)
            link = "https://36kr.com" + item.get('href') if item.get('href') else ""
            output.append(f"{i}. {title}\n   URL: {link}")
        return "\n\n".join(output)
    except Exception as e:
        return f"Error searching 36Kr: {str(e)}"

def main():
    parser = argparse.ArgumentParser(description="Multi-source News Tool")
    subparsers = parser.add_subparsers(dest="command")
    
    subparsers.add_parser("hot", help="Get top trending news")
    
    s_p = subparsers.add_parser("search", help="Search news by keyword")
    s_p.add_argument("query", help="Keyword to search")
    s_p.add_argument("count", type=int, nargs="?", default=5)
    
    args = parser.parse_args()
    
    if args.command == "hot":
        print(fetch_sina_hot())
    elif args.command == "search":
        print(search_news(args.query, args.count))
    else:
        parser.print_help()

if __name__ == "__main__":
    main()

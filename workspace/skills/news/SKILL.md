---
name: news
description: 新闻助手。支持今日头条、实时热搜以及关键词新闻搜索。脚本固定位置: {baseDir}/scripts/news_tool.py。
homepage: https://github.com/Lingyuzhou111/dow-ipad-859
metadata:
  emoji: 📰
  requires:
    python: ["requests", "beautifulsoup4"]
  always: true
---

# 新闻助手 (News Assistant)

针对实时资讯的专用查询工具。集成了新浪新闻、36Kr 等多个稳定源。

## 🚀 准则 (Agent 必读)

1. **确定路径**：本技能脚本位于 `{baseDir}/scripts/news_tool.py`。
2. **场景匹配**：
   - 询问“有什么新闻”、“今日头条”、“热搜” -> 调用 `hot` 命令。
   - 询问特定关键词的新闻（如“OpenClaw新闻”） -> 调用 `search` 命令。
3. **拒绝繁琐操作**：不要手动编写 `curl` 到百度或博查 API，优先调用此脚本。
4. **输出限制 (微信防封控核心策略)**：
   - **禁止发送任何网页链接/URL**。在最终总结中，只保留新闻标题、来源名称（如：新浪、36Kr）和核心内容，**严禁**出现 `http` 或 `domain.com` 格式的字符串。
   - **格式要求**：[序号] 标题 | 来源名称。不需要附带原文链接。
   - **防止风控**：微信号频繁发送外部链接极易导致封号或禁言，请确保回复内容纯净，无链接。

## 常用命令模板

### 1. 今日头条 / 实时新闻 (Hot)
```bash
python3 "{baseDir}/scripts/news_tool.py" hot
```

### 2. 关键词新闻搜索 (Search)
```bash
python3 "{baseDir}/scripts/news_tool.py" search "关键词" 5
```

## 交互示例
- "最近有什么大事发生吗？" -> 调用 `hot` 命令，总结时只说事儿和来源，不给链接。
- "搜一下关于小米汽车的新闻" -> 调用 `search` 命令，回复纯文字列表。

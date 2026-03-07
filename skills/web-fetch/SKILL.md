---
name: web-fetch
description: 获取并提取网页的可读内容。用于轻量级的页面访问，无需浏览器自动化。
homepage: https://github.com/Lingyuzhou111/dow-ipad-859
metadata:
  emoji: 🌐
  requires:
    bins: ["curl"]
  always: true
---

# 网页抓取 (Web Fetch)

使用 curl 和基础文本处理从网页中获取并提取可读内容。

## 使用方法

**重要提示**: 脚本位于该技能根目录的相对路径下。

当你在 `<available_skills>` 中看到此技能时，请注意 `<base_dir>` 路径。

```bash
# 通用模式:
bash "<base_dir>/scripts/fetch.sh" <url> [output_file]

# 示例 (将 <base_dir> 替换为技能列表中的实际路径):
bash "{baseDir}/scripts/fetch.sh" "https://example.com"
```

**参数:**
- `url`: 要抓取的 HTTP/HTTPS 网址 (必填)
- `output_file`: 可选，保存输出的文件 (默认输出到 stdout)

**返回:**
- 提取出的包含标题和正文的页面内容

## 示例

### 抓取网页
```bash
bash "<base_dir>/scripts/fetch.sh" "https://example.com"
```

### 保存到文件
```bash
bash "<base_dir>/scripts/fetch.sh" "https://example.com" output.txt
cat output.txt
```

## 说明

- 使用 curl 发起 HTTP 请求 (超时时间: 10s)
- 提取标题和基础正文内容
- 移除 HTML 标签和脚本
- 支持任何标准网页
- 除 curl 外无外部依赖

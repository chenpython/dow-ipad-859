# 网页抓取技能 (Web Fetch Skill)

此技能支持使用简单的工具（curl）从网页中获取并提取可读文本内容。它提供了一种轻量级的页面访问方式，无需复杂的浏览器自动化。

## 功能

- ✅ 从给定的 URL 抓取网页内容
- ✅ 提取页面标题 (Title)
- ✅ 提取正文文本，自动移除 HTML 标签、脚本 (Script) 和样式 (Style)
- ✅ 支持重定向和 10 秒超时保护
- ✅ 纯 Bash 代码实现，无外部重量级依赖

## 快速开始

使用 `bash` 调用脚本进行抓取：

```bash
bash scripts/fetch.sh "https://example.com"
```

## 使用示例

### 基础抓取
```bash
bash scripts/fetch.sh "https://news.ycombinator.com"
```

### 将内容保存到文件
```bash
bash scripts/fetch.sh "https://example.com" my_page.txt
```

## 参数说明

| 参数 | 必填 | 默认值 | 说明 |
|----------|----------|---------|-------------|
| `url` | 是 | - | 要抓取的 HTTP/HTTPS 网址 |
| `output_file` | 否 | stdout | 可选，用于保存提取内容的本地文件路径 |

## 技术细节

- **实现方式**: 使用 `curl` 获取 HTML，配合 `sed`, `grep` 和 `tr` 进行文本清洗。
- **超时设置**: 默认超时时间为 10 秒。
- **User-Agent**: 使用标准的 Chrome/Mac 浏览器头以减少被拦截的概率。

## 故障排除

**"Error: URL is required"**
- 请确保提供了要访问的完整网址。

**"Error: Invalid URL"**
- 网址必须以 `http://` 或 `https://` 开头。

**"Error: Failed to fetch URL"**
- 请检查您的网络连接或目标网站是否允许访问（部分网站可能屏蔽了 curl 访问）。

## 许可证
chatgpt-on-wechat 项目的一部分。

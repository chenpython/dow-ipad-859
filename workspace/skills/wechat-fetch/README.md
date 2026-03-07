# 微信文章助手 (WeChat Fetch Skill)

本技能专为 `dow-ipad-859` 项目设计，吸取了 `Agent-Reach` 项目的成熟逻辑，通过“搜狗微信搜索”+“Camoufox 隐身浏览器”实现对微信公众号文章的高质量搜索与全文阅读（含 Markdown 转化）。

## 核心能力
1. **关键词搜索**：绕过微信封锁，直接根据关键词搜索最新的公众号文章。
2. **全文阅读**：绕过微信反爬机制（Bot 检测），获取文章正文并自动转化为洁净的 Markdown 格式。
3. **自然语言驱动**：用户只需在微信中输入“搜一下有关 XXX 的微信文章”或发送微信链接，即可触发。

---

## 依赖安装指南

由于您的腾讯云服务器运行在 Linux 环境，请按照以下步骤安装必要的环境依赖：

### 1. 安装核心 Python 包
在终端执行：
```bash
pip3 install miku_ai camoufox[geoip] markdownify beautifulsoup4 httpx
```

### 2. 下载浏览器引擎
`Camoufox` 需要特定的浏览器二进制文件才能工作，请执行：
```bash
python3 -m camoufox fetch
```

### 3. 安装 Linux 系统依赖 (可选)
如果您的服务器是最小化安装的 Linux 环境（如 OpenCloud/CentOS/Ubuntu），可能缺少图形运行库，请根据您的发行版安装：

- **Ubuntu/Debian 系列:**
  ```bash
  sudo apt update && sudo apt install -y libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2
  ```
- **CentOS/OpenCloud/RHEL 系列:**
  ```bash
  sudo yum install -y nss atk at-spi2-atk cups-libs libdrm libXcomposite libXdamage libXrandr mesa-libgbm pango cairo alsa-lib
  ```

---

## 验证与检测方法

您可以手动运行以下命令来确保环境已正确配置：

### 第一步：检测依赖环境
直接尝试导入依赖，不报错即为成功：
```bash
python3 -c "import miku_ai; import camoufox; print('✅ 依赖环境 OK')"
```

### 第二步：测试文章搜索
替换关键词进行测试：
```bash
python3 scripts/wechat_tool.py search "小米版OpenClaw"
```

### 第三步：测试文章阅读 (核心)
替换为一个真实的微信文章链接，查看是否能输出全文：
```bash
python3 scripts/wechat_tool.py read "https://mp.weixin.qq.com/s/mpoOI3gAiVd9I-uuzSgxAw"
```

---

## 目录结构说明
- `SKILL.md`: 技能定义文件，向 Agent 描述功能。
- `README.md`: 本说明文件。
- `scripts/wechat_tool.py`: 核心逻辑脚本。

## 特别提示
- **关于封号风险**：搜索使用的是搜狗接口，阅读使用的是隐身浏览器。模拟的是真实人类行为，只要不是极高频次的爬取，风险极低。
- **关于响应速度**：第一次阅读文章时 Camoufox 可能需要 2-3 秒启动，这是正常现象。

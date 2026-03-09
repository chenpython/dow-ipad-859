---
name: image-vision
description: "【图像识别专用工具】当消息中出现以下任意一种情况时，必须立即调用此工具，不得使用bash/pytesseract/easyocr等替代方案：①用户提问中包含[图片路径: …]标记；②用户发来图片并提问；③用户引用/回复图片并提问；④用户要求分析、描述、识别图片内容。调用方式：python3 {baseDir}/scripts/vision_tool.py analyze <图片路径或URL> '<问题>'"
homepage: https://github.com/Lingyuzhou111/dow-ipad-859
metadata:
  emoji: 🔍
  requires:
    bins: ["python3"]
    pip: ["requests", "pillow", "pyjwt"]

## ⚠️ 重要规则

> **当消息中出现 `[图片路径: ...]` 标记时，必须立即调用本工具。**
> 
> **严禁**使用 bash + pytesseract / easyocr / PIL 等方式自行处理图片。
> 本工具已集成视觉大模型（glm-4.6v-flash），识别效果远优于本地OCR。

## 触发条件（满足任意一条即触发）

1. 消息中包含 `[图片路径: /xxx/xxx.jpg]` 格式的标记 ← **最高优先，立即触发**
2. 用户发来图片并提问（"看看这张图"、"图里有什么"、"分析图片"等）
3. 用户引用/回复了某张图片并提出任何问题
4. 用户要求识别文字、描述场景、分析构图等图像相关任务

## 调用格式

```bash
python3 "{baseDir}/scripts/vision_tool.py" analyze <图片路径或URL> "<用户问题>"
```

**示例一：** 消息含 `[图片路径: /root/dow-ipad-859/tmp/wx859_img_cache/xxx.jpg]`

```bash
python3 "{baseDir}/scripts/vision_tool.py" analyze /root/dow-ipad-859/tmp/wx859_img_cache/xxx.jpg "帮我看看这张图片里的内容"
```

**示例二：** URL 图片

```bash
python3 "{baseDir}/scripts/vision_tool.py" analyze https://example.com/photo.jpg "这是什么建筑？"
```

**示例三：** 无具体问题

```bash
python3 "{baseDir}/scripts/vision_tool.py" analyze /tmp/xxx.jpg "请详细描述这张图片的内容"
```

## 返回格式

```json
{"success": true, "result": "图片分析结果文字", "provider": "glm-4.6v-flash"}
```

## 注意事项

- 从 `[图片路径: ...]` 标记中提取路径，直接作为第一个参数传入
- 工具内部自动压缩图片（1024px 长边）、转 base64、处理路径格式
- 主 API 不通时自动切换备用 API（gemini-3.1-flash-lite-preview）
- **不要**先用 `read` 工具读取图片元数据再决定是否调用本工具，直接调用即可

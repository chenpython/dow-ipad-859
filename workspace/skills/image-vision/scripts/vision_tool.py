#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
image-vision skill 核心脚本
功能：图像识别与分析
  - 主 API：智谱AI glm-4.6v-flash（JWT 鉴权）
  - 备用 API：ai.t8star gemini-3.1-flash-lite-preview（OpenAI 兼容格式）
图像在上传前自动压缩至 max_pixels（默认1024像素），以 base64 格式传输

支持图片来源：
  1. 本地文件路径（/tmp/xxx.jpg 等，含引用图片）
  2. HTTP(S) URL

用法:
    python3 vision_tool.py analyze <图片路径或URL> "<问题>"
"""

import sys
import os
import json
import base64
import time
import requests
import io
import urllib.request

# ── PIL 图片处理 ──────────────────────────────────────────────
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# ── 脚本自身目录，用于定位 config.json ─────────────────────────
_SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR   = os.path.dirname(_SCRIPT_DIR)   # skills/image-vision/
_CONFIG_PATH = os.path.join(_SKILL_DIR, "config.json")

# ── 常见临时目录候选（与 cow 框架保持一致）──────────────────────
_TMP_CANDIDATES = [
    "/tmp",
    "/root/dow-ipad-859/tmp",
    "/root/dify-on-wechat/tmp",
]


# ─────────────────────────────────────────────────────────────
# 配置加载
# ─────────────────────────────────────────────────────────────

def _load_config() -> dict:
    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "primary": {
            "provider": "zhipu",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "model": "glm-4.6v-flash",
            "api_key": "",
            "timeout": 60,
            "temperature": 0.7,
            "max_tokens": 2048
        },
        "fallback": {
            "provider": "openai_compatible",
            "base_url": "https://ai.t8star.cn/v1",
            "model": "gemini-3.1-flash-lite-preview",
            "api_key": "",
            "timeout": 60,
            "temperature": 0.7,
            "max_tokens": 2048
        },
        "image": {
            "max_pixels": 1024,
            "jpeg_quality": 85
        }
    }


# ─────────────────────────────────────────────────────────────
# 图片获取：本地文件 / URL / 相对路径修复
# ─────────────────────────────────────────────────────────────

def _read_local_file(path: str):
    """读取本地文件，返回 bytes 或 None"""
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception:
        return None


def _resolve_local_path(image_source: str):
    """
    处理各种可能的本地路径格式，参考 QwenVision._get_image_data 逻辑：
    1. 绝对路径直接读取
    2. tmp/ 开头的相对路径，尝试多个候选目录
    3. 仅文件名，尝试所有候选 tmp 目录
    返回 bytes 或 None
    """
    # 1. 直接绝对路径或相对路径（已存在）
    if os.path.isfile(image_source):
        return _read_local_file(image_source)

    # 2. 以 tmp/ 开头的相对路径 → 在多个候选目录里找
    if image_source.startswith("tmp/") or image_source.startswith("tmp\\"):
        basename = os.path.basename(image_source)
        for candidate_dir in _TMP_CANDIDATES:
            # 原始相对路径（相对于某些常见根目录）
            for root in ["/root/dow-ipad-859", "/root/dify-on-wechat", "/root", ""]:
                full = os.path.join(root, image_source) if root else image_source
                if os.path.isfile(full):
                    data = _read_local_file(full)
                    if data:
                        return data
            # 只用 basename 在 tmp 目录里找
            alt = os.path.join(candidate_dir, basename)
            if os.path.isfile(alt):
                data = _read_local_file(alt)
                if data:
                    return data

    # 3. 只是文件名，在所有 tmp 候选里找
    basename = os.path.basename(image_source)
    for candidate_dir in _TMP_CANDIDATES:
        alt = os.path.join(candidate_dir, basename)
        if os.path.isfile(alt):
            data = _read_local_file(alt)
            if data:
                return data

    return None


def _fetch_image_bytes(image_source: str) -> bytes:
    """
    根据来源获取图片原始字节：
      - http(s):// → 网络下载
      - 其他       → 本地文件（含引用图片路径、tmp/ 相对路径等）
    """
    src = image_source.strip()

    # 网络 URL
    if src.startswith("http://") or src.startswith("https://"):
        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()

    # 本地文件（含各种路径格式）
    data = _resolve_local_path(src)
    if data:
        return data

    raise FileNotFoundError(f"无法找到图片文件: {src}")


# ─────────────────────────────────────────────────────────────
# 图片压缩 → base64
# ─────────────────────────────────────────────────────────────

def _compress_to_base64(image_bytes: bytes, max_pixels: int = 1024, quality: int = 85) -> str:
    """
    将图片压缩至 max_pixels（长边），转换为 JPEG，返回带 data URI 前缀的 base64 字符串。
    格式：data:image/jpeg;base64,<data>（智谱/OpenAI 视觉 API 均支持此格式）
    """
    if not PIL_AVAILABLE:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"

    img = Image.open(io.BytesIO(image_bytes))

    # 统一转 RGB（去掉透明通道、调色板等）
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # 按长边缩放到 max_pixels（保持比例）
    w, h = img.size
    long_side = max(w, h)
    if long_side > max_pixels:
        scale = max_pixels / long_side
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        img = img.resize((new_w, new_h), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


# ─────────────────────────────────────────────────────────────
# 智谱 JWT Token
# ─────────────────────────────────────────────────────────────

def _generate_zhipu_token(api_key: str, exp_seconds: int = 3600) -> str:
    try:
        import jwt
        api_key_id, api_key_secret = api_key.split(".", 1)
        payload = {
            "api_key": api_key_id,
            "exp": int(round(time.time() * 1000)) + exp_seconds * 1000,
            "timestamp": int(round(time.time() * 1000)),
        }
        token = jwt.encode(
            payload,
            api_key_secret,
            algorithm="HS256",
            headers={"alg": "HS256", "sign_type": "SIGN"},
        )
        return token if isinstance(token, str) else token.decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"智谱 JWT Token 生成失败: {e}")


# ─────────────────────────────────────────────────────────────
# API 调用（主 / 备用）
# ─────────────────────────────────────────────────────────────

def _call_zhipu(cfg: dict, image_b64_url: str, question: str) -> str:
    """智谱 AI 视觉 API（JWT 鉴权）"""
    token = _generate_zhipu_token(cfg["api_key"])
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": cfg["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_b64_url}},
                    {"type": "text", "text": question},
                ],
            }
        ],
        "temperature": cfg.get("temperature", 0.7),
        "max_tokens": cfg.get("max_tokens", 2048),
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=cfg.get("timeout", 60))
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _call_openai_compatible(cfg: dict, image_b64_url: str, question: str) -> str:
    """OpenAI 兼容格式视觉 API"""
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": cfg["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_b64_url}},
                    {"type": "text", "text": question},
                ],
            }
        ],
        "temperature": cfg.get("temperature", 0.7),
        "max_tokens": cfg.get("max_tokens", 2048),
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=cfg.get("timeout", 60))
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _call_api(cfg: dict, image_b64_url: str, question: str) -> str:
    provider = cfg.get("provider", "openai_compatible")
    if provider == "zhipu":
        return _call_zhipu(cfg, image_b64_url, question)
    return _call_openai_compatible(cfg, image_b64_url, question)


# ─────────────────────────────────────────────────────────────
# 核心入口（带 fallback）
# ─────────────────────────────────────────────────────────────

def analyze_image(image_source: str, question: str) -> dict:
    """
    分析图片。
    返回 {"success": True, "result": "...", "provider": "..."} 或
         {"success": False, "error": "..."}
    """
    config    = _load_config()
    img_cfg   = config.get("image", {})
    max_px    = img_cfg.get("max_pixels", 1024)
    quality   = img_cfg.get("jpeg_quality", 85)

    # 1. 获取图片字节
    try:
        raw_bytes = _fetch_image_bytes(image_source)
    except Exception as e:
        return {"success": False, "error": f"图片获取失败: {e}"}

    # 2. 压缩 → base64
    try:
        image_b64_url = _compress_to_base64(raw_bytes, max_pixels=max_px, quality=quality)
    except Exception as e:
        return {"success": False, "error": f"图片处理失败: {e}"}

    errors = []

    # 3. 主 API（智谱）
    primary_cfg = config.get("primary", {})
    if primary_cfg.get("api_key"):
        try:
            result = _call_api(primary_cfg, image_b64_url, question)
            return {
                "success": True,
                "result": result,
                "provider": primary_cfg.get("model", "primary"),
            }
        except Exception as e:
            errors.append(f"主API({primary_cfg.get('model','?')}): {e}")

    # 4. 备用 API
    fallback_cfg = config.get("fallback", {})
    if fallback_cfg.get("api_key"):
        try:
            result = _call_api(fallback_cfg, image_b64_url, question)
            return {
                "success": True,
                "result": result,
                "provider": fallback_cfg.get("model", "fallback"),
            }
        except Exception as e:
            errors.append(f"备用API({fallback_cfg.get('model','?')}): {e}")

    if not errors:
        errors.append("未配置有效的 API Key，请检查 config.json")

    return {"success": False, "error": "; ".join(errors)}


# ─────────────────────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(json.dumps({
            "success": False,
            "error": "用法: vision_tool.py analyze <图片路径或URL> [问题]"
        }, ensure_ascii=False))
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "analyze":
        if len(sys.argv) < 3:
            print(json.dumps({"success": False, "error": "缺少图片路径参数"}, ensure_ascii=False))
            sys.exit(1)
        image_source = sys.argv[2]
        question     = sys.argv[3] if len(sys.argv) >= 4 else "请详细描述这张图片的内容。"
        result       = analyze_image(image_source, question)
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps({"success": False, "error": f"未知命令: {cmd}"}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()

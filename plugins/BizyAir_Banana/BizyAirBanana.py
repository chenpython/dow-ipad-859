#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import time
from typing import Dict, Any, Optional

import requests

import plugins
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from plugins import Plugin, Event, EventContext, EventAction

try:
    import oss2
except ImportError:
    oss2 = None



@plugins.register(
    name="BizyAirBanana",
    desire_priority=200,
    hidden=False,
    desc="使用 BizyAir Nano Banana 2 / Pro 文生图的插件",
    version="1.1",
    author="BizyAir",
)
class BizyAirBanana(Plugin):
    """
    BizyAir Nano Banana 2 / Pro 文生图插件。

    触发指令示例：
        nb2画图 一只可爱的小猫 --ar 3:4 --1k
    默认参数：
        - 画幅比例（aspect_ratio）：1:1
        - 分辨率（resolution）：2K
    """

    # 支持的分辨率映射，与 generate_image.py 保持一致
    RESOLUTION_MAP = {
        "1K": "1K",
        "1k": "1K",
        "2K": "2K",
        "2k": "2K",
        "4K": "4K",
        "4k": "4K",
        "auto": "auto",
        "AUTO": "auto",
    }

    # 支持的画幅比例
    VALID_RATIOS = {
        "1:1",
        "2:3",
        "3:2",
        "3:4",
        "4:3",
        "4:5",
        "5:4",
        "9:16",
        "16:9",
        "21:9",
        "auto",
        "AUTO",
    }

    def __init__(self):
        super().__init__()
        try:
            self.config = self._load_config()
            if not self.config:
                raise RuntimeError("未找到 bizyair_banana_config.json 配置文件")

            self.api_key = self.config.get("api_key")
            self.api_base = (self.config.get("api_base") or "").rstrip("/")
            # Nano Banana 2 模型 web_app_id
            self.web_app_id_nb2 = self.config.get("web_app_id_nb2", 47318)
            # Nano Banana Pro 模型 web_app_id（可在配置中覆盖）
            self.web_app_id_nbp = self.config.get("web_app_id_nbp", 47607)

            if not self.api_key or not self.api_base or not self.web_app_id_nb2:
                raise RuntimeError("bizyair_banana_config.json 中 api_key/api_base/web_app_id 配置不完整")

            # 指令前缀，默认支持小写 / 大写两种
            # nb2：Nano Banana 2
            self.commands_nb2 = self.config.get("commands_nb2", ["nb2画图", "NB2画图"])
            # nbp：Nano Banana Pro
            self.commands_nbp = self.config.get("commands_nbp", ["nbp画图", "NBP画图"])
            # nb2_edit：Nano Banana 2 改图
            self.commands_nb2_edit = self.config.get("commands_nb2_edit", ["nb2改图", "NB2改图"])
            # nbp_edit：Nano Banana Pro 改图
            self.commands_nbp_edit = self.config.get("commands_nbp_edit", ["nbp改图", "NBP改图"])

            # 改图相关设置
            self.web_app_id_nb2_edit = self.config.get("web_app_id_nb2_edit", 47116)
            self.web_app_id_nbp_edit = self.config.get("web_app_id_nbp_edit", 40878)
            self.web_app_id_nbp_multiview = self.config.get("web_app_id_nbp_multiview", 43998)
            self.commands_nbp_multiview = self.config.get("commands_nbp_multiview", ["nbp多视角", "NBP多视角"])
            self.waiting_for_image = {}  # user_id -> timestamp

            self.image_prompts = {}      # user_id -> {prompt, resolution, ratio, model_type}
            self.wait_timeout = 180      # 默认等待 180s


            # 默认参数
            self.default_ratio = self.config.get("default_ratio", "1:1")

            self.default_resolution = self.config.get("default_resolution", "2K")
            self.max_wait = int(self.config.get("max_wait", 300))
            self.poll_interval = int(self.config.get("poll_interval", 6))
            self.timeout = int(self.config.get("timeout", 180))


            # 绑定事件处理
            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context

            logger.info("[BizyAirBanana] 插件初始化成功")
        except Exception as e:
            logger.error(f"[BizyAirBanana] 初始化失败: {e}")
            raise

    def _load_config(self) -> Dict[str, Any]:
        """从插件同目录加载 bizyair_banana_config.json 配置。"""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(base_dir, "bizyair_banana_config.json")
        if not os.path.exists(config_path):
            logger.error(f"[BizyAirBanana] 配置文件不存在: {config_path}")
            return {}
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[BizyAirBanana] 读取配置文件失败: {e}")
            return {}

    def on_handle_context(self, e_context: EventContext):
        """主入口：拦截文本或图片消息，解析 nb2/nbp 指令并调用 BizyAir API。"""
        context = e_context["context"]
        user_id = context.get("session_id") or context.get("from_user_id")

        # 1. 处理图片消息
        if context.type == ContextType.IMAGE:
            if user_id in self.waiting_for_image:
                self._handle_waiting_image(e_context, user_id)
            return

        # 2. 仅处理文本消息
        if context.type != ContextType.TEXT:
            return

        content: str = (context.content or "").strip()
        if not content:
            return

        # 3. 解析指令前缀
        model_type, prefix = self._match_prefix(content)
        if not prefix:
            return

        # 去掉前缀后的参数字符串
        args_str = content[len(prefix) :].strip()

        # 4. 处理改图指令 (nb2_edit / nbp_edit / nbp_multiview)
        if model_type in ["nb2_edit", "nbp_edit", "nbp_multiview"]:

            if not oss2:
                e_context["reply"] = Reply(ReplyType.ERROR, "插件未检测到 'oss2' 依赖，请在环境执行 `pip install oss2` 后使用改图功能。")
                e_context.action = EventAction.BREAK_PASS
                return
            
            # 检测是否引用了图片
            actual_msg = context.kwargs.get('msg') if context.kwargs else None
            referenced_image_path = getattr(actual_msg, 'referenced_image_path', None) if actual_msg else None
            
            if referenced_image_path:
                # 直接使用引用图片改图
                logger.info(f"[BizyAirBanana] 检测到引用图片改图({model_type})，路径: {referenced_image_path}")
                self._handle_direct_edit(e_context, args_str, referenced_image_path, model_type=model_type)
            else:
                # 进入等待图片状态
                self._enter_waiting_state(e_context, args_str, user_id, model_type=model_type)
            return


        # 5. 下面是原有的文生图逻辑 (nb2 / nbp)
        if not args_str:
            # 没有任何描述，返回帮助信息
            reply = Reply(ReplyType.TEXT, self.get_help_text())
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return

        try:
            prompt, resolution, aspect_ratio = self._parse_args(args_str)
        except ValueError as ve:
            reply = Reply(ReplyType.ERROR, f"指令参数错误：{ve}")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return

        # 提前给用户一个“正在处理”的提示
        model_label = "Nano Banana Pro" if model_type == "nbp" else "Nano Banana 2"
        display_prompt = self._truncate_prompt(prompt)
        processing_msg = (
            f"🎨 已收到 BizyAir {model_label} 生图请求。\n"
            f"- 提示词：{display_prompt}\n"
            f"- 分辨率：{resolution}\n"
            f"- 画幅比例：{aspect_ratio}\n"
            f"正在生成图片，请稍候..."
        )
        e_context["channel"].send(Reply(ReplyType.TEXT, processing_msg), e_context["context"])


        try:
            t0 = time.time()
            img_url = self._generate_image(prompt, resolution, aspect_ratio, model_type=model_type)
            elapsed = int(time.time() - t0)

            # 成功返回图片 URL
            image_reply = Reply(ReplyType.IMAGE_URL, img_url)
            e_context["reply"] = image_reply

            # 同时附加一条耗时说明（通过 channel 额外发送）
            duration_msg = f"✅ BizyAir {model_label} 生图完成（耗时 {elapsed}s）"
            e_context["channel"].send(Reply(ReplyType.TEXT, duration_msg), e_context["context"])

            e_context.action = EventAction.BREAK_PASS
        except Exception as e:
            logger.error(f"[BizyAirBanana] 调用 BizyAir API 失败: {e}")
            reply = Reply(ReplyType.ERROR, f"调用 BizyAir {model_label} 失败：{e}")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS

    def _match_prefix(self, content: str) -> (Optional[str], Optional[str]):
        """
        匹配配置中的任意前缀，返回 (model_type, prefix)。
        model_type: "nb2", "nbp", "nb2_edit", "nbp_edit"
        """
        for p in self.commands_nb2_edit:
            if content.startswith(p):
                return "nb2_edit", p
        for p in self.commands_nbp_edit:
            if content.startswith(p):
                return "nbp_edit", p
        for p in self.commands_nbp_multiview:
            if content.startswith(p):
                return "nbp_multiview", p
        for p in self.commands_nb2:


            if content.startswith(p):
                return "nb2", p
        for p in self.commands_nbp:
            if content.startswith(p):
                return "nbp", p
        return None, None

    def _enter_waiting_state(self, e_context: EventContext, args_str: str, user_id: str, model_type: str = "nb2_edit"):
        """进入等待图片上传状态。"""
        try:
            # 多视角模式不需要强制输入提示词
            require_prompt = (model_type not in ["nbp_multiview"])
            prompt, resolution, aspect_ratio = self._parse_args(args_str, require_prompt=require_prompt)
        except ValueError as ve:
            # 改图逻辑下，如果没输入参数且非多视角，给个默认引导
            if "缺少提示词" in str(ve):
                prompt, resolution, aspect_ratio = "更美一点", self.default_resolution, self.default_ratio
            else:
                e_context["reply"] = Reply(ReplyType.ERROR, f"指令参数错误：{ve}")
                e_context.action = EventAction.BREAK_PASS
                return


        self.waiting_for_image[user_id] = time.time()
        self.image_prompts[user_id] = {
            "prompt": prompt,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "model_type": model_type
        }
        
        model_label = "nbp"
        if model_type == "nbp_edit": model_label = "nbp"
        elif model_type == "nbp_multiview": model_label = "nbp多视角"
        else: model_label = "nb2"
        
        display_prompt = self._truncate_prompt(prompt) if prompt else "默认设置"
        prompt_text = f"：'{display_prompt}'" if model_type != "nbp_multiview" else ""
        reply = Reply(ReplyType.TEXT, f"📸 已收到 {model_label} 请求{prompt_text}\n请在3分钟内发送需要编辑的图片。")


        e_context["reply"] = reply
        e_context.action = EventAction.BREAK_PASS


    def _handle_waiting_image(self, e_context: EventContext, user_id: str):
        """处理用户在等待状态下发送的图片。"""
        # 检查超时
        if time.time() - self.waiting_for_image.get(user_id, 0) > self.wait_timeout:
            del self.waiting_for_image[user_id]
            if user_id in self.image_prompts: del self.image_prompts[user_id]
            return

        context = e_context["context"]
        params = self.image_prompts.get(user_id)
        
        # 清除状态，避免重复触发
        del self.waiting_for_image[user_id]
        del self.image_prompts[user_id]

        # 获取图片路径
        # chatgpt-on-wechat 中 context.content 通常是图片路径
        image_path = context.content
        
        self._execute_edit_task(
            e_context, 
            params["prompt"], 
            params["resolution"], 
            params["aspect_ratio"], 
            image_path, 
            model_type=params.get("model_type", "nb2_edit")
        )

    def _handle_direct_edit(self, e_context: EventContext, args_str: str, image_path: str, model_type: str = "nb2_edit"):
        """处理引用图片直接改图。"""
        try:
            require_prompt = (model_type not in ["nbp_multiview"])
            prompt, resolution, aspect_ratio = self._parse_args(args_str, require_prompt=require_prompt)
        except ValueError as ve:
            if "缺少提示词" in str(ve):
                prompt, resolution, aspect_ratio = "更美一点", self.default_resolution, self.default_ratio
            else:
                e_context["reply"] = Reply(ReplyType.ERROR, f"指令参数错误：{ve}")
                e_context.action = EventAction.BREAK_PASS
                return

        self._execute_edit_task(e_context, prompt, resolution, aspect_ratio, image_path, model_type=model_type)


    def _execute_edit_task(self, e_context: EventContext, prompt: str, resolution: str, aspect_ratio: str, image_path: str, model_type: str = "nb2_edit"):
        """执行改图任务的核心流程。"""
        context = e_context["context"]
        e_context.action = EventAction.BREAK_PASS
        
        # 提示处理中
        model_label = "nbp"
        if model_type == "nbp_edit": model_label = "nbp"
        elif model_type == "nbp_multiview": model_label = "nbp多视角"
        else: model_label = "nb2"

        display_prompt = self._truncate_prompt(prompt) if prompt else "默认设置"
        prompt_info = f"\n- 提示词：{display_prompt}" if model_type != "nbp_multiview" else ""
        processing_reply = Reply(ReplyType.TEXT, f"🎨 正在根据图片进行 {model_label} 操作...{prompt_info}\n正在上传并生成图片，请稍候...")
        e_context["channel"].send(processing_reply, context)



        try:
            t0 = time.time()
            
            # 1. 上传图片到 BizyAir
            oss_url = self._upload_image_to_bizyair(image_path)
            
            # 2. 创建并执行改图任务
            img_url = self._generate_edit_image(prompt, resolution, aspect_ratio, oss_url, model_type=model_type)
            
            elapsed = int(time.time() - t0)
            
            # 返回结果
            e_context["channel"].send(Reply(ReplyType.IMAGE_URL, img_url), context)
            e_context["channel"].send(Reply(ReplyType.TEXT, f"✅ {model_label} 改图完成（耗时 {elapsed}s）"), context)
            
        except Exception as e:
            logger.error(f"[BizyAirBanana] 改图任务失败: {e}")
            e_context["channel"].send(Reply(ReplyType.ERROR, f"{model_label} 改图失败：{e}"), context)


    def _upload_image_to_bizyair(self, local_path: str) -> str:
        """上传本地图片到 BizyAir OSS 并提交资源，返回最终 URL。"""
        if not os.path.exists(local_path):
            raise RuntimeError(f"本地图片不存在: {local_path}")
        
        file_name = os.path.basename(local_path)
        
        # 1. 获取上传凭证
        token_url = f"{self.api_base}/x/v1/upload/token"
        masked_key = f"{self.api_key[:6]}***{self.api_key[-4:]}" if self.api_key and len(self.api_key) > 10 else "Invalid/Short Key"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        params = {"file_name": file_name, "file_type": "inputs"}
        
        logger.info(f"[BizyAirBanana] 正在获取上传凭证: URL={token_url}, APIKey={masked_key}")
        try:
            resp = requests.get(token_url, headers=headers, params=params, timeout=30)
            if resp.status_code != 200:
                logger.error(f"[BizyAirBanana] 获取凭证失败: 状态码={resp.status_code}, 响应内容={resp.text}")
            resp.raise_for_status()
            token_data = resp.json()
        except Exception as e:
            logger.error(f"[BizyAirBanana] 请求上传凭证异常: {e}")
            raise

        if not token_data.get("status"):
            raise RuntimeError(f"获取上传凭证失败: {token_data.get('message')}")
        
        data = token_data["data"]
        file_info = data["file"]
        storage = data["storage"]
        
        logger.info(f"[BizyAirBanana] 获取凭证成功，准备上传 OSS: object_key={file_info['object_key']}")
        
        # 2. 使用 oss2 上传
        logger.info(f"[BizyAirBanana] 正在上传图片到 OSS, Endpoint={storage['endpoint']}, Bucket={storage['bucket']}")

        auth = oss2.StsAuth(file_info["access_key_id"], file_info["access_key_secret"], file_info["security_token"])
        bucket = oss2.Bucket(auth, storage["endpoint"], storage["bucket"])
        
        # oss2 的请求可能需要清理 region 的前缀 (官方教程提到的兼容性)
        # bucket.put_object_from_file 会处理上传
        bucket.put_object_from_file(file_info["object_key"], local_path)
        
        # 3. 提交资源
        commit_url = f"{self.api_base}/x/v1/input_resource/commit"
        commit_payload = {
            "name": file_name,
            "object_key": file_info["object_key"]
        }
        commit_resp = requests.post(commit_url, headers=headers, json=commit_payload, timeout=30)
        commit_resp.raise_for_status()
        commit_data = commit_resp.json()
        if not commit_data.get("status"):
            raise RuntimeError(f"提交资源失败: {commit_data.get('message')}")
            
        final_url = commit_data["data"]["url"]
        logger.info(f"[BizyAirBanana] 图片上传成功: {final_url}")
        return final_url

    def _generate_edit_image(self, prompt: str, resolution: str, aspect_ratio: str, image_url: str, model_type: str = "nb2_edit") -> str:
        """创建改图任务并轮询结果。"""
        request_id = self._create_edit_task(prompt, resolution, aspect_ratio, image_url, model_type=model_type)
        self._poll_status(request_id)
        return self._fetch_output_url(request_id)

    def _create_edit_task(self, prompt: str, resolution: str, aspect_ratio: str, image_url: str, model_type: str = "nb2_edit") -> str:
        """Step 1: 创建改图任务 (nb2: 47116 / nbp: 40878)。"""
        url = self.api_base.rstrip("/") + "/w/v1/webapp/task/openapi/create"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        
        if model_type == "nbp_edit":
            web_app_id = self.web_app_id_nbp_edit
            field_prefix = "1:BizyAir_NanoBananaPro"
            input_values = {
                f"{field_prefix}.prompt": prompt,
                f"{field_prefix}.aspect_ratio": aspect_ratio,
                f"{field_prefix}.resolution": resolution,
                "2:LoadImage.image": image_url
            }
        elif model_type == "nbp_multiview":
            web_app_id = self.web_app_id_nbp_multiview
            field_prefix = "117:BizyAir_NanoBananaPro"
            input_values = {
                f"{field_prefix}.aspect_ratio": aspect_ratio,
                f"{field_prefix}.resolution": resolution,
                f"{field_prefix}.quality": "high",
                f"{field_prefix}.character_consistency": "",
                "85:LoadImage.image": image_url
            }
        else:
            web_app_id = self.web_app_id_nb2_edit
            field_prefix = "12:BizyAir_NanoBanana2"
            input_values = {
                f"{field_prefix}.prompt": prompt,
                f"{field_prefix}.aspect_ratio": aspect_ratio,
                f"{field_prefix}.resolution": resolution,
                "2:LoadImage.image": image_url
            }

        # 根据示例代码配置参数
        payload = {
            "web_app_id": web_app_id,
            "suppress_preview_output": True,
            "input_values": input_values
        }
        
        logger.info(f"[BizyAirBanana] 提交改图/多视角任务({model_type}): prompt='{prompt}', image={image_url}")

        
        t1 = time.time()
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"[BizyAirBanana] 创建改图任务请求异常(耗时 {int(time.time()-t1)}s): {e}")
            raise


        request_id = data.get("requestId") or data.get("request_id")
        if not request_id:
            raise RuntimeError(f"创建改图任务请求失败: {json.dumps(data, ensure_ascii=False)}")
        
        logger.info(f"[BizyAirBanana] 改图任务已提交，requestId={request_id}，耗时 {int(time.time()-t1)}s")
        return str(request_id)



    def _truncate_prompt(self, prompt: str) -> str:
        """
        当提示词过长时进行截断展示：
        如果超过30个字，只显示前10个和后10个，中间用...代替。
        """
        if len(prompt) <= 30:
            return prompt
        return f"{prompt[:10]}...{prompt[-10:]}"

    def _parse_args(self, args_str: str, require_prompt: bool = True):


        """
        解析类似：
            一只可爱的小猫 --ar 3:4 --1k

        返回：
            prompt, resolution, aspect_ratio
        """
        # 默认值
        resolution = self.default_resolution
        aspect_ratio = self.default_ratio

        # 解析 --ar/--ratio 参数
        ratio_match = re.search(r"--(?:ar|ratio)\s+([0-9]+:[0-9]+|auto)", args_str, flags=re.IGNORECASE)
        if ratio_match:
            aspect_ratio = ratio_match.group(1)
            # 从原始字符串中移除该片段，避免进入 prompt
            args_str = args_str.replace(ratio_match.group(0), "").strip()

        # 解析 --1k / --2k / --4k / --auto
        res_match = re.search(r"--(1k|2k|4k|1K|2K|4K|auto|AUTO)", args_str)
        if res_match:
            key = res_match.group(1)
            if key not in self.RESOLUTION_MAP:
                raise ValueError(f"不支持的分辨率标记 '--{key}'，可选：--1k/--2k/--4k/--auto")
            resolution = self.RESOLUTION_MAP[key]
            args_str = args_str.replace(res_match.group(0), "").strip()

        # 清理多余空格和连字符
        prompt = args_str.strip()
        prompt = re.sub(r"\s+", " ", prompt)

        if not prompt and require_prompt:
            raise ValueError("缺少提示词，例如：nb2画图 一只可爱的小猫 --ar 3:4 --1k")


        # 校验画幅比例和分辨率
        if aspect_ratio not in self.VALID_RATIOS:
            raise ValueError(
                f"不支持的画幅比例 '{aspect_ratio}'。可选："
                f"{', '.join(sorted({r for r in self.VALID_RATIOS if r.lower() != 'auto'}))} 或 auto"
            )

        # 规范化大小写
        if aspect_ratio == "AUTO":
            aspect_ratio = "auto"

        return prompt, resolution, aspect_ratio

    # ───────────── BizyAir API 封装 ─────────────

    def _create_task(self, prompt: str, resolution: str, aspect_ratio: str, model_type: str = "nb2") -> str:
        """
        Step 1: 创建任务
        POST /w/v1/webapp/task/openapi/create
        响应：{"requestId": "<uuid>"}
        """
        url = self.api_base.rstrip("/") + "/w/v1/webapp/task/openapi/create"

        # 根据模型类型选择不同的 web_app_id 和字段前缀
        if model_type == "nbp":
            web_app_id = self.web_app_id_nbp
            field_prefix = "17:BizyAir_NanoBananaPro"
        else:
            web_app_id = self.web_app_id_nb2
            field_prefix = "17:BizyAir_NanoBanana2"

        payload = {
            "web_app_id": web_app_id,
            "suppress_preview_output": True,
            "input_values": {
                f"{field_prefix}.prompt": prompt,
                f"{field_prefix}.resolution": resolution,
                f"{field_prefix}.aspect_ratio": aspect_ratio,
            },
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        logger.info(
            f"[BizyAirBanana] 提交生图任务(model={model_type}): prompt='{prompt}', resolution={resolution}, aspect_ratio={aspect_ratio}"
        )
        t1 = time.time()
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"[BizyAirBanana] 创建生图任务请求异常(耗时 {int(time.time()-t1)}s): {e}")
            raise


        request_id = data.get("requestId") or data.get("request_id")
        if not request_id:
            raise RuntimeError(
                "任务创建失败，服务器未返回 requestId，完整响应: "
                + json.dumps(data, ensure_ascii=False)
            )

        logger.info(f"[BizyAirBanana] 任务已提交，requestId={request_id}，耗时 {int(time.time()-t1)}s")
        return str(request_id)


    def _poll_status(self, request_id: str):
        """
        Step 2: 轮询任务状态
        GET /w/v1/webapp/task/openapi/detail?requestId={requestId}
        状态枚举：
            Queuing / Preparing / Running → 继续等待
            Success                       → 进入结果查询
            Failed / Canceled             → 抛错
        """
        url = (
            self.api_base.rstrip("/")
            + f"/w/v1/webapp/task/openapi/detail?requestId={request_id}"
        )
        headers = {"Authorization": f"Bearer {self.api_key}"}

        elapsed = 0
        logger.info(
            f"[BizyAirBanana] 开始轮询任务状态（最长 {self.max_wait}s，每 {self.poll_interval}s 一次）"
        )

        while elapsed < self.max_wait:
            time.sleep(self.poll_interval)
            elapsed += self.poll_interval
            try:
                resp = requests.get(url, headers=headers, timeout=self.timeout)
                resp.raise_for_status()


                body = resp.json()
                data = body.get("data", {})
                status = data.get("status", "Unknown")
            except Exception as e:
                logger.warning(f"[BizyAirBanana] 状态查询异常（{elapsed}s）: {e}")
                continue

            queue_info = data.get("queueInfo")
            extra = f"，排队信息: {queue_info}" if queue_info else ""
            logger.info(f"[BizyAirBanana] [{elapsed:>4}s] status={status}{extra}")

            if status == "Success":
                logger.info("[BizyAirBanana] 生图任务成功")
                return
            if status in {"Failed", "Canceled"}:
                raise RuntimeError(f"任务终止，status={status}")

        raise RuntimeError(f"轮询超时（{self.max_wait}s），任务仍未完成")

    def _fetch_output_url(self, request_id: str) -> str:
        """
        Step 3: 获取产出图片 URL
        GET /w/v1/webapp/task/openapi/outputs?requestId={requestId}
        返回 data.outputs[0].object_url
        """
        url = (
            self.api_base.rstrip("/")
            + f"/w/v1/webapp/task/openapi/outputs?requestId={request_id}"
        )
        headers = {"Authorization": f"Bearer {self.api_key}"}

        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            resp.raise_for_status()


            body = resp.json()
        except Exception as e:
            raise RuntimeError(f"获取结果失败: {e}")

        data = body.get("data", {})
        outputs = data.get("outputs", [])
        if not outputs:
            raise RuntimeError(
                "outputs 为空，完整响应: "
                + json.dumps(body, ensure_ascii=False, indent=2)
            )

        first = outputs[0]
        img_url = first.get("object_url", "")
        if not img_url:
            raise RuntimeError(f"outputs[0] 缺少 object_url 字段: {first}")

        audit = first.get("audit_status")
        error_typ = first.get("error_type", "NOT_ERROR")
        if error_typ != "NOT_ERROR":
            err_msg = first.get("error_msg", "未知错误")
            raise RuntimeError(f"任务报错: error_type={error_typ}，error_msg={err_msg}")
        if audit == 3:
            logger.warning("[BizyAirBanana] 图片审核未通过，URL 可能无法正常访问")

        return img_url

    def _generate_image(self, prompt: str, resolution: str, aspect_ratio: str, model_type: str = "nb2") -> str:
        """
        高层封装：顺序执行三步，返回最终图片 URL。
        """
        request_id = self._create_task(prompt, resolution, aspect_ratio, model_type=model_type)
        self._poll_status(request_id)
        img_url = self._fetch_output_url(request_id)
        logger.info(f"[BizyAirBanana] 图片生成成功(model={model_type})，URL={img_url}")
        return img_url

    def get_help_text(self, **kwargs) -> str:
        """
        插件帮助文本，在用户只输入前缀或出错时返回。
        """
        lines = [
            "🎨 BizyAir Banana 绘图插件使用说明：",
            "",
            "1. 基础文生图 (nb2 或 nbp)：",
            "   nb2画图 一只可爱的小猫",
            "   nbp画图 一只可爱的小猫",
            "",
            "2. 基础改图 (对单图进行再编辑)：",
            "   方式 A：发送指令 'nb2改图 戴上墨镜' (或nbp改图)，随后发送图片",
            "   方式 B：发送图片后，回复(引用)图片并发送 'nb2改图 戴上墨镜' (或nbp改图)",
            "",
            "3. 场景多视角 (基于单图生成多视角)：",
            "   方式 A：发送指令 'nbp多视角'，随后发送图片",
            "   方式 B：引用图片并发送 'nbp多视角'",
            "",
            "4. 指定参数 (--ar / --1k / --4k)：",
            "   nb2画图 赛博朋克城市 --ar 16:9",
            "   nbp多视角 --ar 9:16",
            "",
            f"默认比例：{self.default_ratio}；默认分辨率：{self.default_resolution}",
        ]
        return "\n".join(lines)
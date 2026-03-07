"""
Agent Bridge - Integrates Agent system with existing COW bridge
"""

import json
import os
import re
import xml.etree.ElementTree as _ET
from typing import Optional, List

from agent.protocol import Agent, LLMRequest
from agent.protocol.models import LLMModel
from bridge.agent_event_handler import AgentEventHandler
from bridge.agent_initializer import AgentInitializer
from bridge.context import Context

# Avoid circular import - Bridge type is used only for type hints
# from bridge.bridge import Bridge
from bridge.reply import Reply, ReplyType
from common import const
from common.log import logger
from common.utils import expand_path
from bot.openai_compatible_bot import OpenAICompatibleBot


def _repair_music_xml(xml_content: str) -> str:
    """
    校验音乐卡片 XML 合法性，若不合法则从中提取关键字段后重新构建。
    兼容两类来源：
      1. search_music.py 脚本输出（正常情况下应合法）
      2. LLM 从记忆中直接"背出"的 XML（高概率格式错误）
    """
    try:
        _ET.fromstring(xml_content)
        return xml_content  # 合法，直接透传
    except _ET.ParseError as _pe:
        logger.warning(f"[AgentBridge] MUSIC_CARD XML 格式错误，尝试自动修复: {_pe}")

    # --- 用正则提取字段（容忍标签不闭合）---
    def _rget(tag, text, default=""):
        m = re.search(rf'<{tag}>(.*?)</{tag}>', text, re.DOTALL)
        return m.group(1).strip() if m else default

    # cdnthumburl 容易和后续标签粘连，单独用更宽松的正则
    _thumb_m = re.search(r'<cdnthumburl>(https?://[^<]+?)(?:<|$)', xml_content)
    thumb_url = _thumb_m.group(1).strip() if _thumb_m else ""

    appid_m = re.search(r'<appmsg[^>]*appid="([^"]+)"', xml_content)
    appid = appid_m.group(1) if appid_m else "wx8dd6ecd81906fd84"

    title   = _rget("title", xml_content)
    singer  = _rget("des",   xml_content)
    music_url = _rget("dataurl", xml_content)
    source  = _rget("sourcedisplayname", xml_content, "网易云音乐")

    if not (title and singer and music_url):
        logger.error("[AgentBridge] 无法从损坏的 XML 提取必要字段，返回原始内容")
        return xml_content

    def _safe(u):
        if not u:
            return ""
        u = u.replace("&amp;", "&").replace("&", "&amp;")
        if u.startswith("http://"):
            u = "https://" + u[7:]
        return u

    thumb_safe = _safe(thumb_url)
    music_safe = _safe(music_url)

    repaired = (
        f'<appmsg appid="{appid}" sdkver="0">'
        f'<title>{title}</title>'
        f'<des>{singer}</des>'
        f'<action>view</action>'
        f'<type>76</type>'
        f'<showtype>0</showtype>'
        f'<soundtype>0</soundtype>'
        f'<mediatagname>\u97f3\u4e50</mediatagname>'
        f'<messageaction></messageaction>'
        f'<content></content>'
        f'<contentattr>0</contentattr>'
        f'<url>https://y.qq.com/m/index.html</url>'
        f'<lowurl></lowurl>'
        f'<dataurl>{music_safe}</dataurl>'
        f'<lowdataurl></lowdataurl>'
        f'<appattach>'
        f'<totallen>0</totallen>'
        f'<attachid></attachid>'
        f'<emoticonmd5></emoticonmd5>'
        f'<fileext></fileext>'
        f'<cdnthumburl>{thumb_safe}</cdnthumburl>'
        f'<cdnthumbaeskey></cdnthumbaeskey>'
        f'<aeskey></aeskey>'
        f'</appattach>'
        f'<extinfo></extinfo>'
        f'<sourceusername></sourceusername>'
        f'<sourcedisplayname>{source}</sourcedisplayname>'
        f'<thumburl>{thumb_safe}</thumburl>'
        f'<songalbumurl>{thumb_safe}</songalbumurl>'
        f'<songlyric></songlyric>'
        f'</appmsg>'
    )
    logger.info(
        f"[AgentBridge] MUSIC_CARD XML 修复完成 "
        f"(原始长度={len(xml_content)}, 修复后长度={len(repaired)}): "
        f"title={title!r}, singer={singer!r}"
    )
    return repaired


def _build_music_xml_from_data(data: dict) -> str:
    """
    从结构化数据纯代码构建音乐卡片 XML。
    这是正确构建 XML 的唯一入口，LLM 不参与任何拼接过程。
    """
    # 平台 → AppID 映射
    _PLATFORM_APPID = {
        "netease": "wx8dd6ecd81906fd84",
        "kugou":   "wx79f2c4418704b4f8",
        "kuwo":    "wxc305711a2a7ad71c",
        "qishui":  "wx904fb3ecf62c7dea",
        "qq":      "wx5aa333606550dfd5",
    }
    _PLATFORM_SOURCE = {
        "netease": "\u7f51\u6613\u4e91\u97f3\u4e50",
        "kugou":   "\u9177\u72d7\u97f3\u4e50",
        "kuwo":    "\u9177\u6211\u97f3\u4e50",
        "qishui":  "\u6c7d\u6c34\u97f3\u4e50",
        "qq":      "QQ\u97f3\u4e50",
    }

    title     = (data.get("title")     or "").strip()
    singer    = (data.get("singer")    or "").strip()
    music_url = (data.get("music_url") or "").strip()
    thumb_url = (data.get("thumb_url") or "").strip()
    platform  = (data.get("platform")  or "netease").strip().lower()
    source    = (data.get("source")    or "").strip()

    if not (title and singer and music_url):
        logger.error(f"[AgentBridge] _build_music_xml_from_data: 缺少必要字段 title/singer/music_url")
        return ""

    # 根据 URL 域名自动选择 AppID（与 search_music.py / SearchMusic插件逻辑一致）
    if "kuwo.cn" in music_url:
        platform = "kuwo"
    elif "kugou.com" in music_url:
        platform = "kugou"
    elif "music.163.com" in music_url:
        platform = "netease"
    elif "qishui" in music_url or "douyinpic.com" in music_url:
        platform = "qishui"

    appid  = _PLATFORM_APPID.get(platform, _PLATFORM_APPID["netease"])
    source = source or _PLATFORM_SOURCE.get(platform, "\u97f3\u4e50")

    def _safe(u):
        if not u:
            return ""
        u = u.replace("&amp;", "&").replace("&", "&amp;")
        if u.startswith("http://"):
            u = "https://" + u[7:]
        return u

    thumb_safe = _safe(thumb_url)
    music_safe = _safe(music_url)

    xml = (
        f'<appmsg appid="{appid}" sdkver="0">'
        f'<title>{title}</title>'
        f'<des>{singer}</des>'
        f'<action>view</action>'
        f'<type>76</type>'
        f'<showtype>0</showtype>'
        f'<soundtype>0</soundtype>'
        f'<mediatagname>\u97f3\u4e50</mediatagname>'
        f'<messageaction></messageaction>'
        f'<content></content>'
        f'<contentattr>0</contentattr>'
        f'<url>https://y.qq.com/m/index.html</url>'
        f'<lowurl></lowurl>'
        f'<dataurl>{music_safe}</dataurl>'
        f'<lowdataurl></lowdataurl>'
        f'<appattach>'
        f'<totallen>0</totallen>'
        f'<attachid></attachid>'
        f'<emoticonmd5></emoticonmd5>'
        f'<fileext></fileext>'
        f'<cdnthumburl>{thumb_safe}</cdnthumburl>'
        f'<cdnthumbaeskey></cdnthumbaeskey>'
        f'<aeskey></aeskey>'
        f'</appattach>'
        f'<extinfo></extinfo>'
        f'<sourceusername></sourceusername>'
        f'<sourcedisplayname>{source}</sourcedisplayname>'
        f'<thumburl>{thumb_safe}</thumburl>'
        f'<songalbumurl>{thumb_safe}</songalbumurl>'
        f'<songlyric></songlyric>'
        f'</appmsg>'
    )
    logger.info(
        f"[AgentBridge] 纯代码构建 XML 完成: platform={platform}, appid={appid}, "
        f"title={title!r}, singer={singer!r}, xml长度={len(xml)}"
    )
    return xml


def _convert_claude_tools_to_openai(tools):
    """
    Convert tools from Claude format to OpenAI function-calling format.
    Claude: [{"name": ..., "description": ..., "input_schema": {...}}]
    OpenAI: [{"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}]
    """
    if not tools:
        return None
    openai_tools = []
    for tool in tools:
        if tool is None:
            continue  # Skip None entries (failed tool loads)
        if isinstance(tool, dict):
            # Already in OpenAI format
            if tool.get("type") == "function":
                openai_tools.append(tool)
            # Claude format
            elif "name" in tool:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema") or tool.get("parameters") or {},
                    }
                })
    return openai_tools if openai_tools else None


def _inject_modelscope_call_with_tools(bot_instance):
    """
    Inject a native call_with_tools implementation into ModelScopeBot using
    requests (same as its own reply_text/reply_text_stream methods).
    This avoids the openai library entirely, preventing api_key=None TypeError.
    """
    import types, json, requests as _requests

    def call_with_tools(self, messages, tools=None, stream=False, **kwargs):
        from config import conf
        from bot.openai_compatible_bot import OpenAICompatibleBot
        api_key = conf().get("modelscope_api_key") or ""
        api_base = conf().get("modelscope_api_base",
                               "https://api-inference.modelscope.cn/v1")
        if api_base.endswith("/"):
            url = api_base + "chat/completions"
        else:
            url = api_base + "/chat/completions"

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        }

        model = kwargs.get("model") or conf().get("model", "deepseek-ai/DeepSeek-V3.1")

        # ✅ 修复1: 将 Claude content-block 格式的 messages 转换为 OpenAI 字符串格式
        # agent_stream.py 内部用 Claude 格式存储消息（content 为 list of blocks），
        # 但 ModelScope 使用 OpenAI 兼容 API，必须转换后才能正确处理 tool_result 等多轮消息
        _converter = OpenAICompatibleBot.__new__(OpenAICompatibleBot)
        messages = _converter._convert_messages_to_openai_format(messages)

        # ✅ 修复2: 将 system prompt 注入到 messages 开头
        # AgentLLMModel.call_stream() 通过 kwargs['system'] 传来 system prompt，
        # 原来的代码将其丢弃，导致 LLM 不知道自己有工具可以调用
        system_prompt = kwargs.get('system')
        if system_prompt:
            if not messages or messages[0].get('role') != 'system':
                messages = [{"role": "system", "content": system_prompt}] + messages
            else:
                messages[0] = {"role": "system", "content": system_prompt}

        body = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "temperature": kwargs.get("temperature", conf().get("temperature", 0.3)),
            "top_p": kwargs.get("top_p", conf().get("top_p", 1.0)),
        }
        # Convert tools from Claude format → OpenAI format, filter None entries
        openai_tools = _convert_claude_tools_to_openai(tools)
        if openai_tools:
            body["tools"] = openai_tools
            body["tool_choice"] = kwargs.get("tool_choice", "auto")
        if kwargs.get("max_tokens"):
            body["max_tokens"] = kwargs["max_tokens"]

        if stream:
            def _gen():
                try:
                    resp = _requests.post(url, headers=headers,
                                          data=json.dumps(body, ensure_ascii=False),
                                          stream=True, timeout=120)
                    if resp.status_code != 200:
                        yield {"error": True, "message": f"ModelScope API error {resp.status_code}: {resp.text[:300]}", "status_code": resp.status_code}
                        return
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8") if isinstance(line, bytes) else line
                        if decoded.startswith("data: "):
                            data_str = decoded[6:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                yield json.loads(data_str)
                            except json.JSONDecodeError:
                                pass
                except Exception as e:
                    yield {"error": True, "message": str(e), "status_code": 500}
            return _gen()
        else:
            try:
                resp = _requests.post(url, headers=headers,
                                      data=json.dumps(body, ensure_ascii=False),
                                      timeout=120)
                return resp.json()
            except Exception as e:
                return {"error": True, "message": str(e), "status_code": 500}

    bot_instance.call_with_tools = types.MethodType(call_with_tools, bot_instance)
    logger.info(f"[AgentBridge] Injected native requests-based call_with_tools into ModelScopeBot")
    return bot_instance


def _inject_openai_compatible_call_with_tools(bot_instance, orig_class_name: str):
    """
    通用 OpenAI 兼容接口注入（基于 requests，不依赖旧版 openai SDK）。
    支持所有使用 OpenAI 兼容格式的第三方中转 API（含 grok/Claude 中转等）。
    """
    import types, json, requests as _requests

    def _get_api_cfg(cls_name: str):
        from config import conf
        if 'Dashscope' in cls_name or 'dashscope' in cls_name.lower():
            return (
                conf().get("dashscope_api_key", ""),
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            )
        if 'SiliconFlow' in cls_name:
            return (
                conf().get("siliconflow_api_key", ""),
                conf().get("siliconflow_api_base", "https://api.siliconflow.cn/v1"),
            )
        if 'DeepSeek' in cls_name:
            return (
                conf().get("deepseek_api_key", ""),
                conf().get("deepseek_api_base", "https://api.deepseek.com/v1"),
            )
        if 'Moonshot' in cls_name:
            return (
                conf().get("moonshot_api_key", ""),
                "https://api.moonshot.cn/v1",
            )
        if 'LongCat' in cls_name:
            return (
                conf().get("longcat_api_key", ""),
                conf().get("longcat_api_base", "https://api.longcat.chat/openai/v1"),
            )
        # 默认：ChatGPT / 通用中转 API
        api_base = conf().get("open_ai_api_base", "https://api.openai.com/v1")
        return (
            conf().get("open_ai_api_key", ""),
            api_base,
        )

    def _tools_to_text_prompt(tools_list):
        """将tools转换为文本格式，用于不支持function calling的API"""
        if not tools_list:
            return ""
        lines = ["\n\n你有以下工具可以使用："]
        for i, tool in enumerate(tools_list, 1):
            name = tool.get("name", "")
            desc = tool.get("description", "")
            lines.append(f"\n{i}. 工具名称: {name}")
            lines.append(f"   描述: {desc}")
            params = tool.get("input_schema", {}).get("properties", {})
            if params:
                lines.append("   参数:")
                for pname, pinfo in params.items():
                    ptype = pinfo.get("type", "any")
                    pdesc = pinfo.get("description", "")
                    lines.append(f"     - {pname} ({ptype}): {pdesc}")
        lines.append("\n\n当你需要使用工具时，请按以下格式回复：")
        lines.append("【工具调用】")
        lines.append("工具: <工具名称>")
        lines.append("参数: <JSON格式的参数>")
        lines.append("【/工具调用】")
        return "\n".join(lines)

    def _parse_text_tool_calls(content):
        """从文本响应中解析工具调用"""
        import re
        tool_calls = []
        if not content:
            return content, tool_calls
        
        # 匹配【工具调用】...【/工具调用】格式
        pattern = r'【工具调用】\s*工具:\s*(\w+)\s*参数:\s*(\{[^【]+\})\s*【/工具调用】'
        matches = re.findall(pattern, content, re.DOTALL)
        
        for i, (name, args_str) in enumerate(matches):
            try:
                args = json.loads(args_str.strip())
                tool_calls.append({
                    "id": f"call_{i}",
                    "name": name,
                    "arguments": args
                })
            except:
                pass
        
        # 移除工具调用文本，保留其他内容
        clean_content = re.sub(pattern, '', content, flags=re.DOTALL).strip()
        return clean_content, tool_calls

    # 检测是否是LongCat（不支持function calling）
    is_longcat = 'LongCat' in orig_class_name

    def call_with_tools(self, messages, tools=None, stream=False, **kwargs):
        from config import conf
        from bot.openai_compatible_bot import OpenAICompatibleBot

        api_key, api_base = _get_api_cfg(orig_class_name)
        if api_base.endswith("/"):
            url = api_base + "chat/completions"
        else:
            url = api_base + "/chat/completions"

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + (api_key or ""),
        }

        model = kwargs.get("model") or conf().get("model", "gpt-4o-mini")

        # 消息格式转换（Claude → OpenAI）
        _converter = OpenAICompatibleBot.__new__(OpenAICompatibleBot)
        messages = _converter._convert_messages_to_openai_format(messages)

        # 注入 system prompt
        system_prompt = kwargs.get("system")
        
        # LongCat特殊处理：将tools转换为文本并添加到system prompt
        if is_longcat and tools:
            tools_text = _tools_to_text_prompt(tools)
            if system_prompt:
                system_prompt = system_prompt + tools_text
            else:
                system_prompt = "你是一个AI助手，可以帮助用户使用各种工具。" + tools_text
            logger.debug(f"[AgentBridge/{orig_class_name}] Tools converted to text prompt for LongCat")
        
        if system_prompt:
            if not messages or messages[0].get("role") != "system":
                messages = [{"role": "system", "content": system_prompt}] + messages
            else:
                messages[0] = {"role": "system", "content": system_prompt}

        body = {
            "model":    model,
            "messages": messages,
            "stream":   stream,
            "temperature": kwargs.get("temperature", conf().get("temperature", 0.7)),
        }
        top_p = kwargs.get("top_p", conf().get("top_p"))
        if top_p is not None:
            body["top_p"] = top_p

        # 工具调用格式转换（Claude → OpenAI）
        # LongCat不支持function calling，跳过tools参数
        if tools and not is_longcat:
            openai_tools = _convert_claude_tools_to_openai(tools)
            if openai_tools:
                body["tools"] = openai_tools
                body["tool_choice"] = kwargs.get("tool_choice", "auto")

        if kwargs.get("max_tokens"):
            body["max_tokens"] = kwargs["max_tokens"]

        logger.debug(
            f"[AgentBridge/{orig_class_name}] → {url} model={model} "
            f"msgs={len(messages)} tools={'yes' if tools else 'no'} stream={stream}"
        )

        if stream:
            def _gen():
                try:
                    resp = _requests.post(url, headers=headers,
                                         json=body, stream=True, timeout=120)
                    if not resp.ok:
                        err_body = resp.text[:500]
                        logger.error(
                            f"[AgentBridge/{orig_class_name}] HTTP {resp.status_code}: {err_body}"
                        )
                        yield {"error": True, "message": f"HTTP {resp.status_code}: {err_body}",
                               "status_code": resp.status_code}
                        return
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        line = line.decode("utf-8", errors="replace")
                        if line.startswith("data: "):
                            line = line[6:]
                        if line == "[DONE]":
                            break
                        try:
                            yield json.loads(line)
                        except Exception:
                            pass
                except Exception as e:
                    logger.error(f"[AgentBridge/{orig_class_name}] stream error: {e}")
                    yield {"error": True, "message": str(e), "status_code": 500}
            return _gen()
        else:
            try:
                resp = _requests.post(url, headers=headers, json=body, timeout=120)
                if not resp.ok:
                    err_body = resp.text[:500]
                    logger.error(
                        f"[AgentBridge/{orig_class_name}] HTTP {resp.status_code}: {err_body}"
                    )
                    return {"error": True, "message": f"HTTP {resp.status_code}: {err_body}",
                            "status_code": resp.status_code}
                
                result = resp.json()
                
                # LongCat特殊处理：解析文本格式的tool calls
                if is_longcat and tools:
                    choice = result.get("choices", [{}])[0]
                    message = choice.get("message", {})
                    content = message.get("content", "")
                    
                    clean_content, tool_calls = _parse_text_tool_calls(content)
                    
                    if tool_calls:
                        # 转换为OpenAI function calling格式
                        message["content"] = clean_content if clean_content else None
                        message["tool_calls"] = []
                        for tc in tool_calls:
                            message["tool_calls"].append({
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": json.dumps(tc["arguments"], ensure_ascii=False)
                                }
                            })
                        choice["message"] = message
                        if clean_content:
                            choice["finish_reason"] = "stop"
                        else:
                            choice["finish_reason"] = "tool_calls"
                        result["choices"] = [choice]
                        logger.debug(f"[AgentBridge/{orig_class_name}] Parsed {len(tool_calls)} text tool calls")
                
                # LongCat保底机制：如果返回空响应且带了tools，尝试纯文本模式
                if is_longcat and tools:
                    choice = result.get("choices", [{}])[0]
                    message = choice.get("message", {})
                    content = message.get("content", "")
                    tool_calls_found = message.get("tool_calls", [])
                    
                    if not content and not tool_calls_found:
                        logger.warning(f"[AgentBridge/{orig_class_name}] LongCat returned empty with tools, retrying as plain text...")
                        # 移除tools相关prompt，使用简化的system prompt
                        simple_messages = []
                        for msg in messages:
                            if msg.get("role") == "system":
                                # 使用简化的system prompt，不包含tools描述
                                simple_prompt = "你是一个AI助手，请直接回答用户的问题。"
                                simple_messages.append({"role": "system", "content": simple_prompt})
                            else:
                                simple_messages.append(msg)
                        
                        simple_body = {
                            "model": model,
                            "messages": simple_messages,
                            "stream": False,
                            "temperature": body.get("temperature", 0.7),
                        }
                        if top_p is not None:
                            simple_body["top_p"] = top_p
                        if kwargs.get("max_tokens"):
                            simple_body["max_tokens"] = kwargs["max_tokens"]
                        
                        retry_resp = _requests.post(url, headers=headers, json=simple_body, timeout=120)
                        if retry_resp.ok:
                            retry_result = retry_resp.json()
                            retry_choice = retry_result.get("choices", [{}])[0]
                            retry_message = retry_choice.get("message", {})
                            retry_content = retry_message.get("content", "")
                            
                            if retry_content:
                                logger.info(f"[AgentBridge/{orig_class_name}] Plain text retry successful")
                                # 构造一个模拟的正常响应，标记为纯文本回复
                                result = {
                                    "choices": [{
                                        "message": {
                                            "role": "assistant",
                                            "content": retry_content
                                        },
                                        "finish_reason": "stop"
                                    }]
                                }
                            else:
                                logger.warning(f"[AgentBridge/{orig_class_name}] Plain text retry also returned empty")
                        else:
                            logger.error(f"[AgentBridge/{orig_class_name}] Plain text retry failed: {retry_resp.status_code}")
                
                return result
            except Exception as e:
                logger.error(f"[AgentBridge/{orig_class_name}] sync error: {e}")
                return {"error": True, "message": str(e), "status_code": 500}

    bot_instance.call_with_tools = types.MethodType(call_with_tools, bot_instance)
    logger.info(f"[AgentBridge] Injected requests-based call_with_tools into {orig_class_name}")
    return bot_instance


def add_openai_compatible_support(bot_instance):
    """
    Dynamically add OpenAI-compatible tool calling support to a bot instance.
    
    This allows any bot to gain tool calling capability without modifying its code,
    as long as it uses OpenAI-compatible API format.
    
    Note: Some bots like ZHIPUAIBot have native tool calling support and don't need enhancement.
    Note: ModelScopeBot gets a dedicated requests-based implementation (no openai library).
    """
    if hasattr(bot_instance, 'call_with_tools'):
        # Bot already has tool calling support (e.g., ZHIPUAIBot)
        logger.debug(f"[AgentBridge] {type(bot_instance).__name__} already has native tool calling support")
        return bot_instance

    # Capture the original class name for config selection
    _orig_class_name = bot_instance.__class__.__name__

    # ModelScopeBot: use requests directly (avoids openai library / api_key issues)
    if 'ModelScope' in _orig_class_name:
        return _inject_modelscope_call_with_tools(bot_instance)

    # 所有其他 bot（ChatGPT / 第三方中转 API）：同样用 requests 直连，
    # 避免旧版 openai SDK (ChatCompletion.create) 的兼容性问题：
    #   - 旧 SDK 与新格式端点不兼容
    #   - grok 等模型不接受 frequency_penalty/presence_penalty 参数
    #   - 响应解析出错时 stop_reason=None 被误判为 empty response
    return _inject_openai_compatible_call_with_tools(bot_instance, _orig_class_name)



class AgentLLMModel(LLMModel):
    """
    LLM Model adapter that uses COW's existing bot infrastructure
    """

    _MODEL_BOT_TYPE_MAP = {
        "wenxin": const.BAIDU, "wenxin-4": const.BAIDU,
        "xunfei": const.XUNFEI, const.QWEN: const.QWEN,
        const.MODELSCOPE: const.MODELSCOPE,
        # ModelScope上托管的模型（必须在 minimax startswith 检查之前）
        const.MiniMax_M25: const.MODELSCOPE,
        const.QWEN35_397B: const.MODELSCOPE,
        const.KIMI_K25: const.MODELSCOPE,
        const.DS_V32: const.MODELSCOPE,
        const.GLM_5: const.MODELSCOPE,
        # LongCat 模型
        const.LONGCAT_FLASH_LITE: const.LONGCAT,
        const.LONGCAT_FLASH_CHAT: const.LONGCAT,
        const.LONGCAT_THINKING: const.LONGCAT,
        const.LONGCAT_THINKING_2601: const.LONGCAT,
    }
    _MODEL_PREFIX_MAP = [
        ("qwen", const.QWEN_DASHSCOPE), ("qwq", const.QWEN_DASHSCOPE), ("qvq", const.QWEN_DASHSCOPE),
        ("gemini", const.GEMINI), ("glm", const.ZHIPU_AI), ("claude", const.CLAUDEAPI),
        ("moonshot", const.MOONSHOT), ("kimi", const.MOONSHOT),
        ("doubao", const.DOUBAO),
        ("longcat", const.LONGCAT), ("LongCat", const.LONGCAT),
    ]

    def __init__(self, bridge, bot_type: str = "chat"):
        from config import conf
        super().__init__(model=conf().get("model", const.GPT_41))
        self.bridge = bridge
        self.bot_type = bot_type
        self._bot = None
        self._bot_model = None

    @property
    def model(self):
        from config import conf
        return conf().get("model", const.GPT_41)

    @model.setter
    def model(self, value):
        pass

    def _resolve_bot_type(self, model_name: str) -> str:
        """Resolve bot type from model name, matching Bridge.__init__ logic."""
        from config import conf
        if conf().get("use_linkai", False) and conf().get("linkai_api_key"):
            return const.LINKAI
        if not model_name or not isinstance(model_name, str):
            return const.CHATGPT
        if model_name in self._MODEL_BOT_TYPE_MAP:
            return self._MODEL_BOT_TYPE_MAP[model_name]
        if model_name.lower().startswith("minimax") or model_name in ["abab6.5-chat"]:
            return const.MiniMax
        if model_name in [const.QWEN_35_PLUS, const.QWEN_35_FLASH]:
            return const.QWEN_DASHSCOPE
        if model_name in [const.MOONSHOT, "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"]:
            return const.MOONSHOT
        if model_name in [const.DEEPSEEK_CHAT, const.DEEPSEEK_REASONER]:
            return const.CHATGPT
        # LongCat 模型路由
        if model_name in [const.LONGCAT_FLASH_LITE, const.LONGCAT_FLASH_CHAT, const.LONGCAT_THINKING, const.LONGCAT_THINKING_2601]:
            return const.LONGCAT
        for prefix, btype in self._MODEL_PREFIX_MAP:
            if model_name.startswith(prefix):
                return btype
        return const.CHATGPT

    @property
    def bot(self):
        """Lazy load the bot, re-create when model changes"""
        from bot.bot_factory import create_bot
        cur_model = self.model
        if self._bot is None or self._bot_model != cur_model:
            bot_type = self._resolve_bot_type(cur_model)
            self._bot = create_bot(bot_type)
            self._bot = add_openai_compatible_support(self._bot)
            self._bot_model = cur_model
        return self._bot

    def call(self, request: LLMRequest):
        """
        Call the model using COW's bot infrastructure
        """
        try:
            # For non-streaming calls, we'll use the existing reply method
            # This is a simplified implementation
            if hasattr(self.bot, 'call_with_tools'):
                # Use tool-enabled call if available
                kwargs = {
                    'messages': request.messages,
                    'tools': getattr(request, 'tools', None),
                    'stream': False,
                    'model': self.model  # Pass model parameter
                }
                # Only pass max_tokens if it's explicitly set
                if request.max_tokens is not None:
                    kwargs['max_tokens'] = request.max_tokens
                
                # Extract system prompt if present
                system_prompt = getattr(request, 'system', None)
                if system_prompt:
                    kwargs['system'] = system_prompt
                
                response = self.bot.call_with_tools(**kwargs)
                return self._format_response(response)
            else:
                # Fallback to regular call
                # This would need to be implemented based on your specific needs
                raise NotImplementedError("Regular call not implemented yet")
                
        except Exception as e:
            logger.error(f"AgentLLMModel call error: {e}")
            raise
    
    def call_stream(self, request: LLMRequest):
        """
        Call the model with streaming using COW's bot infrastructure
        """
        try:
            if hasattr(self.bot, 'call_with_tools'):
                # Use tool-enabled streaming call if available
                # Extract system prompt if present
                system_prompt = getattr(request, 'system', None)

                # Build kwargs for call_with_tools
                kwargs = {
                    'messages': request.messages,
                    'tools': getattr(request, 'tools', None),
                    'stream': True,
                    'model': self.model  # Pass model parameter
                }

                # Only pass max_tokens if explicitly set, let the bot use its default
                if request.max_tokens is not None:
                    kwargs['max_tokens'] = request.max_tokens

                # Add system prompt if present
                if system_prompt:
                    kwargs['system'] = system_prompt

                # Pass channel_type for linkai tracking
                channel_type = getattr(self, 'channel_type', None)
                if channel_type:
                    kwargs['channel_type'] = channel_type

                stream = self.bot.call_with_tools(**kwargs)
                
                # Convert stream format to our expected format
                for chunk in stream:
                    yield self._format_stream_chunk(chunk)
            else:
                bot_type = type(self.bot).__name__
                raise NotImplementedError(f"Bot {bot_type} does not support call_with_tools. Please add the method.")
                
        except Exception as e:
            logger.error(f"AgentLLMModel call_stream error: {e}", exc_info=True)
            raise
    
    def _format_response(self, response):
        """Format Claude response to our expected format"""
        # This would need to be implemented based on Claude's response format
        return response
    
    def _format_stream_chunk(self, chunk):
        """Format Claude stream chunk to our expected format"""
        # This would need to be implemented based on Claude's stream format
        return chunk


class AgentBridge:
    """
    Bridge class that integrates super Agent with COW
    Manages multiple agent instances per session for conversation isolation
    """
    
    def __init__(self, bridge):
        self.bridge = bridge
        self.agents = {}  # session_id -> Agent instance mapping
        self.default_agent = None  # For backward compatibility (no session_id)
        self.agent: Optional[Agent] = None
        self.scheduler_initialized = False
        
        # Create helper instances
        self.initializer = AgentInitializer(bridge, self)
    def create_agent(self, system_prompt: str, tools: List = None, **kwargs) -> Agent:
        """
        Create the super agent with COW integration
        
        Args:
            system_prompt: System prompt
            tools: List of tools (optional)
            **kwargs: Additional agent parameters
            
        Returns:
            Agent instance
        """
        # Create LLM model that uses COW's bot infrastructure
        model = AgentLLMModel(self.bridge)
        logger.info(f"[AgentBridge.create_agent] Created model type: {type(model)}, class: {model.__class__.__name__}, mro: {[c.__name__ for c in model.__class__.__mro__]}")
        logger.info(f"[AgentBridge.create_agent] AgentLLMModel.call_stream exists: {hasattr(model, 'call_stream')}, LLMModel.call_stream exists: {hasattr(LLMModel, 'call_stream')}")
        
        # Default tools if none provided
        if tools is None:
            # Use ToolManager to load all available tools
            from agent.tools import ToolManager
            tool_manager = ToolManager()
            tool_manager.load_tools()
            
            tools = []
            for tool_name in tool_manager.tool_classes.keys():
                try:
                    tool = tool_manager.create_tool(tool_name)
                    if tool:
                        tools.append(tool)
                except Exception as e:
                    logger.warning(f"[AgentBridge] Failed to load tool {tool_name}: {e}")
        
        # Create agent instance
        agent = Agent(
            system_prompt=system_prompt,
            description=kwargs.get("description", "AI Super Agent"),
            model=model,
            tools=tools,
            max_steps=kwargs.get("max_steps", 15),
            output_mode=kwargs.get("output_mode", "logger"),
            workspace_dir=kwargs.get("workspace_dir"),  # Pass workspace for skills loading
            enable_skills=kwargs.get("enable_skills", True),  # Enable skills by default
            memory_manager=kwargs.get("memory_manager"),  # Pass memory manager
            max_context_tokens=kwargs.get("max_context_tokens"),
            context_reserve_tokens=kwargs.get("context_reserve_tokens"),
            runtime_info=kwargs.get("runtime_info")  # Pass runtime_info for dynamic time updates
        )

        # Log skill loading details
        if agent.skill_manager:
            logger.debug(f"[AgentBridge] SkillManager initialized with {len(agent.skill_manager.skills)} skills")

        return agent
    
    def get_agent(self, session_id: str = None) -> Optional[Agent]:
        """
        Get agent instance for the given session
        
        Args:
            session_id: Session identifier (e.g., user_id). If None, returns default agent.
        
        Returns:
            Agent instance for this session
        """
        # If no session_id, use default agent (backward compatibility)
        if session_id is None:
            if self.default_agent is None:
                self._init_default_agent()
            return self.default_agent
        
        # Check if agent exists for this session
        if session_id not in self.agents:
            self._init_agent_for_session(session_id)
        
        return self.agents[session_id]
    
    def _init_default_agent(self):
        """Initialize default super agent"""
        agent = self.initializer.initialize_agent(session_id=None)
        self.default_agent = agent
    
    def _init_agent_for_session(self, session_id: str):
        """Initialize agent for a specific session"""
        agent = self.initializer.initialize_agent(session_id=session_id)
        self.agents[session_id] = agent
    
    def agent_reply(self, query: str, context: Context = None, 
                   on_event=None, clear_history: bool = False) -> Reply:
        """
        Use super agent to reply to a query
        
        Args:
            query: User query
            context: COW context (optional, contains session_id for user isolation)
            on_event: Event callback (optional)
            clear_history: Whether to clear conversation history
            
        Returns:
            Reply object
        """
        session_id = None
        agent = None
        try:
            # Extract session_id from context for user isolation
            if context:
                session_id = context.kwargs.get("session_id") or context.get("session_id")
            
            # Get agent for this session (will auto-initialize if needed)
            agent = self.get_agent(session_id=session_id)
            if not agent:
                return Reply(ReplyType.ERROR, "Failed to initialize super agent")
            
            # Create event handler for logging and channel communication
            event_handler = AgentEventHandler(context=context, original_callback=on_event)
            
            # Filter tools based on context
            original_tools = agent.tools
            filtered_tools = original_tools
            
            # If this is a scheduled task execution, exclude scheduler tool to prevent recursion
            if context and context.get("is_scheduled_task"):
                filtered_tools = [tool for tool in agent.tools if tool.name != "scheduler"]
                agent.tools = filtered_tools
                logger.info(f"[AgentBridge] Scheduled task execution: excluded scheduler tool ({len(filtered_tools)}/{len(original_tools)} tools)")
            else:
                # Attach context to scheduler tool if present
                if context and agent.tools:
                    for tool in agent.tools:
                        if tool.name == "scheduler":
                            try:
                                from agent.tools.scheduler.integration import attach_scheduler_to_tool
                                attach_scheduler_to_tool(tool, context)
                            except Exception as e:
                                logger.warning(f"[AgentBridge] Failed to attach context to scheduler: {e}")
                            break
            
            # Pass channel_type to model so linkai requests carry it
            if context and hasattr(agent, 'model'):
                agent.model.channel_type = context.get("channel_type", "")

            # Store session_id on agent so executor can clear DB on fatal errors
            agent._current_session_id = session_id

            # Record message count before execution so we can diff new messages
            with agent.messages_lock:
                pre_run_len = len(agent.messages)

            try:
                # Use agent's run_stream method with event handler
                response = agent.run_stream(
                    user_message=query,
                    on_event=event_handler.handle_event,
                    clear_history=clear_history
                )
            finally:
                # Restore original tools
                if context and context.get("is_scheduled_task"):
                    agent.tools = original_tools

                # Log execution summary
                event_handler.log_summary()

            # Persist new messages generated during this run
            if session_id:
                channel_type = (context.get("channel_type") or "") if context else ""
                with agent.messages_lock:
                    new_messages = agent.messages[pre_run_len:]
                if new_messages:
                    self._persist_messages(session_id, list(new_messages), channel_type)
                elif pre_run_len > 0 and len(agent.messages) == 0:
                    # Agent cleared its messages (recovery from format error / overflow)
                    # Also clear the DB to prevent reloading dirty data
                    try:
                        from agent.memory import get_conversation_store
                        get_conversation_store().clear_session(session_id)
                        logger.info(f"[AgentBridge] Cleared DB for recovered session: {session_id}")
                    except Exception as e:
                        logger.warning(f"[AgentBridge] Failed to clear DB after recovery: {e}")
            
            # Check if there are files to send (from read tool)
            if hasattr(agent, 'stream_executor') and hasattr(agent.stream_executor, 'files_to_send'):
                files_to_send = agent.stream_executor.files_to_send
                if files_to_send:
                    # Send the first file (for now, handle one file at a time)
                    file_info = files_to_send[0]
                    logger.info(f"[AgentBridge] Sending file: {file_info.get('path')}")
                    
                    # Clear files_to_send for next request
                    agent.stream_executor.files_to_send = []
                    
                    # Return file reply based on file type
                    return self._create_file_reply(file_info, response, context)
            
            # ✅ IMAGE_URL: —— Skill 脚本返回的图片 HTTPS 直链
            # 框架将其转为 ReplyType.IMAGE_URL，下游通道（如 wx859）自动下载并发送给用户
            IMAGE_URL_PREFIX = "IMAGE_URL:"
            _iu_idx = response.find(IMAGE_URL_PREFIX) if response and isinstance(response, str) else -1
            if _iu_idx >= 0:
                # 提取 URL，使用正则表达式精确匹配 https://... 
                import re
                _after_prefix = response[_iu_idx + len(IMAGE_URL_PREFIX):]
                # 匹配 URL 模式：http(s)://... 直到空白、换行或转义字符
                _url_match = re.search(r'https?://[^\s\\"\'\n\r]+', _after_prefix)
                if _url_match:
                    img_url = _url_match.group(0)
                    # 去除可能的尾随标点
                    img_url = img_url.rstrip('.,;:!?')
                    
                    if img_url.startswith(("http://", "https://")):
                        logger.info(f"[AgentBridge] IMAGE_URL detected, sending as ReplyType.IMAGE_URL: {img_url}")
                        return Reply(ReplyType.IMAGE_URL, img_url)
                
                logger.warning(f"[AgentBridge] IMAGE_URL prefix found but could not extract valid URL from: {response[_iu_idx:_iu_idx+200]!r}")

            # ✅ MEDIA: —— Skill 脚本返回的本地文件路径（兼容旧版 nano-banana-pro 等）
            MEDIA_PREFIX = "MEDIA:"
            _med_idx = response.find(MEDIA_PREFIX) if response and isinstance(response, str) else -1
            if _med_idx >= 0:
                file_path = response[_med_idx + len(MEDIA_PREFIX):].strip().split()[0]
                if os.path.isfile(file_path):
                    logger.info(f"[AgentBridge] MEDIA detected, sending as ReplyType.IMAGE (local file): {file_path}")
                    return Reply(ReplyType.IMAGE, file_path)
                else:
                    logger.warning(f"[AgentBridge] MEDIA prefix found but file not found: {file_path!r}, falling back to TEXT")

            # ✅ 优先：MUSIC_PLAY: —— LLM 提取的结构化 JSON，由纯代码构建 XML
            # 用 find 而非 startswith：兼容 LLM 在标记前添加了说明文字的情况
            MUSIC_PLAY_PREFIX = "MUSIC_PLAY:"
            _mp_idx = response.find(MUSIC_PLAY_PREFIX) if response and isinstance(response, str) else -1
            if _mp_idx >= 0:
                if _mp_idx > 0:
                    logger.warning(
                        f"[AgentBridge] MUSIC_PLAY: LLM 附加了多余说明文字（将被丢弃）: "
                        f"{response[:_mp_idx].strip()[:80]!r}"
                    )
                json_str = response[_mp_idx + len(MUSIC_PLAY_PREFIX):].strip()
                try:
                    import json as _json
                    data = _json.loads(json_str)
                    xml_content = _build_music_xml_from_data(data)
                    if xml_content:
                        logger.info(
                            f"[AgentBridge] MUSIC_PLAY detected: built XML from JSON data "
                            f"(json_len={len(json_str)}, xml_len={len(xml_content)})"
                        )
                        return Reply(ReplyType.APP, xml_content)
                    else:
                        logger.warning("[AgentBridge] MUSIC_PLAY: JSON 缺少必要字段，降级为 TEXT")
                except Exception as _e:
                    logger.warning(f"[AgentBridge] MUSIC_PLAY: JSON 解析失败 ({_e})，降级为 TEXT")

            # ✅ 兜底：MUSIC_CARD: —— 兼容旧流程或 LLM 幻觉生成的 XML（自动修复格式）
            # 同样用 find 兼容前面有说明文字的情况
            MUSIC_CARD_PREFIX = "MUSIC_CARD:"
            _mc_idx = response.find(MUSIC_CARD_PREFIX) if response and isinstance(response, str) else -1
            if _mc_idx >= 0:
                if _mc_idx > 0:
                    logger.warning(
                        f"[AgentBridge] MUSIC_CARD: LLM 附加了多余说明文字（将被丢弃）: "
                        f"{response[:_mc_idx].strip()[:80]!r}"
                    )
                xml_content = response[_mc_idx + len(MUSIC_CARD_PREFIX):].strip()
                if xml_content:
                    xml_content = _repair_music_xml(xml_content)
                    logger.info(f"[AgentBridge] Detected MUSIC_CARD reply, sending as ReplyType.APP (xml length={len(xml_content)})")
                    return Reply(ReplyType.APP, xml_content)
                else:
                    logger.warning("[AgentBridge] MUSIC_CARD prefix found but XML content is empty, falling back to TEXT")

            # ✅ INJECT_COMMAND: —— Skill 脚本请求将命令重新注入处理流程（用于触发插件）
            # 格式: INJECT_COMMAND:<command>|session_id=<sid>|user_id=<uid>
            # 示例: INJECT_COMMAND:gr画图 一只猫咪|session_id=abc123|user_id=wxid_xxx
            INJECT_CMD_PREFIX = "INJECT_COMMAND:"
            _ic_idx = response.find(INJECT_CMD_PREFIX) if response and isinstance(response, str) else -1
            if _ic_idx >= 0:
                inject_content = response[_ic_idx + len(INJECT_CMD_PREFIX):].strip()
                if inject_content:
                    try:
                        logger.info(f"[AgentBridge] Detected INJECT_COMMAND request: {inject_content[:100]}...")
                        
                        # 解析注入的命令和参数
                        parts = inject_content.split("|")
                        command = parts[0].strip() if parts else ""
                        
                        # 提取 session_id 和 user_id
                        inject_session_id = session_id  # 默认使用当前 session
                        inject_user_id = None
                        
                        for part in parts[1:]:
                            if "=" in part:
                                key, value = part.split("=", 1)
                                key = key.strip()
                                value = value.strip()
                                if key == "session_id":
                                    inject_session_id = value
                                elif key == "user_id":
                                    inject_user_id = value
                        
                        # 从当前 context 获取 user_id（如果没有提供）
                        if not inject_user_id and context:
                            inject_user_id = context.get("from_user_id") or context.get("session_id")
                        
                        logger.info(f"[AgentBridge] Injecting command '{command}' for session={inject_session_id}, user={inject_user_id}")
                        
                        # 调用插件管理器触发插件
                        plugin_reply = self._handle_injected_command(
                            command=command,
                            session_id=inject_session_id,
                            user_id=inject_user_id,
                            original_context=context
                        )
                        
                        if plugin_reply:
                            logger.info(f"[AgentBridge] Plugin handled injected command, returning plugin reply")
                            return plugin_reply
                        else:
                            # 如果插件没有处理，返回提示信息
                            fallback_msg = f"⚠️ 命令已转换: {command}\n\n但插件未处理该命令，请手动发送上述命令。"
                            return Reply(ReplyType.TEXT, fallback_msg)
                            
                    except Exception as e:
                        logger.error(f"[AgentBridge] Failed to handle INJECT_COMMAND: {e}")
                        logger.exception(e)
                        error_msg = f"⚠️ 命令转换成功，但自动执行失败: {e}\n\n请手动发送: {inject_content.split('|')[0] if '|' in inject_content else inject_content}"
                        return Reply(ReplyType.TEXT, error_msg)

            return Reply(ReplyType.TEXT, response)

            
        except Exception as e:
            logger.error(f"Agent reply error: {e}")
            # If the agent cleared its messages due to format error / overflow,
            # also purge the DB so the next request starts clean.
            if session_id and agent:
                try:
                    with agent.messages_lock:
                        msg_count = len(agent.messages)
                    if msg_count == 0:
                        from agent.memory import get_conversation_store
                        get_conversation_store().clear_session(session_id)
                        logger.info(f"[AgentBridge] Cleared DB for session after error: {session_id}")
                except Exception as db_err:
                    logger.warning(f"[AgentBridge] Failed to clear DB after error: {db_err}")
            return Reply(ReplyType.ERROR, f"Agent error: {str(e)}")

    def _handle_injected_command(self, command: str, session_id: str, user_id: str, original_context: Context = None) -> Reply:
        """
        处理注入的命令，触发插件执行
        
        此方法允许 Skill 脚本通过 INJECT_COMMAND: 格式请求将命令重新注入消息处理流程，
        从而触发相应的插件（如 GrokMedia 插件）。
        
        Args:
            command: 要注入的命令（如 "gr画图 一只猫咪"）
            session_id: 会话ID
            user_id: 用户ID
            original_context: 原始上下文（可选）
            
        Returns:
            Reply 对象（插件处理结果），如果插件未处理则返回 None
        """
        try:
            from plugins import PluginManager, Event, EventAction
            from plugins.event import EventContext
            from bridge.context import ContextType
            from channel import channel_factory
            from config import conf
            
            logger.info(f"[_handle_injected_command] Processing command: {command}")
            
            # 创建新的 Context 对象
            inject_context = Context()
            inject_context.type = ContextType.TEXT
            inject_context.content = command
            
            # 复制必要的上下文信息
            if original_context:
                # 复制关键字段
                for key in ['session_id', 'from_user_id', 'channel_type', 'channel_name', 'isgroup']:
                    if key in original_context:
                        inject_context[key] = original_context[key]
            
            # 确保 session_id 和 from_user_id 被设置
            if session_id:
                inject_context['session_id'] = session_id
            if user_id:
                inject_context['from_user_id'] = user_id
            
            logger.debug(f"[_handle_injected_command] Created inject context: {inject_context}")
            
            # 获取或创建 channel 对象（某些插件需要 channel 来发送消息）
            channel = None
            try:
                channel_type = conf().get("channel_type")
                if channel_type:
                    channel = channel_factory.create_channel(channel_type)
                    logger.debug(f"[_handle_injected_command] Created channel: {channel_type}")
            except Exception as e:
                logger.warning(f"[_handle_injected_command] Failed to create channel: {e}")
            
            # 获取 PluginManager 实例
            plugin_manager = PluginManager()
            
            # 创建事件上下文（包含 channel，以便插件使用）
            event_context = {"context": inject_context}
            if channel:
                event_context["channel"] = channel
            
            e_context = EventContext(Event.ON_HANDLE_CONTEXT, event_context)
            
            # 触发插件事件
            logger.info(f"[_handle_injected_command] Emitting ON_HANDLE_CONTEXT event for command: {command}")
            plugin_manager.emit_event(e_context)
            
            # 获取插件返回的 reply（先检查是否有回复）
            reply = e_context["reply"] if "reply" in e_context.econtext else None
            if reply:
                logger.info(f"[_handle_injected_command] Plugin returned reply: type={reply.type}, content={reply.content[:100] if reply.content else 'None'}...")
                # 如果是错误回复，记录警告
                if reply.type == ReplyType.ERROR:
                    logger.warning(f"[_handle_injected_command] Plugin returned ERROR reply: {reply.content}")
                return reply
            
            # 如果 action 是 BREAK 或 BREAK_PASS，表示插件已处理但可能没有返回 reply
            if e_context.action in [EventAction.BREAK, EventAction.BREAK_PASS]:
                logger.info(f"[_handle_injected_command] Plugin processed command (action={e_context.action}) but no reply returned")
                # 返回一个空的成功回复
                return Reply(ReplyType.TEXT, "✅ 命令已执行")
            
            # 检查插件是否没有处理该命令（action仍然是CONTINUE）
            if e_context.action == EventAction.CONTINUE:
                logger.warning(f"[_handle_injected_command] No plugin handled the command: {command}")
                return None
            
            logger.warning(f"[_handle_injected_command] Command not processed by any plugin")
            return None
            
        except Exception as e:
            logger.error(f"[_handle_injected_command] Error handling injected command: {e}")
            logger.exception(e)
            return Reply(ReplyType.ERROR, f"执行命令时出错: {e}")
    
    def _create_file_reply(self, file_info: dict, text_response: str, context: Context = None) -> Reply:
        """
        Create a reply for sending files
        
        Args:
            file_info: File metadata from read tool
            text_response: Text response from agent
            context: Context object
            
        Returns:
            Reply object for file sending
        """
        file_type = file_info.get("file_type", "file")
        file_path = file_info.get("path")
        
        # For images, use IMAGE_URL type (channel will handle upload)
        if file_type == "image":
            # Convert local path to file:// URL for channel processing
            file_url = f"file://{file_path}"
            logger.info(f"[AgentBridge] Sending image: {file_url}")
            reply = Reply(ReplyType.IMAGE_URL, file_url)
            # Attach text message if present (for channels that support text+image)
            if text_response:
                reply.text_content = text_response  # Store accompanying text
            return reply
        
        # For all file types (document, video, audio), use FILE type
        if file_type in ["document", "video", "audio"]:
            file_url = f"file://{file_path}"
            logger.info(f"[AgentBridge] Sending {file_type}: {file_url}")
            reply = Reply(ReplyType.FILE, file_url)
            reply.file_name = file_info.get("file_name", os.path.basename(file_path))
            # Attach text message if present
            if text_response:
                reply.text_content = text_response
            return reply
        
        # For other unknown file types, return text with file info
        message = text_response or file_info.get("message", "文件已准备")
        message += f"\n\n[文件: {file_info.get('file_name', file_path)}]"
        return Reply(ReplyType.TEXT, message)
    
    def _migrate_config_to_env(self, workspace_root: str):
        """
        Migrate API keys from config.json to .env file if not already set
        
        Args:
            workspace_root: Workspace directory path (not used, kept for compatibility)
        """
        from config import conf
        import os
        
        # Mapping from config.json keys to environment variable names
        key_mapping = {
            "open_ai_api_key": "OPENAI_API_KEY",
            "open_ai_api_base": "OPENAI_API_BASE",
            "gemini_api_key": "GEMINI_API_KEY",
            "claude_api_key": "CLAUDE_API_KEY",
            "linkai_api_key": "LINKAI_API_KEY",
        }
        
        # Use fixed secure location for .env file
        env_file = expand_path("~/.cow/.env")
        
        # Read existing env vars from .env file
        existing_env_vars = {}
        if os.path.exists(env_file):
            try:
                with open(env_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, _ = line.split('=', 1)
                            existing_env_vars[key.strip()] = True
            except Exception as e:
                logger.warning(f"[AgentBridge] Failed to read .env file: {e}")
        
        # Check which keys need to be migrated
        keys_to_migrate = {}
        for config_key, env_key in key_mapping.items():
            # Skip if already in .env file
            if env_key in existing_env_vars:
                continue
            
            # Get value from config.json
            value = conf().get(config_key, "")
            if value and value.strip():  # Only migrate non-empty values
                keys_to_migrate[env_key] = value.strip()
        
        # Log summary if there are keys to skip
        if existing_env_vars:
            logger.debug(f"[AgentBridge] {len(existing_env_vars)} env vars already in .env")
        
        # Write new keys to .env file
        if keys_to_migrate:
            try:
                # Ensure ~/.cow directory and .env file exist
                env_dir = os.path.dirname(env_file)
                if not os.path.exists(env_dir):
                    os.makedirs(env_dir, exist_ok=True)
                if not os.path.exists(env_file):
                    open(env_file, 'a').close()
                
                # Append new keys
                with open(env_file, 'a', encoding='utf-8') as f:
                    f.write('\n# Auto-migrated from config.json\n')
                    for key, value in keys_to_migrate.items():
                        f.write(f'{key}={value}\n')
                        # Also set in current process
                        os.environ[key] = value
                
                logger.info(f"[AgentBridge] Migrated {len(keys_to_migrate)} API keys from config.json to .env: {list(keys_to_migrate.keys())}")
            except Exception as e:
                logger.warning(f"[AgentBridge] Failed to migrate API keys: {e}")
    
    def _persist_messages(
        self, session_id: str, new_messages: list, channel_type: str = ""
    ) -> None:
        """
        Persist new messages to the conversation store after each agent run.

        Failures are logged but never propagate — they must not interrupt replies.
        """
        if not new_messages:
            return
        try:
            from config import conf
            if not conf().get("conversation_persistence", True):
                return
        except Exception:
            pass
        try:
            from agent.memory import get_conversation_store
            get_conversation_store().append_messages(
                session_id, new_messages, channel_type=channel_type
            )
        except Exception as e:
            logger.warning(
                f"[AgentBridge] Failed to persist messages for session={session_id}: {e}"
            )

    def clear_session(self, session_id: str):
        """
        Clear a specific session's agent and conversation history
        
        Args:
            session_id: Session identifier to clear
        """
        if session_id in self.agents:
            logger.info(f"[AgentBridge] Clearing session: {session_id}")
            del self.agents[session_id]
    
    def clear_all_sessions(self):
        """Clear all agent sessions"""
        logger.info(f"[AgentBridge] Clearing all sessions ({len(self.agents)} total)")
        self.agents.clear()
        self.default_agent = None
    
    def refresh_all_skills(self) -> int:
        """
        Refresh skills and conditional tools in all agent instances after
        environment variable changes. This allows hot-reload without restarting.

        Returns:
            Number of agent instances refreshed
        """
        import os
        from dotenv import load_dotenv
        from config import conf

        # Reload environment variables from .env file
        workspace_root = expand_path(conf().get("agent_workspace", "~/cow"))
        env_file = os.path.join(workspace_root, '.env')

        if os.path.exists(env_file):
            load_dotenv(env_file, override=True)
            logger.info(f"[AgentBridge] Reloaded environment variables from {env_file}")

        refreshed_count = 0

        # Collect all agent instances to refresh
        agents_to_refresh = []
        if self.default_agent:
            agents_to_refresh.append(("default", self.default_agent))
        for session_id, agent in self.agents.items():
            agents_to_refresh.append((session_id, agent))

        for label, agent in agents_to_refresh:
            # Refresh skills
            if hasattr(agent, 'skill_manager') and agent.skill_manager:
                agent.skill_manager.refresh_skills()

            # Refresh conditional tools (e.g. web_search depends on API keys)
            self._refresh_conditional_tools(agent)

            refreshed_count += 1

        if refreshed_count > 0:
            logger.info(f"[AgentBridge] Refreshed skills & tools in {refreshed_count} agent instance(s)")

        return refreshed_count

    @staticmethod
    def _refresh_conditional_tools(agent):
        """
        Add or remove conditional tools based on current environment variables.
        For example, web_search should only be present when BOCHA_API_KEY or
        LINKAI_API_KEY is set.
        """
        try:
            from agent.tools.web_search.web_search import WebSearch

            has_tool = any(t.name == "web_search" for t in agent.tools)
            available = WebSearch.is_available()

            if available and not has_tool:
                # API key was added - inject the tool
                tool = WebSearch()
                tool.model = agent.model
                agent.tools.append(tool)
                logger.info("[AgentBridge] web_search tool added (API key now available)")
            elif not available and has_tool:
                # API key was removed - remove the tool
                agent.tools = [t for t in agent.tools if t.name != "web_search"]
                logger.info("[AgentBridge] web_search tool removed (API key no longer available)")
        except Exception as e:
            logger.debug(f"[AgentBridge] Failed to refresh conditional tools: {e}")
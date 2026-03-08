"""
channel factory
"""
from common import const
from config import conf


def create_bot(bot_type):
    """
    create a bot_type instance
    :param bot_type: bot type code
    :return: bot instance
    """
    # 获取当前配置的模型
    model = conf().get("model")
    
    # 如果模型是 qianfan，使用 QianfanBot
    if model == "qianfan" or bot_type == const.QIANFAN:
        from bot.qianfan.qianfan_bot import QianfanBot
        return QianfanBot()
        
    # 如果模型是 SiliconFlow 系列模型，使用 SiliconFlowBot
    if model in [
        const.SF_DEEPSEEK_V32,
        const.SF_QWEN3,
        const.SF_KIMI_K2,
        const.SF_GLM_46,
        const.SF_STEP35,
        const.SF_LING_FLASH_2,
        const.SF_MiniMax_M21
    ]:
        from bot.siliconflow.siliconflow_bot import SiliconFlowBot
        return SiliconFlowBot()
    
    # 如果模型是 LongCat 系列模型，使用 LongCatBot
    if model in [
        const.LONGCAT_FLASH_LITE,
        const.LONGCAT_FLASH_CHAT,
        const.LONGCAT_THINKING,
        const.LONGCAT_THINKING_2601
    ]:
        from bot.longcat.longcat_bot import LongCatBot
        return LongCatBot()    

    # 如果模型是 DeepSeek 系列模型，使用 DeepSeekBot
    if model in [
        const.DEEPSEEK_CHAT,
        const.DEEPSEEK_REASONER
    ]:
        from bot.deepseek.deepseek_bot import DeepSeekBot
        return DeepSeekBot()
        
    # 如果模型是 ZhipuAI 系列模型，使用 ZhipuAIBot
    if model in [
        const.GLM_47_FLASH,
        const.GLM_45_FLASH,
        const.GLM_46V_FLASH,
        const.GLM_4V_FLASH,
        const.GLM_41V_FLASH
    ]:
        from bot.zhipuai.zhipuai_bot import ZhipuAIBot
        return ZhipuAIBot()

    # 如果模型是 Dashscope 系列模型，使用 DashscopeBot
    if model in [
        const.QWEN_35_PLUS,
        const.QWEN_35_PLUS_2602,
        const.QWEN_35_FLASH,
        const.QWEN_35_FLASH_2602,
        const.QWEN_35_397B,
        const.QWEN_35_27B,
        const.QWEN_DS,
        const.QWEN_GLM,
        const.QWEN_K25,
        const.QWEN_M25
    ]:
        from bot.dashscope.dashscope_bot import DashscopeBot
        return DashscopeBot()

    # 如果模型是 Dify 系列模型，或者 bot_type 是 dify，使用 DifyBot
    if model in [
        const.DIFY_CHATBOT,
        const.DIFY_AGENT,
        const.DIFY_CHATFLOW,
        const.DIFY_WORKFLOW
    ] or bot_type == const.DIFY:
        from bot.dify.dify_bot import DifyBot
        return DifyBot()

    # 如果模型是 OpenAI 系列模型，使用 OpenAIBot
    if model in [
        const.GPT_51,
        const.GPT_52,
        const.GPT_54,
        const.GPT_OSS_120B
    ]:
        from bot.openai.open_ai_bot import OpenAIBot
        return OpenAIBot()

    # Grok 模型 → OpenAIBot（兼容 OpenAI API）
    if model in [
        const.GROK_41_FAST,
        const.GROK_41,
        const.GROK_42
    ]:
        from bot.openai.open_ai_bot import OpenAIBot
        return OpenAIBot()

    # Gemini 模型 → GoogleGeminiBot
#   if model in [
#       const.GEMINI_15_FLASH,
#       const.GEMINI_15_PRO,
#       const.GEMINI_20_FLASH_EXP
#   ] or (model and model.startswith("gemini")):
#       from bot.gemini.google_gemini_bot import GoogleGeminiBot
#       return GoogleGeminiBot()

    # 其他模型的处理逻辑
    if bot_type == const.BAIDU:
        from bot.baidu.baidu_wenxin import BaiduWenxinBot
        return BaiduWenxinBot()

    elif bot_type == const.CHATGPT:
        # ChatGPT 网页端web接口
        from bot.chatgpt.chat_gpt_bot import ChatGPTBot
        return ChatGPTBot()

    elif bot_type == const.CHATGPTONAZURE:
        # Azure chatgpt service
        from bot.chatgpt.chat_gpt_bot import AzureChatGPTBot
        return AzureChatGPTBot()

    elif bot_type == const.XUNFEI:
        from bot.xunfei.xunfei_spark_bot import XunFeiBot
        return XunFeiBot()

    elif bot_type == const.LINKAI:
        from bot.linkai.link_ai_bot import LinkAIBot
        return LinkAIBot()

    elif bot_type == const.CLAUDEAI:
        from bot.claude.claude_ai_bot import ClaudeAIBot
        return ClaudeAIBot()

    elif bot_type == const.CLAUDEAPI:
        from bot.claude.claude_ai_bot import ClaudeAPIBot
        return ClaudeAPIBot()

    elif bot_type == const.QWEN:
        from bot.ali.ali_qwen_bot import AliQwenBot
        return AliQwenBot()
        
    elif bot_type == const.MOONSHOT:
        from bot.moonshot.moonshot_bot import MoonshotBot
        return MoonshotBot()

    elif bot_type == const.MODELSCOPE:
        from bot.modelscope.modelscope_bot import ModelScopeBot
        return ModelScopeBot()

    elif bot_type == const.GEMINI:
        from bot.gemini.google_gemini_bot import GoogleGeminiBot
        return GoogleGeminiBot()        

    elif bot_type == const.COZE:
        from bot.bytedance.bytedance_coze_bot import ByteDanceCozeBot
        return ByteDanceCozeBot()

    raise RuntimeError

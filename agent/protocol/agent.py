import json
import os
import time
import threading

from common.log import logger
from agent.protocol.models import LLMRequest, LLMModel
from agent.protocol.agent_stream import AgentStreamExecutor
from agent.protocol.result import AgentAction, AgentActionType, ToolResult, AgentResult
from agent.tools.base_tool import BaseTool, ToolStage


class Agent:
    def __init__(self, system_prompt: str, description: str = "AI Agent", model: LLMModel = None,
                 tools=None, output_mode="print", max_steps=100, max_context_tokens=None, 
                 context_reserve_tokens=None, memory_manager=None, name: str = None,
                 workspace_dir: str = None, skill_manager=None, enable_skills: bool = True,
                 runtime_info: dict = None):
        """
        Initialize the Agent with system prompt, model, description.

        :param system_prompt: The system prompt for the agent.
        :param description: A description of the agent.
        :param model: An instance of LLMModel to be used by the agent.
        :param tools: Optional list of tools for the agent to use.
        :param output_mode: Control how execution progress is displayed: 
                           "print" for console output or "logger" for using logger
        :param max_steps: Maximum number of steps the agent can take (default: 100)
        :param max_context_tokens: Maximum tokens to keep in context (default: None, auto-calculated based on model)
        :param context_reserve_tokens: Reserve tokens for new requests (default: None, auto-calculated)
        :param memory_manager: Optional MemoryManager instance for memory operations
        :param name: [Deprecated] The name of the agent (no longer used in single-agent system)
        :param workspace_dir: Optional workspace directory for workspace-specific skills
        :param skill_manager: Optional SkillManager instance (will be created if None and enable_skills=True)
        :param enable_skills: Whether to enable skills support (default: True)
        :param runtime_info: Optional runtime info dict (with _get_current_time callable for dynamic time)
        """
        self.name = name or "Agent"
        self.system_prompt = system_prompt
        self.model = model  # Instance of LLMModel - removed type hint
        # Debug: log model type
        logger.info(f"[Agent] model type: {type(model)}, model class: {model.__class__.__name__}")
        logger.info(f"[Agent] model mro: {[c.__name__ for c in model.__class__.__mro__]}")
        logger.info(f"[Agent] model has call_stream: {hasattr(model, 'call_stream')}")
        # Check if call_stream is the base implementation
        if hasattr(model, 'call_stream'):
            import inspect
            call_stream_method = getattr(model, 'call_stream')
            logger.info(f"[Agent] call_stream is method: {inspect.ismethod(call_stream_method) or inspect.isfunction(call_stream_method)}")
        self.description = description
        self.tools: list = []
        self.max_steps = max_steps  # max tool-call steps, default 100
        self.max_context_tokens = max_context_tokens  # max tokens in context
        self.context_reserve_tokens = context_reserve_tokens  # reserve tokens for new requests
        self.captured_actions = []  # Initialize captured actions list
        self.output_mode = output_mode
        self.last_usage = None  # Store last API response usage info
        self.messages = []  # Unified message history for stream mode
        self.messages_lock = threading.Lock()  # Lock for thread-safe message operations
        self.memory_manager = memory_manager  # Memory manager for auto memory flush
        self.workspace_dir = workspace_dir  # Workspace directory
        self.enable_skills = enable_skills  # Skills enabled flag
        self.runtime_info = runtime_info  # Runtime info for dynamic time update
        
        # Initialize skill manager
        self.skill_manager = None
        if enable_skills:
            if skill_manager:
                self.skill_manager = skill_manager
            else:
                # Auto-create skill manager
                try:
                    from agent.skills import SkillManager
                    custom_dir = os.path.join(workspace_dir, "skills") if workspace_dir else None
                    self.skill_manager = SkillManager(custom_dir=custom_dir)
                    logger.debug(f"Initialized SkillManager with {len(self.skill_manager.skills)} skills")
                except Exception as e:
                    logger.warning(f"Failed to initialize SkillManager: {e}")
        
        if tools:
            for tool in tools:
                self.add_tool(tool)

    def add_tool(self, tool: BaseTool):
        """
        Add a tool to the agent.

        :param tool: The tool to add (either a tool instance or a tool name)
        """
        # If tool is already an instance, use it directly
        tool.model = self.model
        self.tools.append(tool)

    def get_skills_prompt(self, skill_filter=None) -> str:
        """
        Get the skills prompt to append to system prompt.
        
        :param skill_filter: Optional list of skill names to include
        :return: Formatted skills prompt or empty string
        """
        if not self.skill_manager:
            return ""
        
        try:
            return self.skill_manager.build_skills_prompt(skill_filter=skill_filter)
        except Exception as e:
            logger.warning(f"Failed to build skills prompt: {e}")
            return ""
    
    def get_full_system_prompt(self, skill_filter=None) -> str:
        """
        Get the full system prompt including skills.

        Note: Skills are now built into the system prompt by PromptBuilder,
        so we just return the base prompt directly. This method is kept for
        backward compatibility.

        :param skill_filter: Optional list of skill names to include (deprecated)
        :return: Complete system prompt
        """
        prompt = self.system_prompt

        # Rebuild tool list section to reflect current self.tools
        prompt = self._rebuild_tool_list_section(prompt)

        # If runtime_info contains dynamic time function, rebuild runtime section
        if self.runtime_info and callable(self.runtime_info.get('_get_current_time')):
            prompt = self._rebuild_runtime_section(prompt)

        return prompt
    
    def _rebuild_runtime_section(self, prompt: str) -> str:
        """
        Rebuild runtime info section with current time.
        
        This method dynamically updates the runtime info section by calling
        the _get_current_time function from runtime_info.
        
        :param prompt: Original system prompt
        :return: Updated system prompt with current runtime info
        """
        try:
            # Get current time dynamically
            time_info = self.runtime_info['_get_current_time']()
            
            # Build new runtime section
            runtime_lines = [
                "\n## 运行时信息\n",
                "\n",
                f"当前时间: {time_info['time']} {time_info['weekday']} ({time_info['timezone']})\n",
                "\n"
            ]
            
            # Add other runtime info
            runtime_parts = []
            if self.runtime_info.get("model"):
                runtime_parts.append(f"模型={self.runtime_info['model']}")
            if self.runtime_info.get("workspace"):
                # Replace backslashes with forward slashes for Windows paths
                workspace_path = str(self.runtime_info['workspace']).replace('\\', '/')
                runtime_parts.append(f"工作空间={workspace_path}")
            if self.runtime_info.get("channel") and self.runtime_info.get("channel") != "web":
                runtime_parts.append(f"渠道={self.runtime_info['channel']}")
            
            if runtime_parts:
                runtime_lines.append("运行时: " + " | ".join(runtime_parts) + "\n")
                runtime_lines.append("\n")
            
            new_runtime_section = "".join(runtime_lines)
            
            # Find and replace the runtime section
            import re
            pattern = r'\n## 运行时信息\s*\n.*?(?=\n##|\Z)'
            updated_prompt = re.sub(pattern, new_runtime_section.rstrip('\n'), prompt, flags=re.DOTALL)
            
            return updated_prompt
        except Exception as e:
            logger.warning(f"Failed to rebuild runtime section: {e}")
            return prompt

    def _rebuild_tool_list_section(self, prompt: str) -> str:
        """
        Rebuild the tool list inside the '## 工具系统' section so that it
        always reflects the current ``self.tools`` (handles dynamic add/remove
        of conditional tools like web_search).
        """
        import re
        from agent.prompt.builder import _build_tooling_section

        try:
            if not self.tools:
                return prompt

            new_lines = _build_tooling_section(self.tools, "zh")
            new_section = "\n".join(new_lines).rstrip("\n")

            # Replace existing tooling section
            pattern = r'## 工具系统\s*\n.*?(?=\n## |\Z)'
            updated = re.sub(pattern, new_section, prompt, count=1, flags=re.DOTALL)
            return updated
        except Exception as e:
            logger.warning(f"Failed to rebuild tool list section: {e}")
            return prompt

    def refresh_skills(self):
        """Refresh the loaded skills."""
        if self.skill_manager:
            self.skill_manager.refresh_skills()
            logger.info(f"Refreshed skills: {len(self.skill_manager.skills)} skills loaded")
    
    def list_skills(self):
        """
        List all loaded skills.
        
        :return: List of skill entries or empty list
        """
        if not self.skill_manager:
            return []
        return self.skill_manager.list_skills()

    def _get_model_context_window(self) -> int:
        """
        Get the model's context window size in tokens.
        Auto-detect based on model name.
        
        Model context windows:
        - Claude 3.5/3.7 Sonnet: 200K tokens
        - Claude 3 Opus: 200K tokens
        - GPT-4 Turbo/128K: 128K tokens
        - GPT-4: 8K-32K tokens
        - GPT-3.5: 16K tokens
        - DeepSeek: 64K tokens
        
        :return: Context window size in tokens
        """
        if self.model and hasattr(self.model, 'model'):
            model_name = self.model.model.lower()

            # Claude models - 200K context
            if 'claude-3' in model_name or 'claude-sonnet' in model_name:
                return 200000

            # GPT-4 models
            elif 'gpt-4' in model_name:
                if 'turbo' in model_name or '128k' in model_name:
                    return 128000
                elif '32k' in model_name:
                    return 32000
                else:
                    return 8000

            # GPT-3.5
            elif 'gpt-3.5' in model_name:
                if '16k' in model_name:
                    return 16000
                else:
                    return 4000

            # DeepSeek
            elif 'deepseek' in model_name:
                return 64000
            
            # Gemini models
            elif 'gemini' in model_name:
                if '2.0' in model_name or 'exp' in model_name:
                    return 2000000  # Gemini 2.0: 2M tokens
                else:
                    return 1000000  # Gemini 1.5: 1M tokens

        # Default conservative value
        return 128000

    def _get_context_reserve_tokens(self) -> int:
        """
        Get the number of tokens to reserve for new requests.
        This prevents context overflow by keeping a buffer.
        
        :return: Number of tokens to reserve
        """
        if self.context_reserve_tokens is not None:
            return self.context_reserve_tokens

        # Reserve ~10% of context window, with min 10K and max 200K
        context_window = self._get_model_context_window()
        reserve = int(context_window * 0.1)
        return max(10000, min(200000, reserve))

    def _estimate_message_tokens(self, message: dict) -> int:
        """
        Estimate token count for a message.

        Uses chars/3 for Chinese-heavy content and chars/4 for ASCII-heavy content,
        plus per-block overhead for tool_use / tool_result structures.

        :param message: Message dict with 'role' and 'content'
        :return: Estimated token count
        """
        content = message.get('content', '')
        if isinstance(content, str):
            return max(1, self._estimate_text_tokens(content))
        elif isinstance(content, list):
            total_tokens = 0
            for part in content:
                if not isinstance(part, dict):
                    continue
                block_type = part.get('type', '')
                if block_type == 'text':
                    total_tokens += self._estimate_text_tokens(part.get('text', ''))
                elif block_type == 'image':
                    total_tokens += 1200
                elif block_type == 'tool_use':
                    # tool_use has id + name + input (JSON-encoded)
                    total_tokens += 50  # overhead for structure
                    input_data = part.get('input', {})
                    if isinstance(input_data, dict):
                        import json
                        input_str = json.dumps(input_data, ensure_ascii=False)
                        total_tokens += self._estimate_text_tokens(input_str)
                elif block_type == 'tool_result':
                    # tool_result has tool_use_id + content
                    total_tokens += 30  # overhead for structure
                    result_content = part.get('content', '')
                    if isinstance(result_content, str):
                        total_tokens += self._estimate_text_tokens(result_content)
                else:
                    # Unknown block type, estimate conservatively
                    total_tokens += 10
            return max(1, total_tokens)
        return 1

    @staticmethod
    def _estimate_text_tokens(text: str) -> int:
        """
        Estimate token count for a text string.

        Chinese / CJK characters typically use ~1.5 tokens each,
        while ASCII uses ~0.25 tokens per char (4 chars/token).
        We use a weighted average based on the character mix.

        :param text: Input text
        :return: Estimated token count
        """
        if not text:
            return 0
        # Count non-ASCII characters (CJK, emoji, etc.)
        non_ascii = sum(1 for c in text if ord(c) > 127)
        ascii_count = len(text) - non_ascii
        # CJK chars: ~1.5 tokens each; ASCII: ~0.25 tokens per char
        return int(non_ascii * 1.5 + ascii_count * 0.25) + 1

    def _find_tool(self, tool_name: str):
        """Find and return a tool with the specified name"""
        for tool in self.tools:
            if tool.name == tool_name:
                # Only pre-process stage tools can be actively called
                if tool.stage == ToolStage.PRE_PROCESS:
                    tool.model = self.model
                    tool.context = self  # Set tool context
                    return tool
                else:
                    # If it's a post-process tool, return None to prevent direct calling
                    logger.warning(f"Tool {tool_name} is a post-process tool and cannot be called directly.")
                    return None
        return None

    # output function based on mode
    def output(self, message="", end="\n"):
        if self.output_mode == "print":
            print(message, end=end)
        elif message:
            logger.info(message)

    def _execute_post_process_tools(self):
        """Execute all post-process stage tools"""
        # Get all post-process stage tools
        post_process_tools = [tool for tool in self.tools if tool.stage == ToolStage.POST_PROCESS]

        # Execute each tool
        for tool in post_process_tools:
            # Set tool context
            tool.context = self

            # Record start time for execution timing
            start_time = time.time()

            # Execute tool (with empty parameters, tool will extract needed info from context)
            result = tool.execute({})

            # Calculate execution time
            execution_time = time.time() - start_time

            # Capture tool use for tracking
            self.capture_tool_use(
                tool_name=tool.name,
                input_params={},  # Post-process tools typically don't take parameters
                output=result.result,
                status=result.status,
                error_message=str(result.result) if result.status == "error" else None,
                execution_time=execution_time
            )

            # Log result
            if result.status == "success":
                # Print tool execution result in the desired format
                self.output(f"\n🛠️ {tool.name}: {json.dumps(result.result)}")
            else:
                # Print failure in print mode
                self.output(f"\n🛠️ {tool.name}: {json.dumps({'status': 'error', 'message': str(result.result)})}")

    def capture_tool_use(self, tool_name, input_params, output, status, thought=None, error_message=None,
                         execution_time=0.0):
        """
        Capture a tool use action.
        
        :param thought: thought content
        :param tool_name: Name of the tool used
        :param input_params: Parameters passed to the tool
        :param output: Output from the tool
        :param status: Status of the tool execution
        :param error_message: Error message if the tool execution failed
        :param execution_time: Time taken to execute the tool
        """
        tool_result = ToolResult(
            tool_name=tool_name,
            input_params=input_params,
            output=output,
            status=status,
            error_message=error_message,
            execution_time=execution_time
        )

        action = AgentAction(
            agent_id=self.id if hasattr(self, 'id') else str(id(self)),
            agent_name=self.name,
            action_type=AgentActionType.TOOL_USE,
            tool_result=tool_result,
            thought=thought
        )

        self.captured_actions.append(action)

        return action

    def run_stream(self, user_message: str, on_event=None, clear_history: bool = False, skill_filter=None) -> str:
        """
        Execute single agent task with streaming (based on tool-call)

        This method supports:
        - Streaming output
        - Multi-turn reasoning based on tool-call
        - Event callbacks
        - Persistent conversation history across calls

        Args:
            user_message: User message
            on_event: Event callback function callback(event: dict)
                     event = {"type": str, "timestamp": float, "data": dict}
            clear_history: If True, clear conversation history before this call (default: False)
            skill_filter: Optional list of skill names to include in this run

        Returns:
            Final response text

        Example:
            # Multi-turn conversation with memory
            response1 = agent.run_stream("My name is Alice")
            response2 = agent.run_stream("What's my name?")  # Will remember Alice

            # Single-turn without memory
            response = agent.run_stream("Hello", clear_history=True)
        """
        # Clear history if requested
        if clear_history:
            with self.messages_lock:
                self.messages = []

        # Get model to use
        if not self.model:
            raise ValueError("No model available for agent")
        
        # Debug: log model type in run_stream
        logger.info(f"[Agent.run_stream] self.model type: {type(self.model)}, has call_stream: {hasattr(self.model, 'call_stream')}")

        # Get full system prompt with skills
        full_system_prompt = self.get_full_system_prompt(skill_filter=skill_filter)

        # Create a copy of messages for this execution to avoid concurrent modification
        # Record the original length to track which messages are new
        with self.messages_lock:
            messages_copy = self.messages.copy()
            original_length = len(self.messages)

        # Get config
        from config import conf
        max_context_turns = conf().get("agent_max_context_turns", 20)
        
        # 🎯 智能消息范围选择：根据用户意图动态决定传递多少历史
        enable_smart_context = conf().get("agent_enable_smart_context", True)
        
        if enable_smart_context and messages_copy:
            # 分析用户意图
            intent = self.analyze_intent(user_message)
            # 根据意图选择消息范围
            messages_copy = self.select_messages_by_intent(messages_copy, intent)
        
        # Create stream executor with copied message history
        executor = AgentStreamExecutor(
            agent=self,
            model=self.model,
            system_prompt=full_system_prompt,
            tools=self.tools,
            max_turns=self.max_steps,
            on_event=on_event,
            messages=messages_copy,  # Pass copied message history
            max_context_turns=max_context_turns
        )

        # Execute
        try:
            response = executor.run_stream(user_message)
        except Exception:
            # If executor cleared its messages (context overflow / message format error),
            # sync that back to the Agent's own message list so the next request
            # starts fresh instead of hitting the same overflow forever.
            if len(executor.messages) == 0:
                with self.messages_lock:
                    self.messages.clear()
                    logger.info("[Agent] Cleared Agent message history after executor recovery")
            raise

        # Append only the NEW messages from this execution (thread-safe)
        # This allows concurrent requests to both contribute to history
        with self.messages_lock:
            new_messages = executor.messages[original_length:]
            self.messages.extend(new_messages)
        
        # Store executor reference for agent_bridge to access files_to_send
        self.stream_executor = executor

        # Execute all post-process tools
        self._execute_post_process_tools()

        return response

    def analyze_intent(self, user_message: str) -> dict:
        """
        分析用户消息意图，返回意图类型和建议的历史消息范围
        
        返回:
            {
                'type': str,  # 'standalone', 'simple', 'normal', 'context_needed', 'full_context'
                'turns': int,  # 建议保留的轮次
                'reason': str  # 判断原因
            }
        """
        import re
        from config import conf
        
        # 🔴 Fix #1: URL检测 — 消息含URL视为独立任务，不引入历史上下文
        # 匹配 http/https URL 或 github.com 等常见域名
        url_pattern = r'https?://[^\s]+|(?:github|gitlab|gitee)\.com/[^\s]+'
        if re.search(url_pattern, user_message, re.IGNORECASE):
            return {
                'type': 'standalone',
                'turns': 0,   # 0 表示不引入任何历史上下文
                'reason': '消息包含URL，视为独立任务，不引入历史上下文'
            }
        
        # 获取配置
        simple_keywords = conf().get("agent_simple_keywords", [
            "你好", "hello", "hi", "在吗", "吃了吗", "天气", "几点了",
            "哈哈", "笑死", "哈哈哈", "呵呵", "早上好", "晚上好", "晚安",
            "最近怎么样", "忙吗", "干嘛呢", "有啥", "推荐", "随便"
        ])
        
        full_context_keywords = conf().get("agent_full_context_keywords", [
            "之前", "上次", "上次我们", "之前你说的", "记得", "我之前",
            "帮我看看", "查看", "读取", "搜索", "查找", "调试", "代码",
            "修改", "写一个", "创建", "生成", "执行", "运行", "安装",
            "总结", "分析", "对比", "翻译", "解释", "为什么", "怎么",
            "提示词", "人设", "prompt", "越狱", "管理员", "安全"
        ])
        
        context_needed_keywords = conf().get("agent_context_needed_keywords", [
            "然后", "接下来", "继续", "刚才", "上面",
            "他/她/它", "你说的", "你的意思是", "对吧", "是不是"
        ])
        
        msg_lower = user_message.lower()
        
        # 优先检查是否需要完整上下文
        for keyword in full_context_keywords:
            if keyword.lower() in msg_lower:
                return {
                    'type': 'full_context',
                    'turns': -1,  # -1 表示全部
                    'reason': f"检测到关键词'{keyword}'需要完整上下文"
                }
        
        # 检查是否需要中等上下文
        for keyword in context_needed_keywords:
            if keyword.lower() in msg_lower:
                return {
                    'type': 'context_needed',
                    'turns': conf().get("agent_context_turns", 5),
                    'reason': f"检测到关键词'{keyword}'需要中等上下文"
                }
        
        # 检查是否简单对话
        for keyword in simple_keywords:
            if keyword.lower() in msg_lower:
                return {
                    'type': 'simple',
                    'turns': conf().get("agent_simple_turns", 2),
                    'reason': f"检测到简单对话关键词'{keyword}'"
                }
        
        # 默认返回中等上下文
        return {
            'type': 'normal',
            'turns': conf().get("agent_normal_turns", 3),
            'reason': '默认中等上下文'
        }
    
    def select_messages_by_intent(self, messages: list, intent: dict) -> list:
        """
        根据意图选择需要传递的消息
        
        Args:
            messages: 完整消息列表
            intent: analyze_intent返回的意图字典
            
        Returns:
            筛选后的消息列表
        """
        if not messages:
            return []
        
        requested_turns = intent.get('turns', -1)
        
        # 🔴 Fix #2: standalone 意图 (turns=0) — 不引入任何历史上下文
        if requested_turns == 0:
            logger.info(f"[Agent] 检测到独立任务，跳过历史上下文注入 (type={intent.get('type')}, 原因={intent.get('reason')})")
            return []
        
        # 识别完整轮次
        turns = self._identify_complete_turns_from_messages(messages)
        
        if not turns:
            # 无法识别轮次，返回原始消息
            return messages
        
        # -1 表示需要全部
        if requested_turns == -1:
            return messages
        
        # 限制轮次
        requested_turns = min(requested_turns, len(turns))
        kept_turns = turns[-requested_turns:]
        
        # 重建消息列表
        selected_messages = []
        for turn in kept_turns:
            selected_messages.extend(turn['messages'])
        
        logger.info(f"[Agent] 智能消息选择: {len(messages)}条 -> {len(selected_messages)}条 "
                   f"({requested_turns}轮, 类型={intent['type']}, 原因={intent['reason']})")
        
        return selected_messages
    
    def _identify_complete_turns_from_messages(self, messages: list) -> list:
        """
        从消息列表识别完整轮次 (不依赖agent实例)
        """
        turns = []
        current_turn = {'messages': []}
        
        for msg in messages:
            role = msg.get('role')
            content = msg.get('content', [])
            
            if role == 'user':
                is_user_query = False
                has_tool_result = False
                if isinstance(content, list):
                    has_text = any(
                        isinstance(block, dict) and block.get('type') == 'text'
                        for block in content
                    )
                    has_tool_result = any(
                        isinstance(block, dict) and block.get('type') == 'tool_result'
                        for block in content
                    )
                    is_user_query = has_text and not has_tool_result
                elif isinstance(content, str):
                    is_user_query = True
                
                if is_user_query:
                    if current_turn['messages']:
                        turns.append(current_turn)
                    current_turn = {'messages': [msg]}
                else:
                    current_turn['messages'].append(msg)
            else:
                current_turn['messages'].append(msg)
        
        if current_turn['messages']:
            turns.append(current_turn)
        
        return turns

    def clear_history(self):
        """Clear conversation history and captured actions"""
        self.messages = []
        self.captured_actions = []

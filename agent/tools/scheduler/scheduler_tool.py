"""
Scheduler tool for creating and managing scheduled tasks
"""

import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from croniter import croniter

from agent.tools.base_tool import BaseTool, ToolResult
from bridge.context import Context, ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger


class SchedulerTool(BaseTool):
    """
    Tool for managing scheduled tasks (reminders, notifications, etc.)
    """
    
    name: str = "scheduler"
    description: str = (
        "创建、查询、管理、清空定时任务（提醒、周期性任务等）。\n"
        "🚨【绝对规则/强制】🚨：只要用户要求「定时/每天/每周/X分钟后」做什么事，或者要求「删除/清空/查询」定时任务，你**必须、立刻、强制**使用本工具 (scheduler) 执行真实的后台命令！\n"
        "绝不允许仅仅口头回复“已安排”、“已清空”而不调用本工具，否则属于严重错误！\n\n"
        "使用方法：\n"
        "- 创建：action='create', name='任务名', message/ai_task='内容', schedule_type='once/interval/cron', schedule_value='...'\n"
        "- 查询：action='list' / action='get', task_id='任务ID'\n"
        "- 删除单个：action='delete', task_id='任务ID'\n"
        "- 删除/清空全部：action='clear'\n"
        "- 管理：action='enable/disable', task_id='任务ID'\n\n"
        "调度类型：\n"
        "- once: 一次性任务，支持相对时间(+5s,+10m,+1h,+1d)或ISO时间\n"
        "- interval: 固定间隔(秒)，如3600表示每小时\n"
        "- cron: cron表达式，如'0 8 * * *'表示每天8点\n\n"
        "注意：一次性任务(今天/明天)一定要用 once+ISO；只有「每天/每周」用 cron！"
    )
    params: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list", "get", "delete", "clear", "enable", "disable"],
                "description": "操作类型: create(创建), list(列表), get(查询), delete(删除单个), clear(清空所有), enable(启用), disable(禁用)"
            },
            "task_id": {
                "type": "string",
                "description": "任务ID (用于 get/delete/enable/disable 操作)"
            },
            "name": {
                "type": "string",
                "description": "任务名称 (用于 create 操作)"
            },
            "message": {
                "type": "string",
                "description": "固定消息内容 (与ai_task二选一)"
            },
            "ai_task": {
                "type": "string",
                "description": "AI任务描述 (与message二选一)，用于定时让AI执行的任务"
            },
            "schedule_type": {
                "type": "string",
                "enum": ["cron", "interval", "once"],
                "description": "调度类型 (用于 create 操作): cron(cron表达式), interval(固定间隔秒数), once(一次性)"
            },
            "schedule_value": {
                "type": "string",
                "description": "调度值: cron表达式/间隔秒数/时间(+5s,+10m,+1h或ISO格式)"
            }
        },
        "required": ["action"]
    }
    
    def __init__(self, config: dict = None):
        super().__init__()
        self.config = config or {}
        
        # Will be set by agent bridge
        self.task_store = None
        self.current_context = None
    
    def execute(self, params: dict) -> ToolResult:
        """
        Execute scheduler operations
        
        Args:
            params: Dictionary containing:
                - action: Operation type (create/list/get/delete/enable/disable)
                - Other parameters depending on action
            
        Returns:
            ToolResult object
        """
        # Extract parameters
        action = params.get("action")
        kwargs = params
        
        if not self.task_store:
            return ToolResult.fail("错误: 定时任务系统未初始化")
        
        try:
            if action == "create":
                result = self._create_task(**kwargs)
                return ToolResult.success(result)
            elif action == "list":
                result = self._list_tasks(**kwargs)
                return ToolResult.success(result)
            elif action == "get":
                result = self._get_task(**kwargs)
                return ToolResult.success(result)
            elif action == "delete":
                result = self._delete_task(**kwargs)
                return ToolResult.success(result)
            elif action == "enable":
                result = self._enable_task(**kwargs)
                return ToolResult.success(result)
            elif action == "disable":
                result = self._disable_task(**kwargs)
                return ToolResult.success(result)
            elif action == "clear":
                result = self._clear_tasks(**kwargs)
                return ToolResult.success(result)
            else:
                return ToolResult.fail(f"未知操作: {action}")
        except Exception as e:
            logger.error(f"[SchedulerTool] Error: {e}")
            return ToolResult.fail(f"操作失败: {str(e)}")
    
    def _create_task(self, **kwargs) -> str:
        """Create a new scheduled task"""
        name = kwargs.get("name")
        message = kwargs.get("message")
        ai_task = kwargs.get("ai_task")
        schedule_type = kwargs.get("schedule_type")
        schedule_value = kwargs.get("schedule_value")
        
        # Validate required fields
        if not name:
            return "错误: 缺少任务名称 (name)"
        
        # Check that exactly one of message/ai_task is provided
        if not message and not ai_task:
            return "错误: 必须提供 message（固定消息）或 ai_task（AI任务）之一"
        if message and ai_task:
            return "错误: message 和 ai_task 只能提供其中一个"
        
        if not schedule_type:
            return "错误: 缺少调度类型 (schedule_type)"
        if not schedule_value:
            return "错误: 缺少调度值 (schedule_value)"
        
        # Validate schedule
        schedule = self._parse_schedule(schedule_type, schedule_value)
        if not schedule:
            return f"错误: 无效的调度配置 - type: {schedule_type}, value: {schedule_value}"
        
        # Get context info for receiver
        if not self.current_context:
            return "错误: 无法获取当前对话上下文"
        
        context = self.current_context
        
        # ── 目标群解析 ─────────────────────────────────────────────────────
        # 若 ai_task/message 中含有明确的目标群名（如"发到Bot测试群"），
        # 则查询群 wxid 并覆盖 receiver，确保任务发到指定群而非发件人私聊。
        task_content = ai_task or message or ""
        resolved_receiver, resolved_receiver_name, resolved_is_group = \
            self._resolve_group_receiver(task_content, context)
        # ──────────────────────────────────────────────────────────────────
        
        # Create task
        task_id = str(uuid.uuid4())[:8]
        
        # Build action based on message or ai_task
        if message:
            action = {
                "type": "send_message",
                "content": message,
                "receiver": resolved_receiver,
                "receiver_name": resolved_receiver_name,
                "is_group": resolved_is_group,
                "channel_type": self.config.get("channel_type", "unknown")
            }
        else:  # ai_task
            action = {
                "type": "agent_task",
                "task_description": ai_task,
                "receiver": resolved_receiver,
                "receiver_name": resolved_receiver_name,
                "is_group": resolved_is_group,
                "channel_type": self.config.get("channel_type", "unknown")
            }

        
        # 针对钉钉单聊，额外存储 sender_staff_id
        msg = context.kwargs.get("msg")
        if msg and hasattr(msg, 'sender_staff_id') and not context.get("isgroup", False):
            action["dingtalk_sender_staff_id"] = msg.sender_staff_id
        
        task_data = {
            "id": task_id,
            "name": name,
            "enabled": True,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "schedule": schedule,
            "action": action
        }
        
        # Calculate initial next_run_at
        next_run = self._calculate_next_run(task_data)
        if next_run:
            task_data["next_run_at"] = next_run.isoformat()
        
        # Save task
        self.task_store.add_task(task_data)
        
        # Format response
        schedule_desc = self._format_schedule_description(schedule)
        receiver_desc = task_data["action"]["receiver_name"] or task_data["action"]["receiver"]
        
        if message:
            content_desc = f"💬 固定消息: {message}"
        else:
            content_desc = f"🤖 AI任务: {ai_task}"
        
        return (
            f"✅ 定时任务创建成功\n\n"
            f"📋 任务ID: {task_id}\n"
            f"📝 名称: {name}\n"
            f"⏰ 调度: {schedule_desc}\n"
            f"👤 接收者: {receiver_desc}\n"
            f"{content_desc}\n"
            f"🕐 下次执行: {next_run.strftime('%Y-%m-%d %H:%M:%S') if next_run else '未知'}"
        )
    
    def _list_tasks(self, **kwargs) -> str:
        """List all tasks"""
        tasks = self.task_store.list_tasks()
        
        if not tasks:
            return "📋 暂无定时任务"
        
        lines = [f"📋 定时任务列表 (共 {len(tasks)} 个)\n"]
        
        for task in tasks:
            status = "✅" if task.get("enabled", True) else "❌"
            schedule_desc = self._format_schedule_description(task.get("schedule", {}))
            next_run = task.get("next_run_at")
            next_run_str = datetime.fromisoformat(next_run).strftime('%m-%d %H:%M') if next_run else "未知"
            
            lines.append(
                f"{status} [{task['id']}] {task['name']}\n"
                f"   ⏰ {schedule_desc} | 下次: {next_run_str}"
            )
        
        return "\n".join(lines)
    
    def _get_task(self, **kwargs) -> str:
        """Get task details"""
        task_id = kwargs.get("task_id")
        if not task_id:
            return "错误: 缺少任务ID (task_id)"
        
        task = self.task_store.get_task(task_id)
        if not task:
            return f"错误: 任务 '{task_id}' 不存在"
        
        status = "启用" if task.get("enabled", True) else "禁用"
        schedule_desc = self._format_schedule_description(task.get("schedule", {}))
        action = task.get("action", {})
        next_run = task.get("next_run_at")
        next_run_str = datetime.fromisoformat(next_run).strftime('%Y-%m-%d %H:%M:%S') if next_run else "未知"
        last_run = task.get("last_run_at")
        last_run_str = datetime.fromisoformat(last_run).strftime('%Y-%m-%d %H:%M:%S') if last_run else "从未执行"
        
        return (
            f"📋 任务详情\n\n"
            f"ID: {task['id']}\n"
            f"名称: {task['name']}\n"
            f"状态: {status}\n"
            f"调度: {schedule_desc}\n"
            f"接收者: {action.get('receiver_name', action.get('receiver'))}\n"
            f"消息: {action.get('content')}\n"
            f"下次执行: {next_run_str}\n"
            f"上次执行: {last_run_str}\n"
            f"创建时间: {datetime.fromisoformat(task['created_at']).strftime('%Y-%m-%d %H:%M:%S')}"
        )
    
    def _delete_task(self, **kwargs) -> str:
        """Delete a task"""
        task_id = kwargs.get("task_id")
        if not task_id:
            return "错误: 缺少任务ID (task_id)"
        
        task = self.task_store.get_task(task_id)
        if not task:
            return f"错误: 任务 '{task_id}' 不存在"
        
        self.task_store.delete_task(task_id)
        return f"✅ 任务 '{task['name']}' ({task_id}) 已删除"
    
    def _clear_tasks(self, **kwargs) -> str:
        """Clear all tasks"""
        tasks = self.task_store.list_tasks(enabled_only=False)
        deleted = 0
        for task in tasks:
            self.task_store.delete_task(task['id'])
            deleted += 1
        return f"✅ 已成功清空所有定时任务。共删除了 {deleted} 个任务。"
    
    def _enable_task(self, **kwargs) -> str:
        """Enable a task"""
        task_id = kwargs.get("task_id")
        if not task_id:
            return "错误: 缺少任务ID (task_id)"
        
        task = self.task_store.get_task(task_id)
        if not task:
            return f"错误: 任务 '{task_id}' 不存在"
        
        self.task_store.enable_task(task_id, True)
        return f"✅ 任务 '{task['name']}' ({task_id}) 已启用"
    
    def _disable_task(self, **kwargs) -> str:
        """Disable a task"""
        task_id = kwargs.get("task_id")
        if not task_id:
            return "错误: 缺少任务ID (task_id)"
        
        task = self.task_store.get_task(task_id)
        if not task:
            return f"错误: 任务 '{task_id}' 不存在"
        
        self.task_store.enable_task(task_id, False)
        return f"✅ 任务 '{task['name']}' ({task_id}) 已禁用"
    
    def _parse_schedule(self, schedule_type: str, schedule_value: str) -> Optional[dict]:
        """Parse and validate schedule configuration"""
        try:
            if schedule_type == "cron":
                # Validate cron expression
                croniter(schedule_value)
                return {"type": "cron", "expression": schedule_value}
            
            elif schedule_type == "interval":
                # Parse interval in seconds
                seconds = int(schedule_value)
                if seconds <= 0:
                    return None
                return {"type": "interval", "seconds": seconds}
            
            elif schedule_type == "once":
                # Parse datetime - support both relative and absolute time
                
                # Check if it's relative time (e.g., "+5s", "+10m", "+1h", "+1d")
                if schedule_value.startswith("+"):
                    import re
                    match = re.match(r'\+(\d+)([smhd])', schedule_value)
                    if match:
                        amount = int(match.group(1))
                        unit = match.group(2)
                        
                        from datetime import timedelta
                        now = datetime.now()
                        
                        if unit == 's':  # seconds
                            target_time = now + timedelta(seconds=amount)
                        elif unit == 'm':  # minutes
                            target_time = now + timedelta(minutes=amount)
                        elif unit == 'h':  # hours
                            target_time = now + timedelta(hours=amount)
                        elif unit == 'd':  # days
                            target_time = now + timedelta(days=amount)
                        else:
                            return None
                        
                        return {"type": "once", "run_at": target_time.isoformat()}
                    else:
                        logger.error(f"[SchedulerTool] Invalid relative time format: {schedule_value}")
                        return None
                else:
                    # Absolute time in ISO format
                    datetime.fromisoformat(schedule_value)
                    return {"type": "once", "run_at": schedule_value}
            
        except Exception as e:
            logger.error(f"[SchedulerTool] Invalid schedule: {e}")
            return None
        
        return None
    
    def _calculate_next_run(self, task: dict) -> Optional[datetime]:
        """Calculate next run time for a task"""
        schedule = task.get("schedule", {})
        schedule_type = schedule.get("type")
        now = datetime.now()
        
        if schedule_type == "cron":
            expression = schedule.get("expression")
            cron = croniter(expression, now)
            return cron.get_next(datetime)
        
        elif schedule_type == "interval":
            seconds = schedule.get("seconds", 0)
            from datetime import timedelta
            return now + timedelta(seconds=seconds)
        
        elif schedule_type == "once":
            run_at_str = schedule.get("run_at")
            return datetime.fromisoformat(run_at_str)
        
        return None
    
    def _format_schedule_description(self, schedule: dict) -> str:
        """Format schedule as human-readable description"""
        schedule_type = schedule.get("type")
        
        if schedule_type == "cron":
            expr = schedule.get("expression", "")
            # Try to provide friendly description
            if expr == "0 9 * * *":
                return "每天 9:00"
            elif expr == "0 */1 * * *":
                return "每小时"
            elif expr == "*/30 * * * *":
                return "每30分钟"
            else:
                return f"Cron: {expr}"
        
        elif schedule_type == "interval":
            seconds = schedule.get("seconds", 0)
            if seconds >= 86400:
                days = seconds // 86400
                return f"每 {days} 天"
            elif seconds >= 3600:
                hours = seconds // 3600
                return f"每 {hours} 小时"
            elif seconds >= 60:
                minutes = seconds // 60
                return f"每 {minutes} 分钟"
            else:
                return f"每 {seconds} 秒"
        
        elif schedule_type == "once":
            run_at = schedule.get("run_at", "")
            try:
                dt = datetime.fromisoformat(run_at)
                return f"一次性 ({dt.strftime('%Y-%m-%d %H:%M')})"
            except Exception:
                return "一次性"
        
        return "未知"
    
    def _get_receiver_name(self, context: Context) -> str:
        """Get receiver name from context"""
        try:
            msg = context.get("msg")
            if msg:
                if context.get("isgroup"):
                    return msg.other_user_nickname or "群聊"
                else:
                    return msg.from_user_nickname or "用户"
        except Exception:
            pass
        return "未知"

    def _resolve_group_receiver(self, task_content: str, context: Context):
        """
        从 ai_task/message 内容中识别目标群名，查询 wxid 并返回
        (receiver_wxid, receiver_name, is_group)。
        若未找到目标群，降级使用当前 context 的 receiver。

        搜索顺序：
          1. difytask 插件的 SQLite 数据库（groups 表）
          2. wx849 的 JSON 缓存文件（wx849_rooms.json）
        """
        import re
        import os
        import json

        default_receiver      = context.get("receiver")
        default_receiver_name = self._get_receiver_name(context)
        default_is_group      = context.get("isgroup", False)

        # 从内容中提取目标群名（匹配"发到/发送到/发至/发给/推送到/在/到 X群"）
        patterns = [
            r'(?:发到|发送到|发至|发给|发往|推送到|在|到)\s*["\'\u201c\u2018]?([^"\'\u201c\u201d\u2018\u2019，,。！!？?\n]{2,}?群)["\'\u201d\u2019]?',
            r'(?:推送|搜索.{0,10}?发.{0,5}?到|整理.{0,10}?发.{0,5}?到)\s*["\'\u201c\u2018]?([^"\'\u201c\u201d\u2018\u2019，,。！!？?\n]{2,}?群)["\'\u201d\u2019]?',
        ]

        group_name = None
        for pat in patterns:
            m = re.search(pat, task_content)
            if m:
                candidate = m.group(1).strip().strip('"\'""\'\'')
                if len(candidate) >= 2:
                    group_name = candidate
                    break

        if not group_name:
            return default_receiver, default_receiver_name, default_is_group

        logger.info(f"[SchedulerTool] Detected target group name in task: '{group_name}'")

        # 1. 查询 difytask SQLite 数据库
        try:
            import sqlite3
            difytask_db = os.path.normpath(os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "..", "..", "plugins", "difytask", "data", "tasks.db"
            ))
            if os.path.isfile(difytask_db):
                conn = sqlite3.connect(difytask_db)
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT wxid, nickname FROM groups WHERE nickname LIKE ?",
                    (f"%{group_name}%",)
                )
                rows = cursor.fetchall()
                conn.close()
                if rows:
                    for wxid, nickname in rows:
                        if nickname == group_name:
                            logger.info(f"[SchedulerTool] Resolved '{group_name}' -> {wxid} (exact, difytask DB)")
                            return wxid, nickname, True
                    wxid, nickname = rows[0]
                    logger.info(f"[SchedulerTool] Resolved '{group_name}' -> {wxid} (fuzzy, difytask DB)")
                    return wxid, nickname, True
        except Exception as e:
            logger.warning(f"[SchedulerTool] difytask DB lookup failed: {e}")

        # 2. 查询 wx849_rooms.json
        try:
            rooms_path = os.path.join("tmp", "wx849_rooms.json")
            if os.path.isfile(rooms_path):
                with open(rooms_path, "r", encoding="utf-8") as f:
                    rooms_data = json.load(f)
                entries = list(rooms_data.values()) if isinstance(rooms_data, dict) else rooms_data
                exact_match = None
                fuzzy_match = None
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    wxid     = entry.get("chatroomId") or entry.get("wxid", "")
                    nickname = entry.get("nickName", "")
                    if not wxid or "@chatroom" not in wxid:
                        continue
                    if nickname == group_name:
                        exact_match = (wxid, nickname)
                        break
                    if group_name in nickname and fuzzy_match is None:
                        fuzzy_match = (wxid, nickname)
                result = exact_match or fuzzy_match
                if result:
                    wxid, nickname = result
                    match_type = "exact" if exact_match else "fuzzy"
                    logger.info(f"[SchedulerTool] Resolved '{group_name}' -> {wxid} ({match_type}, wx849_rooms.json)")
                    return wxid, nickname, True
        except Exception as e:
            logger.warning(f"[SchedulerTool] wx849_rooms.json lookup failed: {e}")

        logger.warning(f"[SchedulerTool] Could not resolve group '{group_name}', using default receiver")
        return default_receiver, default_receiver_name, default_is_group


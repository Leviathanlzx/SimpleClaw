"""
TelegramChannel: Telegram 渠道接入，基于 python-telegram-bot (v20+) 长轮询模式。

相比参考实现的精简：
  - 仅支持文字消息（不处理图片/语音/文件等媒体）
  - 不实现群组@mention 过滤（仅私聊模式）
  - 不实现流式打字模拟（_send_with_streaming）
  - Markdown→HTML 转换保留核心规则，去掉复杂表格渲染
  - 允许名单为空时接受所有用户（单用户 bot 场景）
"""

import asyncio
import re

try:
    from telegram import BotCommand, Update
    from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
    from telegram.request import HTTPXRequest
    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False

from .bus import MessageBus, InboundMessage, OutboundMessage
from .channels import BaseChannel

TELEGRAM_MAX_LEN = 4000  # Telegram 单条消息字符上限


# ─────────────────────────────────────────
# Markdown → Telegram HTML 转换
# ─────────────────────────────────────────

def _markdown_to_telegram_html(text: str) -> str:
    """将 Markdown 文本转换为 Telegram 接受的 HTML 格式。"""
    if not text:
        return ""

    # 1. 提取并保护代码块（```...```），避免被后续规则破坏
    code_blocks: list[str] = []

    def _save_code_block(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"

    text = re.sub(r"```[\w]*\n?([\s\S]*?)```", _save_code_block, text)

    # 2. 提取并保护行内代码（`...`）
    inline_codes: list[str] = []

    def _save_inline(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", _save_inline, text)

    # 3. 标题 # Title → 加粗文字
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # 4. 引用块 > text → 斜体
    text = re.sub(r"^>\s*(.+)$", r"<i>\1</i>", text, flags=re.MULTILINE)

    # 5. 转义 HTML 特殊字符
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 6. 链接 [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # 7. 粗体 **text** 或 __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)

    # 8. 斜体 _text_（避免匹配变量名内的下划线）
    text = re.sub(r"(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])", r"<i>\1</i>", text)

    # 9. 删除线 ~~text~~
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    # 10. 无序列表 - item → • item
    text = re.sub(r"^[-*]\s+", "• ", text, flags=re.MULTILINE)

    # 11. 还原行内代码
    for i, code in enumerate(inline_codes):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00IC{i}\x00", f"<code>{escaped}</code>")

    # 12. 还原代码块
    for i, code in enumerate(code_blocks):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{escaped}</code></pre>")

    return text


def _split_message(text: str, max_len: int = TELEGRAM_MAX_LEN) -> list[str]:
    """将长文本按 max_len 拆分，优先在换行处断开。"""
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # 在 max_len 以内寻找最后一个换行符
        cut = text.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


# ─────────────────────────────────────────
# TelegramChannel
# ─────────────────────────────────────────

class TelegramChannel(BaseChannel):
    """
    Telegram 渠道：长轮询接收消息，通过 Bot API 发送回复。

    消息流：
      Telegram 用户 → _on_message() → InboundMessage → MessageBus → AgentLoop
      AgentLoop     → OutboundMessage → MessageBus → send() → Telegram 用户
    """

    BOT_COMMANDS = [
        BotCommand("start", "Start the bot") if HAS_TELEGRAM else None,
        BotCommand("help", "Show available commands") if HAS_TELEGRAM else None,
    ]

    def __init__(self, token: str, bus: MessageBus, allowed_user_ids: list[int] | None = None):
        super().__init__(bus)
        self.token = token
        self.allowed_user_ids: list[int] = allowed_user_ids or []
        self._app: "Application | None" = None
        self._typing_tasks: dict[str, asyncio.Task] = {}
        self._running = False

    def _is_allowed(self, user_id: int) -> bool:
        """若 allowed_user_ids 为空则放行所有用户（单用户 bot 场景）。"""
        if not self.allowed_user_ids:
            return True
        return user_id in self.allowed_user_ids

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if not HAS_TELEGRAM:
            print("[Telegram] python-telegram-bot not installed. Run: pip install python-telegram-bot")
            return

        if not self.token:
            print("[Telegram] Bot token not configured, channel disabled.")
            return

        self._running = True

        # 构建 Application，增大连接池避免长时间运行超时
        req = HTTPXRequest(
            connection_pool_size=8,
            pool_timeout=5.0,
            connect_timeout=30.0,
            read_timeout=30.0,
        )
        self._app = (
            Application.builder()
            .token(self.token)
            .request(req)
            .get_updates_request(req)
            .build()
        )
        self._app.add_error_handler(self._on_error)

        # 注册命令处理器
        self._app.add_handler(CommandHandler("start", self._on_start))
        self._app.add_handler(CommandHandler("help", self._on_help))

        # 注册文字消息处理器
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )

        print("[Telegram] Starting bot (long polling)...")
        await self._app.initialize()
        await self._app.start()

        # 获取 bot 信息 & 注册命令菜单
        bot_info = await self._app.bot.get_me()
        print(f"[Telegram] Bot @{bot_info.username} connected.")

        try:
            commands = [c for c in self.BOT_COMMANDS if c is not None]
            await self._app.bot.set_my_commands(commands)
        except Exception as e:
            print(f"[Telegram] Failed to register commands: {e}")

        # 启动轮询（忽略启动前积压的旧消息）
        await self._app.updater.start_polling(
            allowed_updates=["message"],
            drop_pending_updates=True,
        )

        # 保持运行直到 stop() 被调用
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        self._running = False
        for chat_id in list(self._typing_tasks):
            self._stop_typing(chat_id)
        if self._app:
            print("[Telegram] Stopping bot...")
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            self._app = None

    # ── Outbound (Agent → Telegram) ────────────────────────────────────────────

    async def send(self, msg: OutboundMessage) -> None:
        if not self._app:
            print("[Telegram] Bot not running, cannot send message.")
            return

        # 回复完成后停止打字状态
        self._stop_typing(msg.chat_id)

        try:
            chat_id = int(msg.chat_id)
        except ValueError:
            print(f"[Telegram] Invalid chat_id: {msg.chat_id}")
            return

        if not msg.content:
            return

        for chunk in _split_message(msg.content):
            await self._send_text(chat_id, chunk)

    async def _send_text(self, chat_id: int, text: str) -> None:
        """发送文字消息，先尝试 HTML 格式，失败则回退纯文本。"""
        try:
            html = _markdown_to_telegram_html(text)
            await self._app.bot.send_message(
                chat_id=chat_id,
                text=html,
                parse_mode="HTML",
            )
        except Exception as e:
            print(f"[Telegram] HTML send failed ({e}), falling back to plain text.")
            try:
                await self._app.bot.send_message(chat_id=chat_id, text=text)
            except Exception as e2:
                print(f"[Telegram] Error sending message: {e2}")

    # ── Inbound (Telegram → Agent) ─────────────────────────────────────────────

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return

        user = update.effective_user
        if not self._is_allowed(user.id):
            print(f"[Telegram] Rejected user {user.id} (not in allowed list).")
            return

        chat_id = str(update.message.chat_id)
        content = update.message.text or ""
        if not content.strip():
            return

        print(f"[Telegram] Received from user {user.id}: {content[:60]}")

        # 发送打字状态
        self._start_typing(chat_id)

        await self.bus.publish_inbound(
            InboundMessage(
                channel="telegram",
                chat_id=chat_id,
                content=content,
                metadata={
                    "user_id": user.id,
                    "username": user.username,
                    "first_name": user.first_name,
                },
            )
        )

    # ── Command Handlers ────────────────────────────────────────────────────────

    async def _on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        name = update.effective_user.first_name or "there"
        await update.message.reply_text(
            f"Hi {name}! I'm SimpleClaw.\n\nSend me a message and I'll respond.\n/help for commands."
        )

    async def _on_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        await update.message.reply_text(
            "SimpleClaw commands:\n"
            "/start — Start the bot\n"
            "/help — Show this message"
        )

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        print(f"[Telegram] Polling error: {context.error}")

    # ── Typing Indicator ────────────────────────────────────────────────────────

    def _start_typing(self, chat_id: str) -> None:
        self._stop_typing(chat_id)
        self._typing_tasks[chat_id] = asyncio.create_task(self._typing_loop(chat_id))

    def _stop_typing(self, chat_id: str) -> None:
        task = self._typing_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()

    async def _typing_loop(self, chat_id: str) -> None:
        """每 4 秒重复发送 'typing...' 状态，直到被取消。"""
        try:
            while self._app:
                await self._app.bot.send_chat_action(
                    chat_id=int(chat_id), action="typing"
                )
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[Telegram] Typing indicator error for {chat_id}: {e}")

"""
WecomChannel: 企业微信渠道接入，基于 wecom_aibot_sdk WebSocket 长连接模式。

特性：
  - 无需公网 IP，通过 WebSocket 长连接接收消息
  - 支持文字、图片、语音、文件、混合消息
  - 消息去重防止重复处理
  - 进入会话时发送欢迎语（可配置）
  - 允许名单为空时接受所有用户
"""

import asyncio
import importlib.util
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any

try:
    from wecom_aibot_sdk import WSClient, generate_req_id
    HAS_WECOM = True
except ImportError:
    HAS_WECOM = False

from ..bus import MessageBus, InboundMessage, OutboundMessage
from .base import BaseChannel


# ── WecomChannel ───────────────────────────────────────────────────────────────

class WecomChannel(BaseChannel):
    """
    企业微信渠道：WebSocket 长连接接收消息，通过 SDK reply API 发送回复。

    消息流：
      企业微信用户 → _on_*_message() → InboundMessage → MessageBus → AgentLoop
      AgentLoop    → OutboundMessage → MessageBus → send() → 企业微信用户
    """

    def __init__(
        self,
        bot_id: str,
        secret: str,
        bus: MessageBus,
        allowed_user_ids: list[str] | None = None,
        welcome_message: str = "",
    ):
        super().__init__(bus)
        self.bot_id = bot_id
        self.secret = secret
        self.allowed_user_ids: list[str] = allowed_user_ids or []
        self.welcome_message = welcome_message

        self._client: Any = None
        self._running = False
        self._generate_req_id = None
        # 存储每个 chat 的 frame，用于 reply
        self._chat_frames: dict[str, Any] = {}
        # 消息去重缓存
        self._processed_ids: OrderedDict[str, None] = OrderedDict()

    def _is_allowed(self, sender_id: str) -> bool:
        if not self.allowed_user_ids:
            return True
        return sender_id in self.allowed_user_ids

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if not HAS_WECOM:
            print("[WeCom] wecom_aibot_sdk 未安装。运行: pip install wecom-aibot-sdk-python")
            return
        if not self.bot_id or not self.secret:
            print("[WeCom] bot_id 或 secret 未配置，渠道已禁用。")
            return

        self._running = True
        self._generate_req_id = generate_req_id

        self._client = WSClient({
            "bot_id": self.bot_id,
            "secret": self.secret,
            "reconnect_interval": 1000,
            "max_reconnect_attempts": -1,
            "heartbeat_interval": 30000,
        })

        self._client.on("connected", self._on_connected)
        self._client.on("authenticated", self._on_authenticated)
        self._client.on("disconnected", self._on_disconnected)
        self._client.on("error", self._on_error)
        self._client.on("message.text", self._on_text_message)
        self._client.on("message.image", self._on_image_message)
        self._client.on("message.voice", self._on_voice_message)
        self._client.on("message.file", self._on_file_message)
        self._client.on("message.mixed", self._on_mixed_message)
        self._client.on("event.enter_chat", self._on_enter_chat)

        print("[WeCom] 正在启动 WebSocket 长连接（无需公网 IP）...")
        await self._client.connect_async()

        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        self._running = False
        if self._client:
            await self._client.disconnect()
        print("[WeCom] 已停止。")

    # ── Outbound ───────────────────────────────────────────────────────────────

    async def send(self, msg: OutboundMessage) -> None:
        if not self._client:
            print("[WeCom] 客户端未初始化，无法发送消息。")
            return
        content = msg.content.strip()
        if not content:
            return
        frame = self._chat_frames.get(msg.chat_id)
        if not frame:
            print(f"[WeCom] 未找到 chat {msg.chat_id} 的 frame，无法回复。")
            return
        try:
            stream_id = self._generate_req_id("stream")
            await self._client.reply_stream(frame, stream_id, content, finish=True)
        except Exception as e:
            print(f"[WeCom] 发送消息失败: {e}")

    # ── Connection Events ──────────────────────────────────────────────────────

    async def _on_connected(self, frame: Any) -> None:
        print("[WeCom] WebSocket 已连接。")

    async def _on_authenticated(self, frame: Any) -> None:
        print("[WeCom] 鉴权成功，Bot 已就绪。")

    async def _on_disconnected(self, frame: Any) -> None:
        reason = frame.body if hasattr(frame, "body") else str(frame)
        print(f"[WeCom] WebSocket 断开: {reason}")

    async def _on_error(self, frame: Any) -> None:
        print(f"[WeCom] 错误: {frame}")

    # ── Message Events ─────────────────────────────────────────────────────────

    async def _on_text_message(self, frame: Any) -> None:
        await self._process_message(frame, "text")

    async def _on_image_message(self, frame: Any) -> None:
        await self._process_message(frame, "image")

    async def _on_voice_message(self, frame: Any) -> None:
        await self._process_message(frame, "voice")

    async def _on_file_message(self, frame: Any) -> None:
        await self._process_message(frame, "file")

    async def _on_mixed_message(self, frame: Any) -> None:
        await self._process_message(frame, "mixed")

    async def _on_enter_chat(self, frame: Any) -> None:
        if not self.welcome_message:
            return
        try:
            body = frame.body if hasattr(frame, "body") else (frame if isinstance(frame, dict) else {})
            chat_id = body.get("chatid", "") if isinstance(body, dict) else ""
            if chat_id:
                await self._client.reply_welcome(frame, {
                    "msgtype": "text",
                    "text": {"content": self.welcome_message},
                })
        except Exception as e:
            print(f"[WeCom] 处理 enter_chat 失败: {e}")

    # ── Message Processing ─────────────────────────────────────────────────────

    async def _process_message(self, frame: Any, msg_type: str) -> None:
        try:
            # 提取 body
            if hasattr(frame, "body"):
                body = frame.body or {}
            elif isinstance(frame, dict):
                body = frame.get("body", frame)
            else:
                body = {}

            if not isinstance(body, dict):
                return

            # 消息去重
            msg_id = body.get("msgid", "")
            if not msg_id:
                msg_id = f"{body.get('chatid', '')}_{body.get('sendertime', '')}"
            if msg_id in self._processed_ids:
                return
            self._processed_ids[msg_id] = None
            while len(self._processed_ids) > 1000:
                self._processed_ids.popitem(last=False)

            # 发送者 & 会话信息
            from_info = body.get("from", {})
            sender_id = from_info.get("userid", "unknown") if isinstance(from_info, dict) else "unknown"
            chat_type = body.get("chattype", "single")
            chat_id = body.get("chatid", sender_id)

            # 权限检查
            if not self._is_allowed(sender_id):
                print(f"[WeCom] 拒绝用户 {sender_id}（不在允许名单中）。")
                return

            # 解析消息内容
            content = self._extract_content(body, msg_type)
            if not content:
                return

            print(f"[WeCom] 收到来自 {sender_id} 的 {msg_type} 消息: {content[:60]}")

            # 存储 frame 用于后续回复
            self._chat_frames[chat_id] = frame

            await self.bus.publish_inbound(
                InboundMessage(
                    channel="wecom",
                    chat_id=chat_id,
                    content=content,
                    metadata={
                        "sender_id": sender_id,
                        "msg_type": msg_type,
                        "chat_type": chat_type,
                        "message_id": msg_id,
                    },
                )
            )

        except Exception as e:
            print(f"[WeCom] 处理消息失败: {e}")

    def _extract_content(self, body: dict, msg_type: str) -> str:
        """从消息体中提取文本内容。媒体消息仅保留元信息描述。"""
        if msg_type == "text":
            return body.get("text", {}).get("content", "")

        elif msg_type == "voice":
            # WeCom 会对语音做 ASR 转文字
            voice_content = body.get("voice", {}).get("content", "")
            return f"[语音] {voice_content}" if voice_content else "[语音消息]"

        elif msg_type == "image":
            image_info = body.get("image", {})
            return f"[图片消息: url={image_info.get('url', '')}]"

        elif msg_type == "file":
            file_info = body.get("file", {})
            return f"[文件消息: {file_info.get('name', 'unknown')}]"

        elif msg_type == "mixed":
            parts = []
            for item in body.get("mixed", {}).get("item", []):
                item_type = item.get("type", "")
                if item_type == "text":
                    text = item.get("text", {}).get("content", "")
                    if text:
                        parts.append(text)
                else:
                    parts.append(f"[{item_type}]")
            return "\n".join(parts)

        return f"[{msg_type}消息]"

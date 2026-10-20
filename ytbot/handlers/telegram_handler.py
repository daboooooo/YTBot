"""
Telegram handler for YTBot

Handles Telegram bot commands and message processing.
"""

import asyncio
import os
import time
from typing import Any, Awaitable, Callable, Dict, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import NetworkError, RetryAfter
from telegram.ext import ContextTypes, filters

from ..core.config import get_config
from ..core.enhanced_logger import get_logger
from ..core.user_state import UserStateManager, UserState
from ..services.telegram_service import TelegramService
from ..services.storage_service import StorageService
from ..services.download_service import DownloadService
from ..services.download_pipeline import (
    DownloadPipeline,
    ProgressSink,
    estimate_format_size,
    LARGE_FILE_THRESHOLD,
)

logger = get_logger(__name__)


class TelegramProgressSink:
    """ProgressSink implementation for the Telegram bot flow.

    Delegates to the TelegramHandler's existing progress-edit and
    success-message methods so that the DownloadPipeline can emit
    Telegram messages without knowing handler internals.
    """

    # 阶段消息最小间隔（秒），避免连续 stage 切换时密集编辑触发限流
    STAGE_THROTTLE_SECONDS = 3.0

    def __init__(
        self,
        telegram_service: TelegramService,
        chat_id: int,
        message_id: int,
        is_twitter: bool = False,
        twitter_result: Optional[Dict[str, Any]] = None,
        progress_label: Optional[str] = None,
        *,
        update_progress_callback: Optional[
            Callable[[int, int, Dict[str, Any]], Awaitable[None]]
        ] = None,
        send_success_callback: Optional[
            Callable[
                [int, int, Dict[str, Any], Dict[str, Any], str, bool, bool],
                Awaitable[None],
            ]
        ] = None,
    ) -> None:
        self._telegram_service = telegram_service
        self._chat_id = chat_id
        self._message_id = message_id
        self._is_twitter = is_twitter
        self._twitter_result = twitter_result
        self._progress_label = progress_label
        self._update_progress = update_progress_callback
        self._send_success = send_success_callback
        # 阶段消息节流：记录上次成功编辑时间
        self._last_stage_edit_time: float = 0.0
        # 上传进度编辑合并：任意时刻最多一个编辑任务，同步回调只覆盖最新文案。
        # Nextcloud 上传在事件循环线程内阻塞执行，旧实现为每次回调都创建
        # 编辑任务，上传结束瞬间会堆积并连发多条过期编辑；flush 清空任务
        # 列表后，任务的 done_callback 再执行 remove 还会抛出
        # ValueError: list.remove(x): x not in list。
        self._upload_edit_task: Optional[asyncio.Task] = None
        self._pending_upload_text: Optional[str] = None

    async def on_download_stage(self, text: str) -> None:
        """下载阶段消息，带节流：避免连续 stage 切换时密集编辑触发限流。

        距上次成功编辑不足 STAGE_THROTTLE_SECONDS 秒时跳过。
        仅在编辑成功后更新时间戳，熔断期丢弃后能立即恢复。
        """
        now = time.monotonic()
        if (
            now - self._last_stage_edit_time
            < self.STAGE_THROTTLE_SECONDS
        ):
            logger.debug(
                f"⏭️ Dropping stage update for message {self._message_id} "
                f"(throttled, last edit {now - self._last_stage_edit_time:.1f}s ago)"
            )
            return

        result = await self._telegram_service.edit_message(
            chat_id=self._chat_id,
            message_id=self._message_id,
            text=text,
        )
        if result is not None:
            self._last_stage_edit_time = now

    async def on_download_progress(
        self, progress_data: Dict[str, Any]
    ) -> None:
        if self._update_progress:
            await self._update_progress(
                self._chat_id, self._message_id, progress_data
            )

    async def on_upload_progress(
        self,
    ) -> Callable[[int, int], None]:
        last_time = [0.0]
        last_percent = [-100.0]

        def callback(current: int, total: int) -> None:
            if total <= 0:
                return
            percentage = current / total * 100
            now = time.monotonic()
            if (
                current < total
                and now - last_time[0] < 5.0
                and abs(percentage - last_percent[0]) < 2.0
            ):
                return
            percentage_display = f"{percentage:.1f}%"
            current_mb = current / (1024 * 1024)
            total_mb = total / (1024 * 1024)
            upload_text = (
                f"☁️ 上传中 {percentage_display}\n"
                f"💾 已上传: {current_mb:.1f} MB / {total_mb:.1f} MB"
            )
            # 仅覆盖最新文案并确保只有一个合并编辑任务在运行
            self._pending_upload_text = upload_text
            self._ensure_upload_edit_task()
            last_time[0] = now
            last_percent[0] = percentage

        return callback

    def _ensure_upload_edit_task(self) -> None:
        """若没有存活的上传编辑任务，则创建一个合并 worker。"""
        task = self._upload_edit_task
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 无运行中的事件循环，丢弃本次更新
        self._upload_edit_task = loop.create_task(
            self._run_upload_edit_worker()
        )

    async def _run_upload_edit_worker(self) -> None:
        """串行消费最新的上传进度文案，编辑期间到达的更新保留到下一轮。"""
        try:
            while True:
                text = self._pending_upload_text
                if text is None:
                    return
                # 先取走文案再发起编辑，保证更新不会丢失也不会重复发送
                self._pending_upload_text = None
                await self._telegram_service.edit_message(
                    chat_id=self._chat_id,
                    message_id=self._message_id,
                    text=text,
                    discardable=True,
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # 进度编辑失败不能影响下载/上传主流程
            logger.debug(f"Upload progress edit error: {e}")

    async def _flush_upload_progress_tasks(self) -> None:
        """丢弃尚未发出的进度文案，并等待进行中的编辑结束。

        在 on_success/on_error 前调用，防止过期进度覆盖最终消息。
        """
        self._pending_upload_text = None
        task = self._upload_edit_task
        if task is None or task.done():
            return
        logger.debug(
            f"Flushing pending upload progress edit for message "
            f"{self._message_id}"
        )
        await asyncio.gather(task, return_exceptions=True)

    async def on_success(
        self,
        storage_result: Dict[str, Any],
        download_type: str,
        is_twitter: bool = False,
        content_info: Optional[Dict[str, Any]] = None,
        send_as_new: bool = False,
    ) -> bool:
        # 确保所有 upload progress 任务已完成，防止它们覆盖成功消息
        await self._flush_upload_progress_tasks()
        # Playlist entries have a progress_label like "3/12" → send_as_new=True
        effective_send_as_new = send_as_new or bool(self._progress_label)
        if self._send_success:
            await self._send_success(
                self._chat_id,
                self._message_id,
                content_info or {},
                storage_result,
                download_type,
                is_twitter=is_twitter,
                send_as_new=effective_send_as_new,
            )
        return True

    async def on_error(
        self,
        error_message: str,
        is_twitter: bool = False,
        content_info: Optional[Dict[str, Any]] = None,
    ) -> bool:
        await self._flush_upload_progress_tasks()
        await self._telegram_service.edit_message(
            chat_id=self._chat_id,
            message_id=self._message_id,
            text=f"❌ {error_message}",
        )
        return False


class TelegramHandler:
    """Handles Telegram bot commands and interactions"""

    def __init__(
        self,
        telegram_service: TelegramService,
        storage_service: StorageService,
        download_service: DownloadService
    ):
        self.telegram_service = telegram_service
        self.storage_service = storage_service
        self.download_service = download_service
        self.user_states: Dict[int, Dict[str, Any]] = {}

        # 下载进度节流状态（方案A2）：
        # editMessageText 单 chat 上限约 1 次/秒，yt-dlp hook 频率约 1-10 次/秒，
        # 不节流会持续触发 Flood control。双阈值：距上次编辑 < 5s 或进度变化 < 2% 时跳过。
        self._progress_last_edit: Dict[int, float] = {}     # message_id -> monotonic 时间
        self._progress_last_percent: Dict[int, float] = {}  # message_id -> 百分比

        # UserStateManager will be set by cli.py to share the same instance
        self.state_manager: UserStateManager = UserStateManager()

    def setup_handlers(self):
        """Set up Telegram command and message handlers"""
        # Command handlers
        self.telegram_service.add_command_handler("start", self.start_command)
        self.telegram_service.add_command_handler("help", self.help_command)
        self.telegram_service.add_command_handler("cancel", self.cancel_command)
        self.telegram_service.add_command_handler("status", self.status_command)
        self.telegram_service.add_command_handler("storage", self.storage_status_command)

        # Message handlers
        self.telegram_service.add_message_handler(
            filters.TEXT & ~filters.COMMAND,
            self.handle_message
        )

        # Callback query handler for inline buttons
        self.telegram_service.add_callback_handler(self.callback_query_handler)

        # Error handler
        self.telegram_service.add_error_handler(self.error_handler)

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        chat_id = update.effective_chat.id

        if not self.telegram_service.check_user_permission(chat_id):
            await self.telegram_service.send_message(
                chat_id=chat_id,
                text="您没有权限使用此机器人。"
            )
            return

        welcome_message = (
            f"👋 您好，{user.first_name}！\n\n"
            f"我是YTBot，可以帮您下载各种平台的内容。\n\n"
            f"💡 使用提示：\n"
            f"• 发送YouTube视频链接\n"
            f"• 选择下载音频或视频\n"
            f"• 支持本地存储和Nextcloud\n\n"
            f"发送 /help 获取更多帮助信息。"
        )

        await self.telegram_service.send_message(
            chat_id=chat_id,
            text=welcome_message
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        chat_id = update.effective_chat.id

        help_message = (
            "📋 使用帮助\n\n"
            "🔹 基本功能：\n"
            "• 发送YouTube视频链接，选择下载类型\n"
            "• 音频文件将转换为MP3格式\n"
            "• 视频文件保持原始质量\n\n"
            "🔹 支持的命令：\n"
            "• /start - 开始使用机器人\n"
            "• /help - 显示此帮助信息\n"
            "• /cancel - 取消当前正在进行的所有下载任务\n"
            "• /status - 查看系统状态\n"
            "• /storage - 查看存储状态（管理员专用）\n\n"
            "🔹 注意事项：\n"
            "• 大文件下载可能需要较长时间\n"
            "• 受版权保护的视频可能无法下载\n"
            "• 年龄限制的视频可能无法下载\n"
            "• Nextcloud不可用时文件将保存到本地存储"
        )

        await self.telegram_service.send_message(
            chat_id=chat_id,
            text=help_message
        )

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        chat_id = update.effective_chat.id

        # Get system status via singleton factories (fixes reverse dep on YTBot)
        from ..monitoring.status_provider import build_status_report
        status = build_status_report()

        status_message = (
            "📊 系统状态报告\n\n"
            f"📱 Telegram: {'已连接' if status['telegram_connected'] else '未连接'}\n"
            f"☁️ Nextcloud: {'可用' if status['nextcloud_available'] else '不可用'}\n"
            f"💾 本地存储: {'已启用' if status['local_storage_enabled'] else '未启用'}\n"
            f"🎯 支持平台: {', '.join(status['supported_platforms'])}"
        )

        await self.telegram_service.send_message(
            chat_id=chat_id,
            text=status_message
        )

    async def storage_status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /storage command"""
        chat_id = update.effective_chat.id

        # Check admin permission
        admin_chat_id = get_config().telegram.admin_chat_id
        if str(chat_id) != str(admin_chat_id):
            await self.telegram_service.send_message(
                chat_id=chat_id,
                text="此命令仅限管理员使用。"
            )
            return

        # Get storage status
        storage_info = self.storage_service.get_storage_info()

        status_message = "📊 存储状态报告\n\n"

        # Nextcloud status
        if storage_info['nextcloud']['available']:
            status_message += "☁️ Nextcloud: ✅ 可用\n"
        else:
            status_message += "☁️ Nextcloud: ❌ 不可用\n"

        # Local storage status
        local_info = storage_info['local_storage']
        if local_info.get('enabled'):
            status_message += (
                f"\n💾 本地存储状态:\n"
                f"📁 存储路径: {local_info['path']}\n"
                f"📈 已用空间: {local_info['usage_mb']:.1f} MB\n"
                f"🆓 可用空间: {local_info['available_space_mb']:.1f} MB\n"
                f"📊 最大容量: {local_info['max_size_mb']} MB\n"
                f"🗓️ 文件保留: {local_info['cleanup_after_days']} 天\n"
            )
        else:
            status_message += "\n💾 本地存储: ❌ 未启用\n"

        # Work mode explanation
        status_message += "\n📋 当前工作模式:\n"
        if storage_info['nextcloud']['available']:
            status_message += "• 优先上传到Nextcloud\n"
            if local_info.get('enabled'):
                status_message += "• Nextcloud失败时自动切换到本地存储\n"
            else:
                status_message += "• Nextcloud失败时下载将失败\n"
        else:
            if local_info.get('enabled'):
                status_message += "• Nextcloud不可用，文件将保存到本地存储\n"
                status_message += f"• 本地文件将在 {local_info['cleanup_after_days']} 天后自动清理\n"
            else:
                status_message += "• 无可用存储，下载将失败\n"

        await self.telegram_service.send_message(
            chat_id=chat_id,
            text=status_message
        )

    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /cancel command"""
        chat_id = update.effective_chat.id

        # Cancel all downloads for this user
        # Implementation would depend on download tracking

        await self.telegram_service.send_message(
            chat_id=chat_id,
            text="🛑 已发出取消命令\n\n所有正在进行的下载任务将在适当时机停止。"
        )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle regular messages (URLs)"""
        chat_id = update.effective_chat.id
        message_text = update.message.text.strip()

        logger.info("=" * 50)
        logger.info("📩 Received Telegram message")
        logger.info("   Chat ID: %s", chat_id)
        logger.info("   Message length: %d chars", len(message_text))
        logger.info(
            "   Content: %s",
            message_text[:200] if len(message_text) > 200 else message_text
        )
        logger.info("=" * 50)

        if not self.telegram_service.check_user_permission(chat_id):
            await self.telegram_service.send_message(
                chat_id=chat_id,
                text="您没有权限使用此机器人。"
            )
            return

        # Check if user is in a specific state
        user_state = self.state_manager.get_user_state_enum(chat_id)

        if user_state == UserState.WAITING_DOWNLOAD_TYPE:
            # User is responding to download type selection
            await self._handle_download_type_response(chat_id, message_text)
            return

        if user_state == UserState.WAITING_CONFIRMATION:
            # User is responding to unsupported format confirmation
            await self._handle_unsupported_format_response(chat_id, message_text)
            return

        if user_state == UserState.WAITING_TEXT_CONFIRMATION:
            # User is responding to text content save confirmation
            await self._handle_text_save_response(chat_id, message_text)
            return

        if user_state == UserState.WAITING_LARGE_FILE_CONFIRMATION:
            # User is responding to large file download confirmation
            await self._handle_large_file_confirmation_response(chat_id, message_text)
            return

        # Check if it's a URL we can handle
        is_url = self._is_url(message_text)

        # Match handler only once and reuse the result
        handler = self.download_service.platform_manager.get_handler(message_text)
        can_handle = handler is not None

        logger.info("🔍 URL analysis results:")
        logger.info("   Is URL: %s", is_url)
        logger.info("   Can handle: %s", can_handle)

        if can_handle:
            logger.info("   Handler: %s", handler.name)
        elif is_url:
            supported = self.download_service.get_supported_platforms()
            logger.info("   Supported platforms: %s", supported)
            logger.info("   Result: URL is valid but no matching handler")

        if not can_handle:
            # Check if the message looks like a URL
            if is_url:
                # Ask user if they want to save the content
                await self._ask_save_unsupported_content(chat_id, message_text)
            else:
                # Not a URL, ask if user wants to save the text content
                await self._ask_save_text_content(chat_id, message_text)
            return

        # Handle the download request, passing the already-matched handler
        await self._handle_download_request(chat_id, message_text, handler)

    def _is_url(self, text: str) -> bool:
        """Check if text is a URL"""
        import re
        url_pattern = re.compile(
            r'^https?://'  # http:// or https://
            r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain
            r'localhost|'  # localhost
            r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # or ip
            r'(?::\d+)?'  # optional port
            r'(?:/?|[/?]\S+)$', re.IGNORECASE)
        return bool(url_pattern.match(text))

    async def _ask_save_unsupported_content(self, chat_id: int, url: str):
        """Ask user if they want to save unsupported URL content"""
        # Store state data
        self.state_manager.set_state(
            chat_id,
            UserState.WAITING_CONFIRMATION,
            {
                'url': url,
                'action': 'save_unsupported_content'
            }
        )

        # Create inline keyboard
        keyboard = [
            [
                InlineKeyboardButton("✅ 保存", callback_data="save_content_yes"),
                InlineKeyboardButton("❌ 忽略", callback_data="save_content_no")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        message_text = (
            f"⚠️ 检测到不支持的链接格式\n\n"
            f"🔗 {url[:80]}{'...' if len(url) > 80 else ''}\n\n"
            f"目前支持的平台：\n"
            f"{', '.join(self.download_service.get_supported_platforms())}\n\n"
            f"是否需要保存该链接的文本内容到本地？"
        )

        await self.telegram_service.send_message(
            chat_id=chat_id,
            text=message_text,
            reply_markup=reply_markup
        )

    async def _handle_unsupported_format_response(self, chat_id: int, message_text: str):
        """Handle user response for unsupported format"""
        # Get state data
        state_data = self.state_manager.get_state_data(chat_id)

        if not state_data:
            await self.telegram_service.send_message(
                chat_id=chat_id,
                text="❌ 会话已过期，请重新发送链接。"
            )
            return

        url = state_data.get('url')

        # Parse user input
        text_lower = message_text.lower()

        if any(word in text_lower for word in ['是', 'yes', 'y', '保存', 'save', '确认']):
            await self._save_unsupported_content(chat_id, url)
        elif any(word in text_lower for word in ['否', 'no', 'n', '忽略', 'ignore', '取消']):
            await self.telegram_service.send_message(
                chat_id=chat_id,
                text="已忽略该链接。"
            )
        else:
            await self.telegram_service.send_message(
                chat_id=chat_id,
                text="❌ 无效的选择，请输入 '是' 或 '否'。"
            )
            return

        # Clear state
        self.state_manager.clear_state(chat_id)

    async def _ask_save_text_content(self, chat_id: int, text: str):
        """Ask user if they want to save text content"""
        # Store state data
        self.state_manager.set_state(
            chat_id,
            UserState.WAITING_TEXT_CONFIRMATION,
            {
                'text_content': text,
                'action': 'save_text_content'
            }
        )

        # Create inline keyboard
        keyboard = [
            [
                InlineKeyboardButton("✅ 保存", callback_data="save_text_yes"),
                InlineKeyboardButton("❌ 忽略", callback_data="save_text_no")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Show preview of the text (first 100 characters)
        preview = text[:100] + "..." if len(text) > 100 else text

        message_text = (
            f"📝 检测到文本内容\n\n"
            f"预览：\n```\n{preview}\n```\n\n"
            f"是否需要保存这段文本内容到本地？"
        )

        await self.telegram_service.send_message(
            chat_id=chat_id,
            text=message_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    async def _handle_text_save_response(self, chat_id: int, message_text: str):
        """Handle user response for text content save"""
        # Get state data
        state_data = self.state_manager.get_state_data(chat_id)

        if not state_data:
            await self.telegram_service.send_message(
                chat_id=chat_id,
                text="❌ 会话已过期，请重新发送内容。"
            )
            return

        text_content = state_data.get('text_content')

        # Parse user input
        text_lower = message_text.lower()

        if any(word in text_lower for word in ['是', 'yes', 'y', '保存', 'save', '确认']):
            await self._save_text_content(chat_id, text_content)
        elif any(word in text_lower for word in ['否', 'no', 'n', '忽略', 'ignore', '取消']):
            await self.telegram_service.send_message(
                chat_id=chat_id,
                text="已忽略该内容。"
            )
        else:
            await self.telegram_service.send_message(
                chat_id=chat_id,
                text="❌ 无效的选择，请输入 '是' 或 '否'。"
            )
            return

        # Clear state
        self.state_manager.clear_state(chat_id)

    async def _save_text_content(self, chat_id: int, text: str):
        """Save text content to HTML file"""
        try:
            from datetime import datetime
            # Get downloads directory
            downloads_dir = get_config().local_storage.path
            os.makedirs(downloads_dir, exist_ok=True)

            # Generate filename with timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"text_content_{timestamp}.html"
            filepath = os.path.join(downloads_dir, filename)

            # Convert text to HTML with proper formatting
            html_content = self._generate_text_html(text, timestamp)

            # Save to HTML file
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(html_content)

            await self.telegram_service.send_message(
                chat_id=chat_id,
                text=f"✅ 已保存文本内容到文件：\n`{filename}`",
                parse_mode='Markdown'
            )

        except Exception as e:
            logger.error(f"Failed to save text content: {e}")
            await self.telegram_service.send_message(
                chat_id=chat_id,
                text=f"❌ 保存失败：{str(e)}"
            )

    def _generate_text_html(self, text: str, timestamp: str) -> str:
        """Generate HTML content from text"""
        import re
        from datetime import datetime

        # Escape HTML special characters
        import html
        escaped_text = html.escape(text)

        # Convert URLs to clickable links
        url_pattern = r'(https?://[^\s<>"\']+)'
        escaped_text = re.sub(
            url_pattern,
            r'<a href="\1" target="_blank">\1</a>',
            escaped_text
        )

        # Convert line breaks to <br> tags
        escaped_text = escaped_text.replace('\n', '<br>\n')

        # Format the timestamp
        formatted_time = datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')

        html_template = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>保存的文本内容</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                         "Helvetica Neue", Arial, sans-serif;
            line-height: 1.8;
            color: #333;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }}

        .container {{
            max-width: 800px;
            margin: 0 auto;
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            overflow: hidden;
        }}

        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }}

        .header h1 {{
            font-size: 24px;
            font-weight: 600;
            margin-bottom: 10px;
        }}

        .header .timestamp {{
            font-size: 14px;
            opacity: 0.9;
        }}

        .content {{
            padding: 40px;
            font-size: 16px;
            line-height: 1.8;
        }}

        .content a {{
            color: #667eea;
            text-decoration: none;
            border-bottom: 1px solid #667eea;
        }}

        .content a:hover {{
            background: rgba(102, 126, 234, 0.1);
        }}

        .content br {{
            display: block;
            margin: 8px 0;
        }}

        .footer {{
            background: #f8f9fa;
            padding: 20px 40px;
            text-align: center;
            color: #666;
            font-size: 14px;
            border-top: 1px solid #e9ecef;
        }}

        .word-count {{
            display: inline-block;
            background: #667eea;
            color: white;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            margin-top: 10px;
        }}

        @media (max-width: 600px) {{
            body {{
                padding: 10px;
            }}

            .header {{
                padding: 20px;
            }}

            .header h1 {{
                font-size: 20px;
            }}

            .content {{
                padding: 20px;
                font-size: 15px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📝 保存的文本内容</h1>
            <div class="timestamp">保存时间：{formatted_time}</div>
            <div class="word-count">共 {len(text)} 个字符</div>
        </div>
        <div class="content">
            {escaped_text}
        </div>
        <div class="footer">
            由 YTBot 自动保存
        </div>
    </div>
</body>
</html>'''

        return html_template

    async def _save_unsupported_content(self, chat_id: int, url: str):
        """Save unsupported URL content to JSON file"""
        try:
            import json
            from datetime import datetime
            # Prepare data to save
            data = {
                'url': url,
                'saved_at': datetime.now().isoformat(),
                'chat_id': chat_id,
                'status': 'unsupported_format'
            }

            # Get downloads directory
            downloads_dir = get_config().local_storage.path
            os.makedirs(downloads_dir, exist_ok=True)

            # Generate filename with timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"unsupported_content_{timestamp}.json"
            filepath = os.path.join(downloads_dir, filename)

            # Save to JSON file
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            await self.telegram_service.send_message(
                chat_id=chat_id,
                text=f"✅ 已保存链接信息到文件：\n`{filename}`",
                parse_mode='Markdown'
            )

        except Exception as e:
            logger.error(f"Failed to save unsupported content: {e}")
            await self.telegram_service.send_message(
                chat_id=chat_id,
                text=f"❌ 保存失败：{str(e)}"
            )

    async def _handle_download_request(
        self,
        chat_id: int,
        url: str,
        handler=None
    ):
        """Handle a download request from user"""
        try:
            # Send initial message
            progress_message = await self.telegram_service.send_message(
                chat_id=chat_id,
                text="🔍 正在获取内容信息..."
            )

            # Log URL being processed
            logger.info("🔗 Processing URL: %s", url)

            # Use the already-matched handler if provided
            if handler is None:
                handler = self.download_service.platform_manager.get_handler(url)

            if handler:
                logger.info("✅ Link type identified: %s", handler.name)
            else:
                logger.warning("⚠️ No handler found for URL: %s", url)

            # Get content info
            content_info = await self.download_service.get_content_info(url)

            if content_info:
                logger.info("✅ Content info retrieved successfully")
                logger.info("   - Title: %s", content_info.get('title', 'N/A'))
                logger.info("   - Type: %s", content_info.get('content_type', 'N/A'))
                logger.info("   - Duration: %s seconds", content_info.get('duration', 'N/A'))
                logger.info("   - Uploader: %s", content_info.get('uploader', 'N/A'))
            else:
                logger.warning("⚠️ Failed to get content info for URL: %s", url)

            if not content_info:
                await self.telegram_service.edit_message(
                    chat_id=chat_id,
                    message_id=progress_message['message_id'],
                    text="❌ 无法获取内容信息，请检查链接是否有效。"
                )
                return

            if content_info.get('error_detail') and content_info.get('title') == 'Unknown':
                error_code = content_info.get('error_detail')
                if handler and handler.name == "YouTube":
                    error_message = handler.get_error_message(error_code)
                elif handler and handler.name == "Twitter/X":
                    twitter_error_messages = {
                        "ERROR_LOGIN_FAILED": "🔐 Twitter/X 登录失败，可能需要重新登录",
                        "ERROR_RATE_LIMITED": "⏳ Twitter/X 请求过于频繁，请稍后重试",
                        "ERROR_TWEET_NOT_FOUND": "❌ 推文不存在或已被删除",
                        "ERROR_ACCOUNT_SUSPENDED": "🚫 账号已被封禁",
                        "ERROR_TWEET_PROTECTED": "🔒 推文来自私密账号，无法访问",
                    }
                    error_message = twitter_error_messages.get(
                        error_code,
                        "❌ 获取推文内容时发生错误"
                    )
                else:
                    error_messages = {
                        "ERROR_SIGN_IN_REQUIRED": "🔐 需要登录或更新cookies",
                        "ERROR_AGE_RESTRICTED": "🔞 年龄限制视频",
                        "ERROR_COPYRIGHT": "©️ 版权限制",
                        "ERROR_VIDEO_UNAVAILABLE": "❌ 视频不可用",
                        "ERROR_VIDEO_PRIVATE": "🔒 私有视频",
                        "ERROR_VIDEO_REMOVED": "🗑️ 视频已删除",
                        "ERROR_GEO_RESTRICTED": "🌍 地区限制",
                        "ERROR_RENTAL_PURCHASE": "💰 需要购买或租借",
                        "ERROR_LOGIN_REQUIRED": "🔐 需要登录",
                    }
                    error_message = error_messages.get(
                        error_code,
                        "❌ 获取内容信息时发生错误"
                    )
                await self.telegram_service.edit_message(
                    chat_id=chat_id,
                    message_id=progress_message['message_id'],
                    text=error_message
                )
                return

            if handler and handler.name == "YouTube":
                # For YouTube, ask user to select download type
                await self._ask_download_type(
                    chat_id, progress_message['message_id'], url, content_info
                )
            elif handler and handler.name == "Twitter/X":
                # For Twitter/X, show friendly preview and download as text content
                await self._handle_twitter_download(
                    chat_id,
                    progress_message['message_id'],
                    url,
                    handler,
                    content_info=content_info
                )
            else:
                # For other platforms, download as video by default
                await self._proceed_with_download(
                    chat_id, progress_message['message_id'], url, "video", None
                )

        except Exception as e:
            logger.error(f"Download request handling error: {e}")
            await self.telegram_service.send_message(
                chat_id=chat_id,
                text="❌ 处理请求时发生错误，请稍后重试。"
            )

    async def _handle_twitter_download(
        self,
        chat_id: int,
        message_id: int,
        url: str,
        handler,
        content_info: Optional[Dict[str, Any]] = None
    ):
        """
        Handle Twitter/X download with friendly preview message.

        Args:
            chat_id: Telegram chat ID
            message_id: Message ID to update
            url: Twitter/X URL
            handler: Twitter handler instance
            content_info: Optional pre-fetched content info to avoid re-scraping
        """
        try:
            await self.telegram_service.edit_message(
                chat_id=chat_id,
                message_id=message_id,
                text="🔍 正在分析 Twitter/X 内容..."
            )

            # Reuse content_info if available to avoid redundant browser access
            if content_info and content_info.get('success'):
                result = content_info
                logger.info("♻️ Reusing pre-fetched content info (skipped redundant scrape)")
            else:
                from ..platforms.twitter import TwitterContentExtractor

                extractor = TwitterContentExtractor()
                result = await extractor.scrape_tweet(url)

                if not result.get('success'):
                    error_msg = result.get('error', '')
                    twitter_error_messages = {
                        "ERROR_LOGIN_FAILED": "🔐 Twitter/X 登录失败，可能需要重新登录",
                        "ERROR_RATE_LIMITED": "⏳ Twitter/X 请求过于频繁，请稍后重试",
                        "ERROR_TWEET_NOT_FOUND": "❌ 推文不存在或已被删除",
                        "ERROR_ACCOUNT_SUSPENDED": "🚫 账号已被封禁",
                        "ERROR_TWEET_PROTECTED": "🔒 推文来自私密账号，无法访问",
                    }
                    error_code = handler._parse_twitter_error(
                        error_msg
                    )
                    error_text = twitter_error_messages.get(
                        error_code,
                        "❌ 无法获取推文内容，请检查链接是否有效。"
                    )
                    await self.telegram_service.edit_message(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=error_text
                    )
                    await extractor.close_browser()
                    return

                await extractor.close_browser()

            preview_text = handler.generate_telegram_preview(result, is_processing=True)
            await self.telegram_service.edit_message(
                chat_id=chat_id,
                message_id=message_id,
                text=preview_text
            )

            await self._proceed_with_download(
                chat_id, message_id, url, "text", result, handler=handler
            )

        except Exception as e:
            logger.error(f"Twitter download handling error: {e}")
            await self.telegram_service.edit_message(
                chat_id=chat_id,
                message_id=message_id,
                text=f"❌ 处理 Twitter/X 链接时出错: {str(e)}"
            )

    async def _ask_download_type(
        self,
        chat_id: int,
        message_id: int,
        url: str,
        content_info: Dict[str, Any]
    ):
        """
        Ask user to select download type (audio or video).

        Args:
            chat_id: Telegram chat ID
            message_id: Message ID to edit
            url: Video URL
            content_info: Content information dictionary
        """
        # Store state data
        self.state_manager.set_state(
            chat_id,
            UserState.WAITING_DOWNLOAD_TYPE,
            {
                'url': url,
                'content_info': content_info,
                'message_id': message_id
            }
        )

        # Create inline keyboard
        keyboard = [
            [
                InlineKeyboardButton("🎵 音频 (MP3)", callback_data="download_audio"),
                InlineKeyboardButton("🎬 视频 (MP4)", callback_data="download_video")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Prepare video info text
        duration_str = ""
        if content_info.get('duration'):
            minutes = content_info['duration'] // 60
            seconds = content_info['duration'] % 60
            duration_str = f"⏱ 时长: {minutes}:{seconds:02d}\n"

        uploader_str = ""
        if content_info.get('uploader'):
            uploader_str = f"👤 上传者: {content_info['uploader']}\n"

        message_text = (
            f"📹 视频信息\n\n"
            f"📌 标题: {content_info['title']}\n"
            f"{duration_str}"
            f"{uploader_str}\n"
            f"请选择下载类型："
        )

        # Edit message with inline keyboard（走统一接口以获得熔断保护）
        await self.telegram_service.edit_message(
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            reply_markup=reply_markup
        )

    async def callback_query_handler(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle callback queries from inline buttons"""
        query = update.callback_query
        chat_id = query.message.chat_id

        # Acknowledge the callback
        await query.answer()

        # Check permission
        if not self.telegram_service.check_user_permission(chat_id):
            await query.edit_message_text("您没有权限使用此机器人。")
            return

        # Get callback data
        callback_data = query.data

        # Handle download type selection
        if callback_data in ['download_audio', 'download_video']:
            await self._handle_download_type_callback(chat_id, callback_data, query)

        # Handle unsupported content save confirmation
        if callback_data in ['save_content_yes', 'save_content_no']:
            await self._handle_save_content_callback(chat_id, callback_data, query)

        # Handle text content save confirmation
        if callback_data in ['save_text_yes', 'save_text_no']:
            await self._handle_save_text_callback(chat_id, callback_data, query)

        # Handle large file download confirmation
        if callback_data in ['confirm_download_yes', 'confirm_download_no']:
            await self._handle_large_file_confirmation_callback(chat_id, callback_data, query)

    async def _handle_download_type_callback(
        self,
        chat_id: int,
        callback_data: str,
        query
    ):
        """
        Handle download type selection from callback.

        Args:
            chat_id: Telegram chat ID
            callback_data: Callback data string
            query: Callback query object
        """
        # Get state data
        state_data = self.state_manager.get_state_data(chat_id)

        if not state_data:
            await query.edit_message_text(
                "❌ 会话已过期，请重新发送链接。"
            )
            return

        url = state_data.get('url')
        content_info = state_data.get('content_info')
        message_id = state_data.get('message_id')

        # Determine download type
        download_type = "audio" if callback_data == "download_audio" else "video"

        # Clear state
        self.state_manager.clear_state(chat_id)

        # Proceed with download
        await self._proceed_with_download(
            chat_id, message_id, url, download_type, content_info
        )

    async def _handle_save_content_callback(
        self,
        chat_id: int,
        callback_data: str,
        query
    ):
        """
        Handle save content confirmation from callback.

        Args:
            chat_id: Telegram chat ID
            callback_data: Callback data string
            query: Callback query object
        """
        # Get state data
        state_data = self.state_manager.get_state_data(chat_id)

        if not state_data:
            await query.edit_message_text(
                "❌ 会话已过期，请重新发送链接。"
            )
            return

        url = state_data.get('url')

        if callback_data == "save_content_yes":
            # Save the content
            await query.edit_message_text("💾 正在保存链接信息...")
            await self._save_unsupported_content(chat_id, url)
        else:
            await query.edit_message_text("❌ 已忽略该链接。")

        # Clear state
        self.state_manager.clear_state(chat_id)

    async def _handle_save_text_callback(
        self,
        chat_id: int,
        callback_data: str,
        query
    ):
        """
        Handle save text content confirmation from callback.

        Args:
            chat_id: Telegram chat ID
            callback_data: Callback data string
            query: Callback query object
        """
        # Get state data
        state_data = self.state_manager.get_state_data(chat_id)

        if not state_data:
            await query.edit_message_text(
                "❌ 会话已过期，请重新发送内容。"
            )
            return

        text_content = state_data.get('text_content')

        if callback_data == "save_text_yes":
            # Save the content
            await query.edit_message_text("💾 正在保存文本内容...")
            await self._save_text_content(chat_id, text_content)
        else:
            await query.edit_message_text("❌ 已忽略该内容。")

        # Clear state
        self.state_manager.clear_state(chat_id)

    async def _handle_download_type_response(self, chat_id: int, message_text: str):
        """
        Handle text response for download type selection.

        Args:
            chat_id: Telegram chat ID
            message_text: User's message text
        """
        # Get state data
        state_data = self.state_manager.get_state_data(chat_id)

        if not state_data:
            await self.telegram_service.send_message(
                chat_id=chat_id,
                text="❌ 会话已过期，请重新发送链接。"
            )
            return

        # Parse user input
        text_lower = message_text.lower()

        if '音频' in text_lower or 'audio' in text_lower or 'mp3' in text_lower:
            download_type = "audio"
        elif '视频' in text_lower or 'video' in text_lower or 'mp4' in text_lower:
            download_type = "video"
        else:
            await self.telegram_service.send_message(
                chat_id=chat_id,
                text="❌ 无效的选择，请回复 '音频' 或 '视频'。"
            )
            return

        url = state_data.get('url')
        content_info = state_data.get('content_info')
        message_id = state_data.get('message_id')

        # Clear state
        self.state_manager.clear_state(chat_id)

        # Proceed with download
        await self._proceed_with_download(
            chat_id, message_id, url, download_type, content_info
        )

    async def _proceed_with_download(
        self,
        chat_id: int,
        message_id: int,
        url: str,
        download_type: str,
        content_info: Optional[Dict[str, Any]] = None,
        handler=None,
        progress_label: Optional[str] = None,
        skip_large_file_confirmation: bool = False,
        inherited_format_id: Optional[str] = None,
        archive_mode: bool = False
    ) -> bool:
        """Proceed with the actual download.

        Delegates format selection, download, format fallback, store,
        and success/error messaging to ``DownloadPipeline``.

        Keeps Bot-specific interaction logic:
        - Playlist expansion and recursive processing
        - Twitter preview messages
        - Large-file confirmation dialog
        - Cleanup (throttle state + temp dir)
        """
        try:
            # Resolve handler
            if handler is None:
                handler = self.download_service.platform_manager.get_handler(url)

            if not handler:
                await self.telegram_service.edit_message(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="❌ 无法处理此链接。"
                )
                return False

            # ── Playlist expansion (Bot-specific) ──────────────────
            if handler.name == "YouTube" and handler.is_playlist(url):
                entries = await handler.get_playlist_entries(url)
                if entries is None:
                    logger.warning(
                        f"Failed to expand playlist, falling back to "
                        f"whole-playlist download: {url}"
                    )
                elif not entries:
                    await self.telegram_service.edit_message(
                        chat_id=chat_id,
                        message_id=message_id,
                        text="❌ 播放列表为空或没有可下载的视频。"
                    )
                    return False
                else:
                    total = len(entries)
                    await self.telegram_service.edit_message(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=(
                            f"📋 检测到播放列表，共 {total} 个视频，"
                            f"将逐个下载并上传..."
                        )
                    )

                    # One-time format selection; all entries inherit
                    _, first_formats = await handler.get_format_list(
                        entries[0]
                    )
                    shared_format_id = None
                    if download_type == "audio":
                        shared_format_id = (
                            handler.select_best_audio_format(first_formats)
                        )
                    else:
                        vfmt = handler.select_best_video_format(
                            first_formats
                        )
                        afmt = handler.select_best_audio_format(
                            first_formats
                        )
                        if vfmt and afmt:
                            shared_format_id = f"{vfmt}+{afmt}"
                        elif vfmt:
                            shared_format_id = vfmt
                    if shared_format_id:
                        logger.info(
                            f"Playlist format selection: {shared_format_id} "
                            f"(inherited by {total} entries)"
                        )

                    succeeded = 0
                    failed = 0
                    for i, entry_url in enumerate(entries, 1):
                        item_ok = await self._proceed_with_download(
                            chat_id,
                            message_id,
                            entry_url,
                            download_type,
                            None,
                            handler,
                            progress_label=f"{i}/{total}",
                            skip_large_file_confirmation=True,
                            inherited_format_id=shared_format_id,
                            archive_mode=archive_mode,
                        )
                        if item_ok:
                            succeeded += 1
                        else:
                            failed += 1
                    await self.telegram_service.send_message(
                        chat_id=chat_id,
                        text=(
                            f"📋 播放列表处理完成："
                            f"成功 {succeeded} / 失败 {failed} / 共 {total}"
                        )
                    )
                    return failed == 0

            # ── Twitter status message ────────────────────────────
            is_twitter = handler.name == "Twitter/X"
            twitter_result = (
                content_info if is_twitter and content_info
                and content_info.get('success') else None
            )
            if is_twitter:
                if twitter_result:
                    await self.telegram_service.edit_message(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=handler.generate_telegram_preview(
                            twitter_result,
                            is_processing=True,
                            processing_state="⬇️ 正在保存到本地..."
                        ),
                    )
                else:
                    await self.telegram_service.edit_message(
                        chat_id=chat_id,
                        message_id=message_id,
                        text="⬇️ 正在获取推文...",
                    )
            else:
                suffix = f" ({progress_label})" if progress_label else ""
                label = ("音频" if download_type == "audio" else "视频")
                await self.telegram_service.edit_message(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"⬇️ 正在下载{label}{suffix}...",
                )

            # ── Large-file confirmation (Bot-only) ────────────────
            if not skip_large_file_confirmation and not is_twitter:
                _, formats = await handler.get_format_list(url)
                file_size_estimate = None
                if download_type == "audio":
                    fid = handler.select_best_audio_format(formats)
                    file_size_estimate = estimate_format_size(
                        formats, fid
                    )
                else:
                    vf = handler.select_best_video_format(formats)
                    file_size_estimate = estimate_format_size(
                        formats, vf
                    )
                if (
                    file_size_estimate is not None
                    and file_size_estimate > LARGE_FILE_THRESHOLD
                ):
                    logger.info(
                        f"Large file detected: "
                        f"{file_size_estimate / 1024 / 1024:.1f} MB"
                    )
                    await self._ask_large_file_confirmation(
                        chat_id, message_id, url, download_type,
                        content_info, file_size_estimate, None
                    )
                    return False

            # ── Pipeline: download → store → success/error ────────
            pipeline = DownloadPipeline(
                download_service=self.download_service,
                storage_service=self.storage_service,
                progress_sink=self._make_progress_sink(
                    chat_id, message_id,
                    is_twitter=is_twitter,
                    twitter_result=twitter_result,
                    progress_label=progress_label,
                ),
                archive_mode=archive_mode,
            )
            result = await pipeline.run(
                url=url,
                download_type=download_type,
                handler=handler,
                content_info=content_info,
                inherited_format_id=inherited_format_id,
                progress_label=progress_label,
            )
            # Pipeline returns True/False; store result for cleanup
            if result:
                # Success: get file_path for cleanup (re-fetch from storage)
                return True
            return False

        except Exception as e:
            logger.error(f"Download error: {e}")
            await self.telegram_service.edit_message(
                chat_id=chat_id,
                message_id=message_id,
                text=f"❌ 下载过程中发生错误: {str(e)}"
            )
            return False
        finally:
            # Clean up progress throttle state
            self._progress_last_edit.pop(message_id, None)
            self._progress_last_percent.pop(message_id, None)

    async def _update_download_progress(
        self,
        chat_id: int,
        message_id: int,
        progress_data: Dict[str, Any]
    ):
        """
        Update download progress message.

        Args:
            chat_id: Telegram chat ID
            message_id: Message ID to update
            progress_data: Progress data from yt-dlp
        """
        try:
            status = progress_data.get('status')

            if status == 'downloading':
                # Get progress info
                downloaded = progress_data.get('downloaded_bytes', 0)
                total = progress_data.get('total_bytes') or progress_data.get(
                    'total_bytes_estimate', 0
                )
                speed = progress_data.get('speed', 0)

                if total > 0:
                    percentage = (downloaded / total) * 100

                    # 节流（方案A2）：距上次成功编辑不足 5 秒，或进度变化不足 2% 时跳过。
                    # 仅在编辑成功后更新时间戳，熔断期丢弃后能立即恢复。
                    now = time.monotonic()
                    if now - self._progress_last_edit.get(message_id, 0.0) < 5.0:
                        return
                    if abs(percentage - self._progress_last_percent.get(message_id, -100.0)) < 2.0:
                        return

                    downloaded_mb = downloaded / (1024 * 1024)
                    total_mb = total / (1024 * 1024)

                    speed_str = ""
                    if speed:
                        speed_mbps = speed / (1024 * 1024)
                        speed_str = f" ({speed_mbps:.1f} MB/s)"

                    progress_text = (
                        f"⬇️ 下载中...\n"
                        f"📊 进度: {percentage:.1f}%\n"
                        f"💾 已下载: {downloaded_mb:.1f} MB / {total_mb:.1f} MB"
                        f"{speed_str}"
                    )

                    # discardable=True：flood 熔断窗口内直接丢弃本次进度编辑，
                    # 避免重试使封锁续期（最终结果消息不受影响）
                    result = await self.telegram_service.edit_message(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=progress_text,
                        discardable=True
                    )
                    if result is not None:
                        self._progress_last_edit[message_id] = now
                        self._progress_last_percent[message_id] = percentage
        except Exception as e:
            # Don't fail the download if progress update fails
            logger.debug(f"Progress update error: {e}")

    async def _send_success_message(
        self,
        chat_id: int,
        message_id: int,
        content_info: Dict[str, Any],
        storage_result: Dict[str, Any],
        download_type: str = "video",
        is_twitter: bool = False,
        send_as_new: bool = False
    ):
        """
        Send success message to user.

        Args:
            chat_id: Telegram chat ID
            message_id: Message ID to edit (ignored when send_as_new=True)
            content_info: Content information dictionary
            storage_result: Storage result dictionary
            download_type: "audio", "video", "image", or "text"
            is_twitter: Whether this is a Twitter/X download
            send_as_new: Send as a new message instead of editing
                (playlist entries keep their own result message)
        """
        if is_twitter and content_info.get('success'):
            # Build display title from Twitter scrape result
            author = content_info.get('author', '')
            full_text = content_info.get('full_text', '')
            if author:
                display_title = f"{author}: {full_text[:50]}"
            elif full_text:
                display_title = full_text[:60]
            else:
                display_title = "推文内容"

            if storage_result.get('storage_type') == 'nextcloud':
                lines = [
                    "🐦 Twitter/X 内容",
                    "",
                    "✅ 已保存到 Nextcloud",
                    f"📁 文件: {display_title}",
                    f"🔗 访问链接: {storage_result.get('file_url', '')}",
                ]
            else:
                lines = [
                    "🐦 Twitter/X 内容",
                    "",
                    "✅ 已保存到本地",
                    f"📁 文件: {display_title}",
                    f"💾 保存位置: {storage_result.get('file_path', '本地存储')}",
                ]
            message = "\n".join(lines)
        else:
            type_emoji = "🎵" if download_type == "audio" else "🎬"
            type_name = "音频" if download_type == "audio" else "视频"

            if storage_result['storage_type'] == 'nextcloud':
                message = (
                    f"✅ {type_name}下载和上传完成！\n\n"
                    f"📁 文件: {content_info.get('title', 'Unknown')}\n"
                    f"{type_emoji} 类型: {type_name}\n"
                    f"🔗 访问链接: {storage_result['file_url']}"
                )
            else:
                message = (
                    f"⚠️ Nextcloud上传失败，文件已保存到本地存储\n\n"
                    f"📁 文件: {content_info.get('title', 'Unknown')}\n"
                    f"{type_emoji} 类型: {type_name}\n"
                    f"💾 本地路径: {storage_result['file_path']}"
                )

        if send_as_new:
            await self.telegram_service.send_message(
                chat_id=chat_id,
                text=message
            )
        else:
            await self.telegram_service.edit_message(
                chat_id=chat_id,
                message_id=message_id,
                text=message
            )

    async def _handle_large_file_confirmation_response(
        self, chat_id: int, message_text: str
    ):
        """Handle user response for large file download confirmation"""
        state_data = self.state_manager.get_state_data(chat_id)

        if not state_data:
            await self.telegram_service.send_message(
                chat_id=chat_id,
                text="❌ 会话已过期，请重新发送链接。"
            )
            return

        text_lower = message_text.lower()

        if any(word in text_lower for word in ['是', 'yes', 'y', '确认', '确定', 'continue']):
            await self._proceed_with_large_file_download(chat_id, state_data)
        elif any(word in text_lower for word in ['否', 'no', 'n', '取消', 'ignore', 'stop']):
            await self.telegram_service.send_message(
                chat_id=chat_id,
                text="❌ 已取消大文件下载。"
            )
        else:
            await self.telegram_service.send_message(
                chat_id=chat_id,
                text="❌ 无效的选择，请输入 '是' 或 '否'。"
            )
            return

        self.state_manager.clear_state(chat_id)

    async def _handle_large_file_confirmation_callback(
        self, chat_id: int, callback_data: str, query
    ):
        """Handle large file confirmation from inline buttons"""
        state_data = self.state_manager.get_state_data(chat_id)

        if not state_data:
            await query.edit_message_text(
                "❌ 会话已过期，请重新发送链接。"
            )
            return

        if callback_data == "confirm_download_yes":
            await query.edit_message_text("⬇️ 正在下载大文件...")
            await self._proceed_with_large_file_download(chat_id, state_data)
        else:
            await query.edit_message_text("❌ 已取消大文件下载。")

        self.state_manager.clear_state(chat_id)

    async def _ask_large_file_confirmation(
        self,
        chat_id: int,
        message_id: int,
        url: str,
        download_type: str,
        content_info: Optional[Dict[str, Any]],
        file_size: int,
        format_id: Optional[str]
    ):
        """Ask user to confirm large file download"""
        self.state_manager.set_state(
            chat_id,
            UserState.WAITING_LARGE_FILE_CONFIRMATION,
            {
                'url': url,
                'download_type': download_type,
                'content_info': content_info,
                'file_size': file_size,
                'format_id': format_id,
                'message_id': message_id
            }
        )

        file_size_mb = file_size / (1024 * 1024)
        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ 确认下载",
                    callback_data="confirm_download_yes"
                ),
                InlineKeyboardButton(
                    "❌ 取消",
                    callback_data="confirm_download_no"
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        message_text = (
            f"⚠️ 警告：文件较大\n\n"
            f"📹 视频: {content_info.get('title', 'Unknown')[:50]}...\n"
            f"💾 预计大小: {file_size_mb:.1f} MB\n\n"
            f"下载此视频可能需要较长时间，是否确认下载？"
        )

        await self.telegram_service.edit_message(
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            reply_markup=reply_markup
        )

    async def _proceed_with_large_file_download(
        self, chat_id: int, state_data: Dict[str, Any]
    ):
        """Proceed with large file download after user confirmation."""
        url = state_data['url']
        download_type = state_data['download_type']
        content_info = state_data['content_info']
        format_id = state_data['format_id']
        message_id = state_data['message_id']

        handler = self.download_service.platform_manager.get_handler(url)
        if not handler:
            await self.telegram_service.edit_message(
                chat_id=chat_id,
                message_id=message_id,
                text="❌ 无法处理此链接。"
            )
            return

        await self.telegram_service.edit_message(
            chat_id=chat_id,
            message_id=message_id,
            text="⬇️ 正在下载大文件...",
        )

        pipeline = DownloadPipeline(
            download_service=self.download_service,
            storage_service=self.storage_service,
            progress_sink=self._make_progress_sink(
                chat_id, message_id,
                progress_label="大文件",
            ),
        )
        await pipeline.run(
            url=url,
            download_type=download_type,
            handler=handler,
            content_info=content_info,
            inherited_format_id=format_id,
        )

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Telegram error: {context.error}")

        # 网络类/限流类错误（方案A3）：此时再向用户发通知只会增加 API 负载、
        # 加剧 Flood control（且大概率同样失败），只记日志即可。
        if isinstance(context.error, (RetryAfter, NetworkError)):
            logger.debug(
                "Network/rate-limit error - skipping user notification to avoid "
                "worsening flood control"
            )
            return

        if update and update.effective_chat:
            await self.telegram_service.send_message(
                chat_id=update.effective_chat.id,
                text="抱歉，处理您的请求时发生错误。请稍后重试。"
            )

    # ------------------------------------------------------------------
    # Download pipeline integration
    # ------------------------------------------------------------------

    def _make_progress_sink(
        self,
        chat_id: int,
        message_id: int,
        is_twitter: bool = False,
        twitter_result: Optional[Dict[str, Any]] = None,
        progress_label: Optional[str] = None,
    ) -> ProgressSink:
        """Create a ProgressSink that emits Telegram messages.

        This lets ``DownloadPipeline.run()`` delegate to this handler's
        existing progress-edit and success-message logic.
        """
        return TelegramProgressSink(
            telegram_service=self.telegram_service,
            chat_id=chat_id,
            message_id=message_id,
            is_twitter=is_twitter,
            twitter_result=twitter_result,
            progress_label=progress_label,
            update_progress_callback=self._update_download_progress,
            send_success_callback=self._send_success_message,
        )

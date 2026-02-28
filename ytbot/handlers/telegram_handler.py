"""
Telegram handler for YTBot

Handles Telegram bot commands and message processing.
"""

from typing import Dict, Any, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, filters

from ..core.config import CONFIG
from ..core.logger import get_logger
from ..core.user_state import UserStateManager, UserState
from ..services.telegram_service import TelegramService
from ..services.storage_service import StorageService
from ..services.download_service import DownloadService

logger = get_logger(__name__)


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

        # Initialize user state manager
        self.state_manager = UserStateManager(
            timeout=CONFIG['monitor']['user_state_timeout']
        )

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

        # Get system status
        from ..cli import YTBot
        bot = YTBot()
        status = bot.get_status()

        status_message = (
            "📊 系统状态报告\n\n"
            f"🤖 Bot状态: {'运行中' if status['running'] else '已停止'}\n"
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
        admin_chat_id = CONFIG['telegram']['admin_chat_id']
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

        # Check if it's a URL we can handle
        if not self.download_service.can_handle_url(message_text):
            # Check if the message looks like a URL
            if self._is_url(message_text):
                # Ask user if they want to save the content
                await self._ask_save_unsupported_content(chat_id, message_text)
            else:
                # Not a URL, ask if user wants to save the text content
                await self._ask_save_text_content(chat_id, message_text)
            return

        # Handle the download request
        await self._handle_download_request(chat_id, message_text)

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
            import os
            from datetime import datetime
            from ..core.config import CONFIG

            # Get downloads directory
            downloads_dir = CONFIG['local_storage']['path']
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
            import os
            from datetime import datetime
            from ..core.config import CONFIG

            # Prepare data to save
            data = {
                'url': url,
                'saved_at': datetime.now().isoformat(),
                'chat_id': chat_id,
                'status': 'unsupported_format'
            }

            # Get downloads directory
            downloads_dir = CONFIG['local_storage']['path']
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

    async def _handle_download_request(self, chat_id: int, url: str):
        """Handle a download request from user"""
        try:
            # Send initial message
            progress_message = await self.telegram_service.send_message(
                chat_id=chat_id,
                text="🔍 正在获取内容信息..."
            )

            # Get content info
            content_info = await self.download_service.get_content_info(url)

            if not content_info:
                await self.telegram_service.edit_message(
                    chat_id=chat_id,
                    message_id=progress_message['message_id'],
                    text="❌ 无法获取内容信息，请检查链接是否有效。"
                )
                return

            # Get platform handler to check platform type
            handler = self.download_service.platform_manager.get_handler(url)

            if handler and handler.name == "YouTube":
                # For YouTube, ask user to select download type
                await self._ask_download_type(
                    chat_id, progress_message['message_id'], url, content_info
                )
            elif handler and handler.name == "Twitter/X":
                # For Twitter/X, download as text content
                await self._proceed_with_download(
                    chat_id, progress_message['message_id'], url, "text", None
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

        # Edit message with inline keyboard
        await self.telegram_service.bot.edit_message_text(
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
        content_info: Optional[Dict[str, Any]] = None
    ):
        """
        Proceed with the actual download.

        Args:
            chat_id: Telegram chat ID
            message_id: Message ID to update
            url: Video URL
            download_type: "audio" or "video"
            content_info: Optional content info dictionary
        """
        try:
            handler = self.download_service.platform_manager.get_handler(url)

            if not handler:
                await self.telegram_service.edit_message(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="❌ 无法处理此链接。"
                )
                return

            if handler.name == "Twitter/X":
                action_text = "正在获取推文"
            elif download_type == "audio":
                action_text = "正在下载音频"
            else:
                action_text = "正在下载视频"

            await self.telegram_service.edit_message(
                chat_id=chat_id,
                message_id=message_id,
                text=f"⬇️ {action_text}..."
            )

            format_id = None
            if handler.name == "YouTube":
                # Get format list
                video_info, formats = await handler.get_format_list(url)

                if download_type == "audio":
                    # Select best audio format
                    audio_format = handler.select_best_audio_format(formats)
                    if audio_format:
                        format_id = audio_format
                else:
                    # Select best video and audio formats
                    video_format = handler.select_best_video_format(formats)
                    audio_format = handler.select_best_audio_format(formats)

                    if video_format and audio_format:
                        format_id = f"{video_format}+{audio_format}"
                    elif video_format:
                        format_id = video_format

            # Create progress callback
            async def progress_callback(progress_data):
                await self._update_download_progress(
                    chat_id, message_id, progress_data
                )

            # Download content
            download_result = await self.download_service.download_content(
                url=url,
                content_type=download_type,
                progress_callback=progress_callback,
                format_id=format_id
            )

            if download_result.success:
                # Get content info from result
                if download_result.content_info:
                    title = download_result.content_info.title
                elif content_info:
                    title = content_info['title']
                else:
                    title = "Unknown"

                # Store the file
                storage_result = await self._store_downloaded_file(
                    download_result.file_path,
                    title,
                    download_type
                )

                if storage_result['success']:
                    await self._send_success_message(
                        chat_id,
                        message_id,
                        content_info or {},
                        storage_result,
                        download_type
                    )
                else:
                    await self.telegram_service.edit_message(
                        chat_id=chat_id,
                        message_id=message_id,
                        text="❌ 文件存储失败，请稍后重试。"
                    )
            else:
                await self.telegram_service.edit_message(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"❌ 下载失败: {download_result.error_message}"
                )

        except Exception as e:
            logger.error(f"Download error: {e}")
            await self.telegram_service.edit_message(
                chat_id=chat_id,
                message_id=message_id,
                text=f"❌ 下载过程中发生错误: {str(e)}"
            )

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

                    # Update message (with rate limiting)
                    # Only update every 2 seconds to avoid API limits
                    await self.telegram_service.edit_message(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=progress_text
                    )
        except Exception as e:
            # Don't fail the download if progress update fails
            logger.debug(f"Progress update error: {e}")

    async def _store_downloaded_file(
        self,
        file_path: str,
        title: str,
        download_type: str
    ) -> Dict[str, Any]:
        """
        Store the downloaded file.

        Args:
            file_path: Path to downloaded file or directory
            title: Content title
            download_type: "audio" or "video"

        Returns:
            Storage result dictionary
        """
        from ..utils.common import sanitize_filename
        import os

        if os.path.isdir(file_path):
            html_files = [f for f in os.listdir(file_path) if f.endswith('.html')]
            if html_files:
                ext = ".html"
            else:
                ext = ""
        else:
            _, original_ext = os.path.splitext(file_path)
            if original_ext:
                ext = original_ext
            else:
                ext = ".mp3" if download_type == "audio" else ".mp4"

        safe_filename = sanitize_filename(f"{title}{ext}")

        return await self.storage_service.store_file(file_path, safe_filename, download_type)

    async def _send_success_message(
        self,
        chat_id: int,
        message_id: int,
        content_info: Dict[str, Any],
        storage_result: Dict[str, Any],
        download_type: str = "video"
    ):
        """
        Send success message to user.

        Args:
            chat_id: Telegram chat ID
            message_id: Message ID to edit
            content_info: Content information dictionary
            storage_result: Storage result dictionary
            download_type: "audio" or "video"
        """
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

        await self.telegram_service.edit_message(
            chat_id=chat_id,
            message_id=message_id,
            text=message
        )

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Telegram error: {context.error}")

        if update and update.effective_chat:
            await self.telegram_service.send_message(
                chat_id=update.effective_chat.id,
                text="抱歉，处理您的请求时发生错误。请稍后重试。"
            )
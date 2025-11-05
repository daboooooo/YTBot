import asyncio
import os
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler
from telegram.ext.filters import Text, Command
from config import CONFIG
from logger import get_logger
from downloader import download_video, is_youtube_url
from nextcloud_client import upload_to_nextcloud, check_nextcloud_connection
from utils import retry, format_file_size


logger = get_logger(__name__)


# 创建Bot实例
def create_bot(token=None):
    """
    创建Telegram Bot实例

    Args:
        token: Telegram Bot Token

    Returns:
        Bot: 配置好的Bot实例
    """
    if token is None:
        token = CONFIG['telegram']['token']

    try:
        # 在python-telegram-bot 22.5版本中，使用httpx设置代理
        if os.environ.get('PROXY_URL'):
            proxy_url = os.environ.get('PROXY_URL')
            proxy_username = os.environ.get('PROXY_USERNAME', '')
            proxy_password = os.environ.get('PROXY_PASSWORD', '')

            # 构建带认证的代理URL
            if proxy_username and proxy_password:
                # 从URL中提取协议和主机信息
                from urllib.parse import urlparse
                parsed = urlparse(proxy_url)
                # 完全拆分长行
                scheme = parsed.scheme
                netloc = parsed.netloc
                path = parsed.path
                auth_proxy_url = f"{scheme}://"
                auth_part = f"{proxy_username}:{proxy_password}@"
                auth_proxy_url += auth_part
                auth_proxy_url += f"{netloc}{path}"
            else:
                auth_proxy_url = proxy_url

            # 创建带代理的HTTPX客户端
            import httpx
            http_client = httpx.Client(
                proxies={"all://": auth_proxy_url},
                verify=False  # 对于自签名证书可能需要
            )

            proxy_info = auth_proxy_url
            logger.info(f"使用代理: {proxy_info}")
            bot = Bot(token=token, http_client=http_client)
        else:
            # 不使用代理
            bot = Bot(token=token)
        logger.info("Bot实例创建成功")
        return bot
    except Exception as e:
        logger.error(f"创建Bot实例失败: {str(e)}")
        return None

# TelegramHandler类，封装所有Telegram相关功能


class TelegramHandler:
    def __init__(self, bot=None, user_states=None, semaphore=None, processing_updates=None):
        self.bot = bot or create_bot()
        self.application = None
        self.user_states = user_states if user_states is not None else {}
        self.processing_updates = processing_updates if processing_updates is not None else set()
        self.download_semaphore = semaphore or asyncio.Semaphore(
            CONFIG['app']['max_concurrent_downloads'])

    async def initialize_bot(self):
        """
        初始化Bot和Application
        """
        if not self.bot:
            logger.error("Bot实例未初始化")
            return False

        try:
            # 获取Bot信息
            bot_info = await self.bot.get_me()
            username = f"@{bot_info.username}"
            bot_id = bot_info.id
            logger.info(f"Bot初始化成功: {username}, ID: {bot_id}")

            # 创建Application
            self.application = Application.builder().bot(self.bot).build()

            # 设置处理器
            await self._setup_handlers()

            return True
        except Exception as e:
            logger.error(f"初始化Bot失败: {str(e)}")
            return False

    async def _setup_handlers(self):
        """
        设置各种命令和消息处理器
        """
        if not self.application:
            return

        logger.debug("正在设置消息处理器")
        # 命令处理器
        self.application.add_handler(CommandHandler('start', self.start_command))
        self.application.add_handler(CommandHandler('help', self.help_command))
        logger.debug("消息处理器设置完成")

        # 消息处理器 - 使用正确的filters导入
        self.application.add_handler(
            MessageHandler(
                Text() & ~Command(), self.handle_message))

        # 回调处理器
        self.application.add_handler(CallbackQueryHandler(self._handle_download_choice))

        # 错误处理器
        self.application.add_error_handler(self.error_handler)

    async def start_command(self, update, context):
        """处理/start命令"""
        user = update.effective_user
        chat_id = update.effective_chat.id

        logger.info(f"收到来自用户 {chat_id} 的 /start 命令")

        # 检查用户权限
        if not self._check_user_permission(chat_id):
            await self.send_message_safely(
                chat_id=chat_id,
                text="您没有权限使用此机器人。"
            )
            return

        # 构建欢迎消息
        user_name = user.first_name
        welcome_message = (
            f"👋 您好，{user_name}！\n\n"
            f"我是YouTube下载机器人，您可以发送YouTube链接给我，\n"
            f"我会帮您下载并上传到Nextcloud。\n\n"
            f"💡 使用提示：\n"
            f"• 直接发送YouTube视频链接\n"
            f"• 选择下载音频或视频\n"
            f"• 文件将自动上传到Nextcloud\n\n"
            f"发送 /help 获取更多帮助信息。"
        )

        logger.debug(f"向用户 {chat_id} 发送欢迎消息")
        await self.send_message_safely(chat_id=chat_id, text=welcome_message)

    async def help_command(self, update, context):
        """处理/help命令"""
        chat_id = update.effective_chat.id

        logger.info(f"收到来自用户 {chat_id} 的 /help 命令")

        # 检查用户权限
        if not self._check_user_permission(chat_id):
            await self.send_message_safely(
                chat_id=chat_id,
                text="您没有权限使用此机器人。"
            )
            return

        help_message = (
            "📋 使用帮助\n\n"
            "🔹 基本功能：\n"
            "• 发送YouTube视频链接，选择下载类型\n"
            "• 音频文件将转换为MP3格式\n"
            "• 视频文件保持原始质量\n\n"
            "🔹 支持的命令：\n"
            "• /start - 开始使用机器人\n"
            "• /help - 显示此帮助信息\n\n"
            "🔹 注意事项：\n"
            "• 大文件下载可能需要较长时间\n"
            "• 受版权保护的视频可能无法下载\n"
            "• 年龄限制的视频可能无法下载"
        )

        logger.debug(f"向用户 {chat_id} 发送帮助消息")
        await self.send_message_safely(chat_id=chat_id, text=help_message)

    async def handle_message(self, update, context):
        """处理普通消息"""
        chat_id = update.effective_chat.id
        text = update.message.text
        message_id = update.message.message_id

        # 添加详细日志
        logger.info(f"收到来自用户 {chat_id} 的消息: {text}")

        # 检查用户权限
        logger.info(f"正在检查用户 {chat_id} 的权限")
        if not self._check_user_permission(chat_id):
            logger.warning(f"用户 {chat_id} 没有权限使用机器人")
            await self.send_message_safely(
                chat_id=chat_id,
                text="您没有权限使用此机器人。"
            )
            return

        # 检查是否为YouTube链接
        logger.debug(f"正在检查消息是否为YouTube链接: {text}")
        is_youtube = is_youtube_url(text)
        logger.debug(f"YouTube链接检查结果: {is_youtube}")

        if is_youtube:
            logger.info(f"检测到YouTube链接: {text}")
            logger.debug(f"为用户 {chat_id} 记录状态并创建下载类型选择按钮")
            # 记录用户状态
            self.user_states[chat_id] = {
                'url': text,
                'timestamp': asyncio.get_event_loop().time()
            }

            # 发送下载类型选择
            keyboard = [
                [
                    InlineKeyboardButton(
                        "🎵 下载音频",
                        callback_data=f"audio_{chat_id}"
                    ),
                    InlineKeyboardButton(
                        "🎬 下载视频",
                        callback_data=f"video_{chat_id}"
                    )
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await self.send_message_safely(
                chat_id=chat_id,
                text="请选择下载类型：",
                reply_markup=reply_markup,
                reply_to_message_id=message_id
            )
        else:
            await self.send_message_safely(
                chat_id=chat_id,
                text="这不是有效的YouTube链接，请发送正确的YouTube视频链接。",
                reply_to_message_id=message_id
            )

    async def _handle_download_choice(self, update, context):
        """处理下载类型选择回调"""
        query = update.callback_query
        data = query.data
        chat_id = query.message.chat_id

        # 立即回复回调，避免超时
        await query.answer()

        # 解析回调数据
        try:
            download_type, user_id = data.split('_', 1)
            user_id = int(user_id)

            # 验证用户ID
            if user_id != chat_id:
                await self.send_message_safely(
                    chat_id=chat_id,
                    text="无效的操作。"
                )
                return

            # 获取用户状态
            if chat_id not in self.user_states:
                await self.send_message_safely(
                    chat_id=chat_id,
                    text="会话已超时，请重新发送YouTube链接。"
                )
                return

            url = self.user_states[chat_id]['url']

            # 删除用户状态
            del self.user_states[chat_id]

            # 通知用户开始下载
            await self.send_message_safely(
                chat_id=chat_id,
                text=f"🚀 开始{download_type == 'audio' and '音频' or '视频'}下载..."
            )

            # 使用信号量控制并发
            async with self.download_semaphore:
                # 执行下载和处理
                # 获取用户ID
                user_id = update.effective_user.id

                await self.download_and_process(
                    video_url=url,
                    download_type=download_type,
                    chat_id=chat_id,
                    user_id=user_id
                )

        except Exception as e:
            logger.error(f"处理下载选择时出错: {str(e)}")
            await self.send_message_safely(
                chat_id=chat_id,
                text=f"处理您的请求时出错: {str(e)}"
            )

    @retry(max_retries=3, initial_delay=1.0)
    async def send_message_safely(self, chat_id, text, **kwargs):
        """
        安全发送消息，处理消息长度限制和发送失败情况
        """
        # 检查消息长度
        max_length = CONFIG['app']['max_message_length']
        if len(text) > max_length:
            # 分段发送
            parts = []
            current_part = ""

            for paragraph in text.split('\n'):
                if len(current_part) + len(paragraph) + 1 > max_length:
                    if current_part:
                        parts.append(current_part)
                        current_part = ""
                    # 如果单个段落就超过长度限制，直接截断
                    if len(paragraph) > max_length:
                        current_part = paragraph[:max_length - 3] + "..."
                    else:
                        current_part = paragraph
                else:
                    if current_part:
                        current_part += "\n"
                    current_part += paragraph

            if current_part:
                parts.append(current_part)

            # 发送所有部分
            last_message = None
            for i, part in enumerate(parts):
                if i > 0:
                    part = f"(继续)\n{part}"
                if i < len(parts) - 1:
                    part = f"{part}\n(待续)"

                last_message = await self.bot.send_message(
                    chat_id=chat_id,
                    text=part,
                    **kwargs
                )

            return last_message
        else:
            # 消息长度正常，直接发送
            return await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                **kwargs
            )

    async def download_and_process(self, video_url, download_type, chat_id, user_id):
        """
        下载视频并上传到Nextcloud的完整流程
        与telegram_handler.py中的参数保持一致
        """
        # 创建进度消息
        progress_message = await self.send_message_safely(
            chat_id=chat_id,
            text=f"🚀 正在开始下载...\n\n"
                 f"📱 类型: {'音频' if download_type == 'audio' else '视频'}"
        )

        # 进度更新回调
        last_percent = -1

        async def progress_callback(progress_info):
            nonlocal last_percent

            try:
                if progress_info['status'] == 'downloading':
                    percent = progress_info.get('percent', 0)
                    speed = progress_info.get('speed', '未知')
                    eta = progress_info.get('eta', '未知')
                    # 根据配置的进度更新频率更新
                    progress_interval = CONFIG['download']['progress_update_interval']
                    if percent - last_percent >= progress_interval or percent >= 95:
                        last_percent = percent
                        media_type = "下载音频" if download_type == 'audio' else "下载视频"
                        update_text = (
                            f"🚀 正在{media_type}...\n\n"
                            f"🔗 链接: {video_url}\n"
                            f"📊 进度: {percent:.1f}%\n"
                            f"⚡ 速度: {speed}\n"
                            f"⏱️ 剩余: {eta}"
                        )
                        # 使用编辑消息功能，避免发送多条消息
                        try:
                            await self.bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=progress_message.message_id,
                                text=update_text
                            )
                        except Exception as e:
                            logger.warning(f"更新进度消息失败: {str(e)}")
                            # 如果编辑失败，尝试重新发送
                            try:
                                await self.bot.send_message(
                                    chat_id=chat_id,
                                    text=update_text
                                )
                            except Exception:
                                pass
                elif progress_info['status'] == 'finished':
                    await self.bot.edit_message_text(
                                    chat_id=chat_id,
                                    message_id=progress_message.message_id,
                                    text=f"✅ 下载完成！正在准备上传到Nextcloud...\n\n"
                                    f"🔗 链接: {video_url}"
                                )
            except Exception as e:
                logger.error(f"处理进度回调时出错: {str(e)}")

        try:
            # 下载视频
            file_path, info = await download_video(
                url=video_url,
                download_type=download_type,
                progress_callback=progress_callback
            )

            # 获取文件信息
            file_size = os.path.getsize(file_path)
            file_name = os.path.basename(file_path)
            video_title = info.get('title', 'Unknown Video')

            # 更新进度消息
            await self.bot.edit_message_text(
                                    chat_id=chat_id,
                                    message_id=progress_message.message_id,
                                    text=f"📤 正在上传到Nextcloud...\n\n"
                                    f"📁 文件: {file_name}\n"
                                    f"📊 大小: {format_file_size(file_size)}"
                                )

            # 构建Nextcloud上传路径
            remote_dir = CONFIG['nextcloud']['upload_dir']
            if not remote_dir.startswith('/'):
                remote_dir = f'/{remote_dir}'

            # 根据下载类型创建子目录
            media_type_dir = 'Audio' if download_type == 'audio' else 'Video'
            remote_dir = f"{remote_dir}/{media_type_dir}"

            # 上传文件到Nextcloud
            remote_file_path = f"{remote_dir}/{file_name}"
            file_url = await asyncio.to_thread(
                upload_to_nextcloud,
                file_path,
                remote_file_path
            )

            # 更新进度消息为完成状态
            await self.bot.edit_message_text(
                                    chat_id=chat_id,
                                    message_id=progress_message.message_id,
                                    text=f"✅ 下载和上传完成！\n\n"
                                    f"📁 文件: {file_name}\n"
                                    f"📊 大小: {format_file_size(file_size)}\n"
                                    f"🔗 访问链接: {file_url}"
                                )

            logger.info(f"成功为用户 {user_id} 下载并上传文件: {file_name}")

        except Exception as e:
            # 发送错误消息
            error_message = f"❌ 下载和处理失败: {str(e)}"

            # 尝试编辑消息，失败则发送新消息
            try:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_message.message_id,
                    text=error_message
                )
            except Exception as e:
                logger.warning(f"编辑错误消息失败: {str(e)}")
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=error_message
                )

            logger.error(f"处理用户 {user_id} 的下载请求时出错: {str(e)}")
        finally:
            # 清理临时文件
            if 'file_path' in locals() and file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.debug(f"已清理临时文件: {file_path}")
                except Exception as e:
                    logger.warning(f"清理临时文件失败: {str(e)}")

    def _check_user_permission(self, chat_id):
        """
        检查用户权限

        Args:
            chat_id: 用户的聊天ID

        Returns:
            bool: 用户是否有权限
        """
        # 获取允许的聊天ID列表
        allowed_ids = CONFIG['telegram']['allowed_chat_ids']
        # 确保chat_id是字符串类型
        chat_id_str = str(chat_id)

        # 优化权限检查逻辑
        has_permission = chat_id_str in allowed_ids

        # 添加详细日志便于调试
        logger.info(f"权限检查: 用户ID {chat_id_str} {'有' if has_permission else '无'}权限访问")
        logger.debug(f"允许的ID列表: {allowed_ids}")

        return has_permission

    async def error_handler(self, update, context):
        """
        处理更新过程中的错误
        """
        logger.error(f"更新处理错误: {context.error}")

        # 尝试通知用户
        if update and update.effective_chat:
            try:
                await self.send_message_safely(
                    chat_id=update.effective_chat.id,
                    text="处理您的请求时发生错误，请稍后重试。"
                )
            except Exception:
                pass

    async def start_polling(self):
        """
        开始轮询更新
        """
        print("=== 进入start_polling函数 ===")
        logger.info("=== 进入start_polling函数 ===")

        if not self.application:
            print("无法启动轮询，Application未初始化")
            logger.error("无法启动轮询，Application未初始化")
            return

        try:
            # 检查Nextcloud连接
            print("检查Nextcloud连接...")
            logger.info("检查Nextcloud连接...")
            if not check_nextcloud_connection():
                print("Nextcloud连接失败，无法启动机器人")
                logger.error("Nextcloud连接失败，无法启动机器人")
                return

            print("开始轮询更新...")
            logger.info("开始轮询更新...")

            # 初始化Application - 注意：处理器已在initialize_bot中设置
            print("初始化Application...")
            logger.info("初始化Application...")
            await self.application.initialize()
            print("Application初始化完成")
            logger.info("Application初始化完成")

            # 启动Application
            print("启动Application...")
            logger.info("启动Application...")
            await self.application.start()
            print("Application启动完成")
            logger.info("Application启动完成")

            # 获取updater并启动轮询
            # 这是兼容旧版python-telegram-bot的方式
            if hasattr(self.application, 'updater') and self.application.updater:
                logger.info("启动updater轮询...")
                await self.application.updater.start_polling(
                    poll_interval=1.0,
                    timeout=10,
                    drop_pending_updates=True
                )
            else:
                # 如果没有updater，使用Application的轮询方法
                # 尝试使用不同版本的API兼容方式
                logger.info("使用Application直接轮询...")
                # 尝试直接启动轮询
                await self.application.run_polling(
                    poll_interval=1.0,
                    timeout=10,
                    drop_pending_updates=True
                )

            logger.info("✅ 轮询已成功启动！机器人现在应该能接收消息了")
            logger.info("💡 提示: 尝试发送 /start 命令或YouTube链接测试")

            # 保持运行直到收到停止信号
            while True:
                await asyncio.sleep(1)

        except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
            logger.info("收到停止信号，正在关闭...")
        except Exception as e:
            logger.error(f"轮询过程中发生错误: {str(e)}")
            import traceback
            logger.debug(traceback.format_exc())
        finally:
            try:
                # 确保正确关闭应用
                if hasattr(self.application, 'is_running') and self.application.is_running:
                    logger.info("停止轮询并关闭应用...")
                    # 先停止updater（如果存在）
                    if hasattr(self.application, 'updater') and self.application.updater:
                        await self.application.updater.stop()
                    # 再停止应用
                    await self.application.stop()
                    await self.application.shutdown()
            except Exception as e:
                logger.warning(f"关闭Application时发生错误: {str(e)}")

            logger.info("机器人已关闭")

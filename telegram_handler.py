import asyncio
import os
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from logger import logger
from downloader import is_youtube_url, download_video
from nextcloud import upload_file_to_nextcloud, get_nextcloud_client
from config import CONFIG


# 重试装饰器
def retry(max_retries=3, delay=1, exponential_backoff=True):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            retries = 0
            current_delay = delay
            last_exception = None

            while retries <= max_retries:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    retries += 1
                    if retries > max_retries:
                        logger.error(
                            f"重试失败 {max_retries} 次: {func.__name__}() - {str(e)}")
                        raise last_exception

                    logger.warning(
                        f"重试中... ({retries}/{max_retries}) - {func.__name__}() - {str(e)}"
                    )
                    await asyncio.sleep(current_delay)

                    if exponential_backoff:
                        current_delay *= 2

            # 这个地方不应该被到达，但为了安全
            raise last_exception
        return wrapper
    return decorator


class TelegramHandler:
    def __init__(self, token=None, chat_ids=None):
        """
        初始化Telegram处理器

        Args:
            token: Telegram Bot令牌（可选，默认使用配置中的值）
            chat_ids: 允许的聊天ID列表（可选，默认使用配置中的值）
        """
        self.token = token or CONFIG['TELEGRAM_TOKEN']
        self.application = None
        self.chat_ids = set(chat_ids or CONFIG['ALLOWED_CHAT_IDS'])
        self.user_states = {}
        self.processing_locks = {}
        self.nextcloud_client = None

    async def initialize_bot(self):
        """
        初始化Telegram Bot应用
        """
        try:
            logger.info("正在初始化Telegram Bot...")

            # 创建应用
            self.application = Application.builder().token(self.token).build()

            # 设置命令
            await self.application.bot.setMyCommands([
                BotCommand("start", "启动机器人"),
                BotCommand("help", "显示帮助信息")
            ])

            # 设置处理器
            self._setup_handlers()

            logger.info("Telegram Bot初始化成功")
            return True
        except Exception as e:
            logger.error(f"初始化Telegram Bot失败: {str(e)}")
            raise

    def _setup_handlers(self):
        """
        设置各种处理器
        """
        if not self.application:
            raise ValueError("Bot应用尚未初始化")

        # 添加命令处理器
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))

        # 添加消息处理器
        message_filter = filters.TEXT & ~filters.COMMAND
        self.application.add_handler(MessageHandler(message_filter, self.handle_message))

    async def start_command(self, update: Update, context):
        """
        处理/start命令
        """
        user_id = update.message.from_user.id

        # 记录用户ID
        self.chat_ids.add(user_id)

        # 发送欢迎消息
        welcome_message = (
            "欢迎使用YouTube下载机器人！\n\n"
            "请发送YouTube视频链接，我会将其转换并上传到Nextcloud。\n"
            "您可以发送视频链接，我会提示您选择下载类型。"
        )

        await self.send_message(update.message.chat_id, welcome_message)

        logger.info(f"用户 {user_id} 已启动机器人")

    async def help_command(self, update: Update, context):
        """
        处理/help命令
        """
        help_message = (
            "如何使用：\n\n"
            "1. 发送YouTube视频链接\n"
            "2. 选择下载类型：音频或视频\n"
            "3. 等待下载、转换和上传完成\n"
            "4. 收到Nextcloud文件链接\n\n"
            "命令列表：\n"
            "/start - 启动机器人\n"
            "/help - 显示帮助信息"
        )

        await self.send_message(update.message.chat_id, help_message)

        logger.info(f"用户 {update.message.from_user.id} 请求了帮助信息")

    async def handle_message(self, update: Update, context):
        """
        处理普通消息
        """
        try:
            chat_id = update.message.chat_id
            text = update.message.text
            user_id = update.message.from_user.id

            # 验证用户
            if user_id not in self.chat_ids:
                await self.send_message(chat_id, "请先使用 /start 命令启动机器人")
                return

            # 检查用户状态
            if chat_id in self.user_states:
                state = self.user_states[chat_id]
                if state['waiting_for_choice']:
                    # 处理用户的下载类型选择
                    await self._handle_download_choice(update, state)
                    return

            # 检查是否是YouTube链接
            if is_youtube_url(text):
                # 验证链接是否已在处理中
                if chat_id in self.processing_locks and self.processing_locks[chat_id].locked():
                    await self.send_message(chat_id, "请等待当前任务完成后再发送新的链接")
                    return

                # 初始化锁
                if chat_id not in self.processing_locks:
                    self.processing_locks[chat_id] = asyncio.Lock()

                # 使用锁确保同一用户不会同时处理多个任务
                async with self.processing_locks[chat_id]:
                    # 保存视频链接到用户状态
                    self.user_states[chat_id] = {
                        'waiting_for_choice': True,
                        'video_url': text
                    }

                    # 询问用户选择下载类型
                    await self.send_message(
                        chat_id,
                        "请选择下载类型：\n"
                        "1. 仅音频 (MP3)\n"
                        "2. 视频 (MP4)"
                    )

                    logger.info(f"已向用户 {user_id} 请求下载类型选择")
            else:
                # 不是YouTube链接
                await self.send_message(chat_id, "请发送有效的YouTube视频链接")
                logger.info(f"用户 {user_id} 发送了非YouTube链接")

        except Exception as e:
            logger.error(f"处理消息时出错: {str(e)}")
            await self.send_message(update.message.chat_id, "处理您的消息时出错，请稍后重试")

    async def _handle_download_choice(self, update: Update, state):
        """
        处理用户的下载类型选择
        """
        chat_id = update.message.chat_id
        text = update.message.text.strip().lower()
        user_id = update.message.from_user.id

        try:
            # 验证选择
            if text in ['1', '音频', 'audio', 'mp3']:
                download_type = 'audio'
            elif text in ['2', '视频', 'video', 'mp4']:
                download_type = 'video'
            else:
                await self.send_message(chat_id, "无效选择，请回复 1 或 2")
                return

            # 获取视频URL
            video_url = state['video_url']

            # 清除状态
            state['waiting_for_choice'] = False

            await self.send_message(chat_id, f"开始{download_type}下载...")

            # 执行下载和上传
            await self.download_and_process(video_url, download_type, chat_id, user_id)
        except Exception as e:
            logger.error(f"处理下载选择时出错: {str(e)}")
            await self.send_message(chat_id, "处理您的选择时出错，请稍后重试")
            # 重置状态
            state['waiting_for_choice'] = False

    @retry(
        max_retries=CONFIG['TELEGRAM_MESSAGE_RETRIES'],
        delay=CONFIG['TELEGRAM_MESSAGE_RETRY_DELAY']
    )
    async def send_message(self, chat_id, text, **kwargs):
        """
        发送消息，包含长度检查和重试机制
        """
        if not self.application:
            raise ValueError("Bot应用尚未初始化")

        # 检查消息长度（Telegram限制为4096字符）
        max_message_length = CONFIG['TELEGRAM_MAX_MESSAGE_LENGTH']
        if len(text) > max_message_length:
            # 消息过长，分段发送
            chunks = [text[i:i+max_message_length] for i in range(0, len(text), max_message_length)]
            for chunk in chunks:
                await self.application.bot.send_message(chat_id, chunk, **kwargs)
                await asyncio.sleep(CONFIG['TELEGRAM_MESSAGE_DELAY'])  # 避免发送过快
        else:
            await self.application.bot.send_message(chat_id, text, **kwargs)

    async def download_and_process(self, video_url, download_type, chat_id, user_id):
        """
        下载视频并上传到Nextcloud
        修复了上传失败后重复下载的问题
        """
        last_progress_message = None
        progress_updated = False
        file_path = None

        try:
            # 定义进度回调函数
            async def progress_callback(progress_info):
                nonlocal last_progress_message, progress_updated

                try:
                    if progress_info['status'] == 'downloading':
                        percent = progress_info.get('percent', 0)
                        speed = progress_info.get('speed', '未知')
                        eta = progress_info.get('eta', '未知')

                        # 根据配置的进度更新频率更新
                        update_interval = CONFIG['TELEGRAM_PROGRESS_UPDATE_INTERVAL']
                        if int(percent) % update_interval == 0 or percent >= 95:
                            # 生成进度消息
                            progress_message = f"下载进度: {percent:.1f}%\n速度: {speed}\n剩余时间: {eta}"

                            # 如果进度消息有变化，更新它
                            if progress_message != last_progress_message:
                                if last_progress_message is None:
                                    last_progress_message = await self.send_message(
                                        chat_id, progress_message)
                            else:
                                # 尝试多次编辑消息
                                edit_success = False
                                for edit_attempt in range(CONFIG['TELEGRAM_EDIT_MESSAGE_RETRIES']):
                                    try:
                                        # 尝试编辑之前的进度消息
                                        await self.application.bot.edit_message_text(
                                            progress_message,
                                            chat_id=chat_id,
                                            message_id=last_progress_message.message_id
                                        )
                                        edit_success = True
                                        break
                                    except Exception as e:
                                        # 如果编辑失败，记录错误并等待后重试
                                        logger.warning(
                                            f"编辑进度消息失败 (尝试 {edit_attempt + 1}/"
                                            f"{CONFIG['TELEGRAM_EDIT_MESSAGE_RETRIES']}): {str(e)}"
                                        )
                                        await asyncio.sleep(0.5)

                                # 如果所有编辑尝试都失败，发送新消息
                                if not edit_success:
                                    logger.warning("所有编辑进度消息的尝试都失败，发送新消息")
                                    last_progress_message = await self.send_message(
                                        chat_id, progress_message)
                                progress_updated = True
                    elif progress_info['status'] == 'finished':
                        # 下载完成，发送完成消息
                        if last_progress_message is not None:
                            # 尝试多次编辑消息
                            edit_success = False
                            for edit_attempt in range(CONFIG['TELEGRAM_EDIT_MESSAGE_RETRIES']):
                                try:
                                    await self.application.bot.edit_message_text(
                                        "下载完成，正在上传到Nextcloud...",
                                        chat_id=chat_id,
                                        message_id=last_progress_message.message_id
                                    )
                                    edit_success = True
                                    break
                                except Exception as e:
                                    logger.warning(
                                            f"编辑完成消息失败 (尝试 {edit_attempt + 1}/"
                                            f"{CONFIG['TELEGRAM_EDIT_MESSAGE_RETRIES']}): {str(e)}"
                                        )
                                    await asyncio.sleep(0.5)

                            if not edit_success:
                                await self.send_message(chat_id, "下载完成，正在上传到Nextcloud...")
                        else:
                            await self.send_message(chat_id, "下载完成，正在上传到Nextcloud...")
                except Exception as e:
                    logger.error(f"发送进度更新时出错: {str(e)}")

            # 下载视频，获取文件路径（修复上传失败后重复下载的问题）
            file_path, info = await download_video(video_url, download_type, progress_callback)

            logger.info(f"成功下载文件: {file_path}")

            # 计算文件大小
            file_size = os.path.getsize(file_path) / (1024 * 1024)  # MB
            file_info = {
                'name': os.path.basename(file_path),
                'size': f"{file_size:.2f} MB",
                'title': info.get('title', '未知标题'),
                'duration': info.get('duration_string', '未知时长')
            }

            # 发送文件信息
            file_info_message = (
                f"📄 文件信息\n"
                f"标题: {file_info['title']}\n"
                f"文件名: {file_info['name']}\n"
                f"大小: {file_info['size']}\n"
                f"时长: {file_info['duration']}\n"
                "正在上传到Nextcloud..."
            )

            if last_progress_message is not None:
                # 尝试多次编辑消息
                edit_success = False
                for edit_attempt in range(CONFIG['TELEGRAM_EDIT_MESSAGE_RETRIES']):
                    try:
                        await self.application.bot.edit_message_text(
                            file_info_message,
                            chat_id=chat_id,
                            message_id=last_progress_message.message_id
                        )
                        edit_success = True
                        break
                    except Exception as e:
                        logger.warning(
                                            f"编辑文件信息失败 (尝试 {edit_attempt + 1}/"
                                            f"{CONFIG['TELEGRAM_EDIT_MESSAGE_RETRIES']}): {str(e)}"
                                        )
                        await asyncio.sleep(0.5)

                if not edit_success:
                    await self.send_message(chat_id, file_info_message)
            else:
                await self.send_message(chat_id, file_info_message)

            # 初始化Nextcloud客户端（如果尚未初始化）
            if self.nextcloud_client is None:
                self.nextcloud_client = get_nextcloud_client()

            # 构建远程路径
            upload_dir = CONFIG['NEXTCLOUD_UPLOAD_DIR']
            remote_path = f"{upload_dir}/{file_info['name']}"

            # 上传到Nextcloud（修复上传失败不重复下载的问题）
            try:
                await upload_file_to_nextcloud(
                    file_path=file_path,
                    remote_path=remote_path,
                    nextcloud_client=self.nextcloud_client
                )

                # 添加终端提示
                print(f"📤 视频 '{file_info['title']}' 已成功下载并上传到Nextcloud！")

                # 构建访问链接（已移除，因为不再需要）

                success_message = (
                    f"✅ 上传成功！\n"
                    f"文件路径: {remote_path}\n"
                    f"\n感谢使用！"
                )
                await self.send_message(chat_id, success_message)
                logger.info(f"用户 {user_id} 的文件上传成功: {remote_path}")
            except Exception as upload_error:
                error_message = f"❌ 上传失败: {str(upload_error)}"
                await self.send_message(chat_id, error_message)
                logger.error(f"用户 {user_id} 的文件上传失败: {str(upload_error)}")

        except Exception as e:
            error_message = f"❌ 处理失败: {str(e)}"
            await self.send_message(chat_id, error_message)
            logger.error(f"处理文件时出错: {str(e)}")
        finally:
            # 清理临时文件
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.info(f"已清理临时文件: {file_path}")
                except Exception as e:
                    logger.warning(f"清理临时文件失败: {str(e)}")

            # 如果有进度更新但没有后续更新，确保用户知道任务已完成
            if progress_updated and last_progress_message is not None:
                try:
                    await self.application.bot.edit_message_text(
                        "任务已完成\n感谢使用！",
                        chat_id=chat_id,
                        message_id=last_progress_message.message_id
                    )
                except Exception:
                    # 忽略编辑失败
                    pass

    async def start_polling(self):
        """
        开始轮询更新
        """
        if not self.application:
            raise ValueError("Bot应用尚未初始化")

        try:
            logger.info("开始轮询Telegram更新...")

            # 启动轮询
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling(
                poll_interval=CONFIG['TELEGRAM_POLL_INTERVAL'],
                timeout=CONFIG['TELEGRAM_POLL_TIMEOUT'],
                read_timeout=CONFIG['TELEGRAM_READ_TIMEOUT'],
                write_timeout=CONFIG['TELEGRAM_WRITE_TIMEOUT']
            )

            # 保持运行
            while True:
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"轮询过程中出错: {str(e)}")
            raise
        finally:
            # 确保应用正确停止
            if self.application:
                await self.application.stop()
                await self.application.shutdown()

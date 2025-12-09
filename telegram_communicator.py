import asyncio
import os
from typing import Optional, Callable, Any
from telegram import Bot
from telegram.request import HTTPXRequest
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler
from telegram.ext.filters import Text
from config import CONFIG
from logger import get_logger

logger = get_logger(__name__)


class TelegramCommunicator:
    """
    Telegram通信模块，封装与Telegram服务器的通信功能
    支持连接状态检测、自动重连和资源管理
    """
    def __init__(self, token: Optional[str] = None):
        """
        初始化Telegram通信模块

        Args:
            token: Telegram Bot Token
        """
        self._token = token or CONFIG['telegram']['token']
        self._bot: Optional[Bot] = None
        self._application: Optional[Application] = None
        self._is_connected = False
        self._connect_lock = asyncio.Lock()
        self._handlers = {
            'command': [],
            'message': [],
            'callback_query': []
        }
        self._error_handler = None

    @property
    def bot(self) -> Optional[Bot]:
        """
        获取Bot实例

        Returns:
            Optional[Bot]: Bot实例
        """
        return self._bot

    @property
    def application(self) -> Optional[Application]:
        """
        获取Application实例

        Returns:
            Optional[Application]: Application实例
        """
        return self._application

    @property
    def is_connected(self) -> bool:
        """
        获取连接状态

        Returns:
            bool: 连接是否正常
        """
        return self._is_connected

    async def connect(self) -> bool:
        """
        连接到Telegram服务器

        Returns:
            bool: 连接是否成功
        """
        async with self._connect_lock:
            if self._is_connected:
                logger.info("已经连接到Telegram服务器")
                return True

            try:
                logger.info("正在连接到Telegram服务器...")

                # 创建Bot实例
                proxy_url = os.environ.get('PROXY_URL')

                if proxy_url:
                    # 处理代理配置
                    from urllib.parse import urlparse
                    parsed = urlparse(proxy_url)
                    clean_proxy_url = f"{parsed.scheme}://{parsed.netloc}"

                    request = HTTPXRequest(
                        proxy_url=clean_proxy_url,
                        proxy_kwargs={
                            'verify': False  # 对于自签名证书可能需要
                        },
                        connect_timeout=30,
                        read_timeout=30,
                        write_timeout=30
                    )

                    self._bot = Bot(token=self._token, request=request)
                else:
                    # 不使用代理
                    request = HTTPXRequest(
                        connect_timeout=30,
                        read_timeout=30,
                        write_timeout=30
                    )
                    self._bot = Bot(token=self._token, request=request)

                # 测试连接
                await self._bot.get_me()
                logger.info("成功连接到Telegram服务器")

                # 创建Application
                self._application = Application.builder().bot(self._bot).build()

                # 添加处理器
                self._setup_handlers()

                self._is_connected = True
                return True
            except Exception as e:
                logger.error(f"连接到Telegram服务器失败: {str(e)}")
                await self.disconnect()
                return False

    async def disconnect(self) -> None:
        """
        断开与Telegram服务器的连接，释放资源
        """
        async with self._connect_lock:
            if not self._is_connected:
                return

            logger.info("正在断开与Telegram服务器的连接...")

            try:
                # 停止Application（如果存在）
                if self._application:
                    # 先停止updater（如果存在且运行）
                    if hasattr(self._application, 'updater') and self._application.updater:
                        try:
                            # 检查updater是否在运行
                            if hasattr(self._application.updater, 'is_running') and \
                                    self._application.updater.is_running:
                                await self._application.updater.stop()
                            elif not hasattr(self._application.updater, 'is_running'):
                                # 对于没有is_running属性的版本，尝试安全停止
                                try:
                                    await self._application.updater.stop()
                                except Exception as stop_e:
                                    # 忽略"not running"类型的错误
                                    if "not running" not in str(stop_e).lower():
                                        logger.warning(f"停止updater时发生错误: {str(stop_e)}")
                        except Exception as e:
                            # 忽略"not running"类型的错误
                            if "not running" not in str(e).lower():
                                logger.warning(f"停止updater时发生错误: {str(e)}")

                    # 停止Application（如果运行）
                    try:
                        # 检查Application是否在运行
                        if hasattr(self._application, 'is_running') and \
                                self._application.is_running:
                            await self._application.stop()
                        elif not hasattr(self._application, 'is_running'):
                            # 对于没有is_running属性的版本，尝试安全停止
                            try:
                                await self._application.stop()
                            except Exception as stop_e:
                                # 忽略"not running"类型的错误
                                if "not running" not in str(stop_e).lower():
                                    logger.warning(f"停止Application时发生错误: {str(stop_e)}")
                    except Exception as e:
                        # 忽略"not running"类型的错误
                        if "not running" not in str(e).lower():
                            logger.warning(f"停止Application时发生错误: {str(e)}")

                    # 关闭Application
                    try:
                        await self._application.shutdown()
                    except Exception as e:
                        logger.warning(f"关闭Application时发生错误: {str(e)}")
            finally:
                # 释放资源
                self._application = None
                self._bot = None
                self._is_connected = False
                logger.info("已断开与Telegram服务器的连接")

    async def check_connection(self) -> bool:
        """
        检查与Telegram服务器的连接状态

        Returns:
            bool: 连接是否正常
        """
        if not self._is_connected or not self._bot:
            return False

        try:
            # 简单的连接测试
            await self._bot.get_me()
            return True
        except Exception as e:
            logger.error(f"Telegram连接检查失败: {str(e)}")
            await self.disconnect()
            return False

    async def send_message(self, chat_id: int, text: str, **kwargs) -> Any:
        """
        发送消息到Telegram服务器

        Args:
            chat_id: 聊天ID
            text: 消息文本
            **kwargs: 其他参数

        Returns:
            Any: 发送结果
        """
        if not self._is_connected or not self._bot:
            logger.error("未连接到Telegram服务器，无法发送消息")
            return None

        try:
            return await self._bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except Exception as e:
            logger.error(f"发送消息失败: {str(e)}")
            await self.disconnect()
            return None

    def add_command_handler(self, command: str, handler: Callable) -> None:
        """
        添加命令处理器

        Args:
            command: 命令名称
            handler: 处理器函数
        """
        self._handlers['command'].append((command, handler))

        # 如果已经连接，立即添加处理器
        if self._application:
            self._application.add_handler(CommandHandler(command, handler))

    def add_message_handler(self, handler: Callable) -> None:
        """
        添加消息处理器

        Args:
            handler: 处理器函数
        """
        self._handlers['message'].append(handler)

        # 如果已经连接，立即添加处理器
        if self._application:
            self._application.add_handler(MessageHandler(Text(), handler))

    def add_callback_query_handler(self, handler: Callable) -> None:
        """
        添加回调查询处理器

        Args:
            handler: 处理器函数
        """
        self._handlers['callback_query'].append(handler)

        # 如果已经连接，立即添加处理器
        if self._application:
            self._application.add_handler(CallbackQueryHandler(handler))

    def set_error_handler(self, handler: Callable) -> None:
        """
        设置错误处理器

        Args:
            handler: 处理器函数
        """
        self._error_handler = handler

        # 如果已经连接，立即设置处理器
        if self._application:
            self._application.add_error_handler(handler)

    def _setup_handlers(self) -> None:
        """
        设置所有处理器
        """
        if not self._application:
            return

        # 添加命令处理器
        for command, handler in self._handlers['command']:
            self._application.add_handler(CommandHandler(command, handler))

        # 添加消息处理器
        for handler in self._handlers['message']:
            self._application.add_handler(MessageHandler(Text(), handler))

        # 添加回调查询处理器
        for handler in self._handlers['callback_query']:
            self._application.add_handler(CallbackQueryHandler(handler))

        # 设置错误处理器
        if self._error_handler:
            self._application.add_error_handler(self._error_handler)

    async def start_polling(self, poll_interval: float = 1.0) -> None:
        """
        开始轮询Telegram更新

        Args:
            poll_interval: 轮询间隔
        """
        while True:
            try:
                if not await self.check_connection():
                    logger.info("连接断开，尝试重新连接...")
                    if not await self.connect():
                        logger.info("30秒后再次尝试连接...")
                        await asyncio.sleep(30)
                        continue

                logger.info("开始轮询Telegram更新...")

                # 初始化并启动Application
                if not self._application:
                    logger.error("Application未初始化")
                    await self.disconnect()
                    continue

                await self._application.initialize()
                await self._application.start()

                # 启动轮询
                if hasattr(self._application, 'updater') and self._application.updater:
                    await self._application.updater.start_polling(
                        poll_interval=poll_interval,
                        timeout=30,
                        drop_pending_updates=True
                    )
                else:
                    await self._application.run_polling(
                        poll_interval=poll_interval,
                        timeout=30,
                        drop_pending_updates=True
                    )

                # 保持运行直到停止信号
                # 使用无限循环替代wait_closed，因为某些版本不支持
                while True:
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"轮询过程中发生错误: {str(e)}")
                await self.disconnect()
                logger.info("30秒后重新连接...")
                await asyncio.sleep(30)
            finally:
                # 确保资源被释放
                await self.disconnect()

    async def stop_polling(self) -> None:
        """
        停止轮询
        """
        await self.disconnect()

    async def __aenter__(self) -> 'TelegramCommunicator':
        """
        异步上下文管理器进入方法
        """
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        异步上下文管理器退出方法
        """
        await self.disconnect()

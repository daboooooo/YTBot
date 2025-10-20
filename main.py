# 导入必要的模块
import os
import re
import sys
import socket
import threading
import tempfile
import asyncio
import logging
import logging.handlers
import requests
import inspect
import psutil
from telegram import Bot
from telegram.error import BadRequest, NetworkError, RetryAfter
from webdav3.client import Client
import yt_dlp
import time
from urllib.parse import urlparse
import signal

# 从配置文件导入配置
from config import (
    TELEGRAM_BOT_TOKEN,
    NEXTCLOUD_URL,
    NEXTCLOUD_USERNAME,
    NEXTCLOUD_PASSWORD,
    NEXTCLOUD_UPLOAD_DIR,
    MAX_CONCURRENT_DOWNLOADS,
    LOG_LEVEL,
    ADMIN_CHAT_ID
)

# 全局错误处理配置
ERROR_CHANNEL_ID = ADMIN_CHAT_ID

# 主事件循环引用
main_event_loop = None

# 用户状态管理字典，用于存储用户的选择状态
# 格式: {user_id: {'state': 'waiting_for_download_type', 'url': 'youtube_url',
#        'timestamp': timestamp}}
user_states = {}


# 配置日志

logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, LOG_LEVEL if 'LOG_LEVEL' in locals() else 'INFO'))

# 创建格式化器
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# 创建控制台处理器
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# 创建按大小和时间轮换的文件处理器
file_handler = logging.handlers.RotatingFileHandler(
    'ytbot.log', 
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5  # 保留5个备份
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)


def global_exception_handler(exctype, value, traceback):
    """
    全局异常处理器，捕获所有未处理的异常并记录

    Args:
        exctype: 异常类型
        value: 异常值
        traceback: 堆栈跟踪
    """
    # 先使用默认的异常处理器记录异常
    sys.__excepthook__(exctype, value, traceback)

    # 记录到日志
    error_msg = f"未处理的异常: {exctype.__name__}: {value}"
    logger.critical(error_msg)

    # 尝试向管理员发送错误通知
    if ERROR_CHANNEL_ID:
        try:
            # 格式化错误消息
            error_details = f"🚨 发生未处理的异常！\n\n" \
                f"**类型**: {exctype.__name__}\n" \
                f"**信息**: {str(value)}\n" \
                f"**时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n" \
                f"**主机**: {socket.gethostname() if hasattr(socket, 'gethostname') else '未知'}\n"

            # 限制消息长度
            if len(error_details) > 4096:
                error_details = error_details[:4093] + "..."

            # 在单独的线程中发送通知，避免阻塞
            def send_admin_notification():
                try:
                    # 创建一个新的事件循环来发送通知，与主事件循环完全分离
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                    # 创建一个新的Bot实例，避免使用全局的Bot实例
                    thread_bot = Bot(token=TELEGRAM_BOT_TOKEN)

                    loop.run_until_complete(thread_bot.send_message(
                        chat_id=ERROR_CHANNEL_ID,
                        text=error_details,
                        parse_mode='Markdown',
                        disable_notification=False
                    ))
                    loop.close()
                except Exception as e:
                    # 如果发送通知失败，记录到日志
                    logger.error(f"发送管理员错误通知失败: {str(e)}")

            # 启动线程发送通知
            notification_thread = threading.Thread(target=send_admin_notification)
            notification_thread.daemon = True
            notification_thread.start()
        except Exception as e:
            # 如果初始化通知发送失败，记录到日志
            logger.error(f"准备管理员错误通知失败: {str(e)}")


# 设置全局异常处理器
sys.excepthook = global_exception_handler


# 检查必需的配置是否存在
def check_required_config():
    required_configs = {
        'TELEGRAM_BOT_TOKEN': TELEGRAM_BOT_TOKEN,
        'NEXTCLOUD_URL': NEXTCLOUD_URL,
        'NEXTCLOUD_USERNAME': NEXTCLOUD_USERNAME,
        'NEXTCLOUD_PASSWORD': NEXTCLOUD_PASSWORD,
        'NEXTCLOUD_UPLOAD_DIR': NEXTCLOUD_UPLOAD_DIR
    }

    missing_configs = []
    for key, value in required_configs.items():
        if not value or value == f'YOUR_{key}':
            missing_configs.append(key)

    # 尝试获取ADMIN_CHAT_ID，如果不存在则设为None
    admin_chat_id = None
    try:
        from config import ADMIN_CHAT_ID as CONFIG_ADMIN_CHAT_ID
        if CONFIG_ADMIN_CHAT_ID and CONFIG_ADMIN_CHAT_ID != 'YOUR_TELEGRAM_USER_ID':
            admin_chat_id = CONFIG_ADMIN_CHAT_ID
    except ImportError:
        pass

    return missing_configs, admin_chat_id


# 检查并创建Bot实例
def create_bot(token):
    """
    创建并返回一个Bot实例，支持代理配置，不执行异步验证以避免事件循环冲突

    Args:
        token: Telegram Bot token

    Returns:
        Bot实例或None（如果创建失败）
    """
    try:
        # 尝试从配置文件获取代理设置
        proxy_url = None
        try:
            from config import PROXY_URL
            if PROXY_URL and PROXY_URL != 'YOUR_PROXY_URL':
                proxy_url = PROXY_URL
                logger.info(f"从配置文件获取代理设置: {proxy_url}")
        except (ImportError, AttributeError):
            # 如果配置文件中没有代理设置，尝试从环境变量获取
            for env_var in ['PROXY_URL', 'ALL_PROXY', 'all_proxy']:
                if env_var in os.environ:
                    proxy_url = os.environ[env_var]
                    logger.info(f"从环境变量 {env_var} 获取代理设置: {proxy_url}")
                    break
        
        # 验证和修正代理URL格式
        if proxy_url:
            try:
                parsed = urlparse(proxy_url)
                # 确保SOCKS代理使用正确的格式
                if parsed.scheme == 'socks' and not parsed.scheme.startswith('socks5'):
                    # 修正socks为socks5
                    proxy_url = proxy_url.replace('socks://', 'socks5://')
                    logger.warning(f"修正代理URL格式: {proxy_url}")
                
                # 检查是否有有效的scheme
                if not parsed.scheme:
                    # 如果没有scheme，默认添加http
                    proxy_url = f'http://{proxy_url}'
                    logger.warning("添加代理URL scheme: %s", proxy_url)
                    
            except Exception as e:
                logger.error("解析代理URL失败: %s", str(e))
                proxy_url = None
        
        # 创建Bot实例，使用代理设置（如果有）
        if proxy_url:
            # 使用代理设置
            bot = Bot(token=token, 
                      base_url='https://api.telegram.org/bot{}/', 
                      request_kwargs={'proxy_url': proxy_url})
            logger.info("成功创建带代理的Bot实例")
        else:
            # 不使用代理
            bot = Bot(token=token)
            logger.info("成功创建Bot实例（无代理）")
            
        return bot
    except Exception as e:
        logger.error(f"创建Bot实例失败: {str(e)}")
        # 记录详细错误信息
        import traceback
        logger.debug(traceback.format_exc())
        return None


# 初始化全局Bot变量
bot = None

# 并发控制
semaphore = None

# 主事件循环引用
main_event_loop = None

# 并发控制
semaphore = asyncio.Semaphore(
    MAX_CONCURRENT_DOWNLOADS if 'MAX_CONCURRENT_DOWNLOADS' in locals() else 5)


# 重试装饰器
def retry(max_retries=3, delay=2, exceptions=(Exception,)):
    def decorator(func):
        # 检查函数是否是异步函数
        is_async = inspect.iscoroutinefunction(func)

        if is_async:
            # 异步函数的装饰器
            async def async_wrapper(*args, **kwargs):
                retries = 0
                while retries < max_retries:
                    try:
                        return await func(*args, **kwargs)
                    except exceptions as e:
                        retries += 1
                        if retries >= max_retries:
                            logger.error(
                                f"函数 {func.__name__} 在 {max_retries} 次重试后失败: {str(e)}")
                            raise
                        logger.warning(
                            f"函数 {func.__name__} 重试 ({retries}/{max_retries})，错误: {str(e)}")
                        await asyncio.sleep(delay * retries)  # 指数退避
            return async_wrapper
        else:
            # 同步函数的装饰器
            def sync_wrapper(*args, **kwargs):
                retries = 0
                while retries < max_retries:
                    try:
                        return func(*args, **kwargs)
                    except exceptions as e:
                        retries += 1
                        if retries >= max_retries:
                            logger.error(
                                f"函数 {func.__name__} 在 {max_retries} 次重试后失败: {str(e)}")
                            raise
                        logger.warning(
                            f"函数 {func.__name__} 重试 ({retries}/{max_retries})，错误: {str(e)}")
                        time.sleep(delay * retries)  # 指数退避
            return sync_wrapper
    return decorator


# 规范化版本号，去除前导零
def normalize_version(version):
    # 分割版本号并去除每个部分的前导零
    parts = version.split('.')
    normalized_parts = [str(int(part)) if part.isdigit() else part for part in parts]
    return '.'.join(normalized_parts)


# 检查yt_dlp版本是否为最新
@retry(max_retries=3, delay=2, exceptions=(requests.RequestException,))
def check_yt_dlp_version():
    try:
        # 获取当前安装的yt_dlp版本
        current_version = yt_dlp.version.__version__
        logger.info(f"当前yt_dlp版本: {current_version}")

        # 获取PyPI上的最新版本，添加超时设置
        response = requests.get('https://pypi.org/pypi/yt-dlp/json', timeout=10)
        response.raise_for_status()
        latest_version = response.json()['info']['version']
        logger.info(f"最新yt_dlp版本: {latest_version}")

        # 规范化版本号并比较
        normalized_current = normalize_version(current_version)
        normalized_latest = normalize_version(latest_version)

        if normalized_current < normalized_latest:
            logger.warning(f"yt_dlp版本已过时! 当前版本: {current_version}, 最新版本: {latest_version}")
            logger.warning("建议运行: pip install --upgrade yt-dlp")
            return False, f"yt_dlp版本已过时! 当前版本: {current_version}, 最新版本: {latest_version}\n" +\
                "建议运行: pip install --upgrade yt-dlp"
        else:
            logger.info("yt_dlp已是最新版本")
            return True, f"yt_dlp已是最新版本: {current_version}"
    except Exception as e:
        error_msg = f"检查yt_dlp版本时出错: {str(e)}"
        logger.error(error_msg)
        return False, error_msg


# 验证YouTube链接格式
def is_youtube_url(url):
    # 首先确保url不为空且为字符串类型
    if not url or not isinstance(url, str):
        return False

    # 去除可能的前后空格
    url = url.strip()

    youtube_regex = (
        r'(https?://)?(www\.)?'
        r'(youtube|youtu|youtube-nocookie)\.(com|be)/'
        r'(watch\?v=|embed/|v/|.+/|watch\?.+&v=|watch\?v=|embed/|v/|.+/)?'
        r'([^&=%\?]{11})'
    )
    return re.match(youtube_regex, url) is not None


# 全局Nextcloud客户端缓存
_nextcloud_client_cache = {
    'client': None,
    'last_initialized': 0,
    'cache_ttl': 3600  # 缓存1小时
}


# 初始化NextCloud客户端
def get_nextcloud_client():
    """
    初始化并返回NextCloud客户端，增强了容错性、缓存和错误处理

    Returns:
        Client: 配置好的NextCloud客户端实例

    Raises:
        ValueError: 如果配置不完整或无效
        ConnectionError: 如果无法连接到NextCloud服务器
        Exception: 如果初始化过程中发生其他错误
    """
    global _nextcloud_client_cache
    current_time = time.time()

    # 检查缓存是否有效
    cache_valid = (_nextcloud_client_cache['client'] and
                   (current_time - _nextcloud_client_cache['last_initialized']) <
                   _nextcloud_client_cache['cache_ttl'])
    if cache_valid:
        try:
            # 验证缓存的客户端是否仍然有效
            if check_client_validity(_nextcloud_client_cache['client']):
                logger.debug("使用缓存的Nextcloud客户端")
                return _nextcloud_client_cache['client']
        except Exception as e:
            logger.warning(f"缓存的客户端验证失败: {str(e)}")
            _nextcloud_client_cache['client'] = None

    # 验证配置是否完整
    if not NEXTCLOUD_URL or not NEXTCLOUD_USERNAME or not NEXTCLOUD_PASSWORD:
        raise ValueError("Nextcloud配置不完整: URL、用户名或密码缺失")

    # 验证URL格式
    try:
        # 确保URL格式正确
        parsed_url = urlparse(NEXTCLOUD_URL)
        if not parsed_url.scheme or parsed_url.scheme not in ['http', 'https']:
            raise ValueError("Nextcloud URL格式无效，必须包含http或https协议")
    except Exception as e:
        raise ValueError(f"Nextcloud URL格式无效: {str(e)}")

    max_retries = 3
    retry_delay = 2  # 初始重试延迟为2秒

    for attempt in range(max_retries):
        try:
            options = {
                'webdav_hostname': f'{NEXTCLOUD_URL}/remote.php/dav/files/{NEXTCLOUD_USERNAME}/',
                'webdav_login': NEXTCLOUD_USERNAME,
                'webdav_password': NEXTCLOUD_PASSWORD,
                'webdav_timeout': 30,  # 连接超时设置，单位秒
                'webdav_verbose': False  # 禁用详细日志
            }

            # 添加更多健壮的选项
            client = Client(options)

            # 验证客户端连接
            if check_client_validity(client):
                # 更新缓存
                _nextcloud_client_cache['client'] = client
                _nextcloud_client_cache['last_initialized'] = current_time
                logger.info("Nextcloud客户端初始化成功")
                return client
            else:
                raise ConnectionError("Nextcloud客户端连接验证失败")
        except Exception as e:
            error_msg = f"初始化Nextcloud客户端失败 (尝试 {attempt + 1}/{max_retries}): {str(e)}"
            logger.error(error_msg)

            # 如果是最后一次尝试，抛出异常
            if attempt == max_retries - 1:
                if isinstance(e, ConnectionError):
                    raise
                elif 'timeout' in str(e).lower() or 'connection' in str(e).lower():
                    raise ConnectionError(f"无法连接到Nextcloud服务器: {str(e)}")
                else:
                    raise

            # 指数退避重试
            wait_time = retry_delay * (2 ** attempt)
            logger.info(f"{wait_time}秒后重试...")
            time.sleep(wait_time)

    # 理论上不会到达这里，但为了安全起见
    raise Exception("无法初始化Nextcloud客户端")


def check_client_validity(client):
    """
    验证Nextcloud客户端是否有效

    Args:
        client: Nextcloud客户端实例

    Returns:
        bool: 客户端是否有效
    """
    if not client:
        return False

    try:
        # 尝试列出根目录作为验证
        # 使用较短的超时来快速验证
        original_timeout = client.timeout
        client.timeout = 10  # 临时设置较短的超时

        # 尝试一个轻量级的操作来验证连接
        response = client.list('/')

        # 恢复原始超时
        client.timeout = original_timeout

        # 验证响应是否有效
        return isinstance(response, list) and len(response) >= 0
    except Exception as e:
        logger.warning(f"Nextcloud客户端验证失败: {str(e)}")
        return False


def check_nextcloud_connection():
    """
    检查Nextcloud连接，增强了错误处理和重试机制

    Returns:
        tuple: (是否成功, 消息)
    """
    for attempt in range(3):  # 最多尝试3次
        try:
            # 创建Nextcloud客户端
            nc_client = get_nextcloud_client()

            # 验证连接是否成功
            if nc_client:
                # 尝试列出根目录，验证基本连接
                root_items = nc_client.list('/')
                logger.info(
                    f"Nextcloud连接成功，根目录包含 {len(root_items)} 个项目"
                )

                # 检查上传目录是否存在，尝试创建测试目录验证权限
                test_dir = "ytbot_test_connection"

                # 检查上传目录是否可访问
                try:
                    # 尝试列出上传目录内容
                    if NEXTCLOUD_UPLOAD_DIR:
                        upload_dir_items = nc_client.list(NEXTCLOUD_UPLOAD_DIR)
                        logger.info(
                            f"Nextcloud上传目录 '{NEXTCLOUD_UPLOAD_DIR}' 存在，包含 {
                                len(upload_dir_items)} 个项目")
                    else:
                        raise Exception("上传目录未配置")
                except Exception as e:
                    error_msg = f"检查上传目录失败: {str(e)}"
                    logger.warning(error_msg)
                    if attempt >= 2:  # 最后一次尝试
                        return False, f"Nextcloud连接失败: {error_msg}\n请检查NEXTCLOUD_UPLOAD_DIR路径和权限设置"
                    continue

                # 尝试创建测试目录
                try:
                    if not hasattr(nc_client, 'check') or not nc_client.check(test_dir):
                        nc_client.mkdir(test_dir)
                        logger.info(f"创建测试目录 {test_dir} 成功")
                except Exception as e:
                    error_msg = f"创建测试目录失败: {str(e)}"
                    logger.warning(error_msg)
                    if attempt >= 2:  # 最后一次尝试
                        return False, f"Nextcloud连接失败: {error_msg}\n请检查写入权限"
                    continue

                # 写入测试文件
                test_file = f"{test_dir}/test.txt"
                try:
                    # 由于webdavclient3的upload_sync和upload_from行为差异，使用upload_sync
                    with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp:
                        temp.write("test")
                        temp_path = temp.name

                    try:
                        nc_client.upload_sync(remote_path=test_file, local_path=temp_path)
                        logger.info(f"上传测试文件 {test_file} 成功")
                    finally:
                        # 清理临时文件
                        if os.path.exists(temp_path):
                            os.unlink(temp_path)
                except Exception as e:
                    error_msg = f"上传测试文件失败: {str(e)}"
                    logger.warning(error_msg)
                    if attempt >= 2:  # 最后一次尝试
                        return False, f"Nextcloud连接失败: {error_msg}\n请检查上传权限"
                    continue

                # 清理测试文件和目录
                try:
                    if hasattr(nc_client, 'clean'):
                        nc_client.clean(test_file)
                        nc_client.clean(test_dir)
                        logger.info("清理测试文件和目录成功")
                except Exception as e:
                    logger.warning(f"清理测试文件和目录失败: {str(e)}")

                return True, f"✅ Nextcloud连接成功！\n上传目录 '{NEXTCLOUD_UPLOAD_DIR}' 可访问且权限正常"
            else:
                error_msg = "Nextcloud客户端初始化失败"
                logger.warning(error_msg)
                if attempt >= 2:
                    return False,
                    f"Nextcloud连接失败: {error_msg}\n请检查配置文件中的NEXTCLOUD相关设置"
                continue
        except ValueError as ve:
            error_msg = f"配置值错误: {str(ve)}"
            logger.warning(error_msg)
            if attempt >= 2:
                return False, (f"Nextcloud连接失败: {error_msg}\n"
                               "请检查配置文件中的NEXTCLOUD_URL和NEXTCLOUD_USERNAME设置")
            continue
        except Exception as e:
            error_msg = f"未知错误: {str(e)}"
            logger.warning(error_msg)
            if attempt >= 2:
                return False, f"Nextcloud连接失败: {error_msg}\n请查看日志获取更多详情"
            continue

        # 如果不是最后一次尝试，等待一段时间后重试
        if attempt < 2:
            wait_time = 2 * (attempt + 1)  # 指数退避策略
            logger.info(f"第 {attempt + 1} 次尝试失败，{wait_time} 秒后重试...")
            time.sleep(wait_time)

    # 所有尝试都失败
    return False, "Nextcloud连接失败: 所有重试尝试都失败\n请检查配置和网络连接后重试"


# 下载YouTube视频并根据选择转换为音频或视频
@retry(max_retries=2, delay=5, exceptions=(Exception,))
async def download_and_convert(url, chat_id, download_type='audio'):
    """
    下载YouTube视频并转换为指定格式（MP3音频或MP4视频），然后上传到Nextcloud
    增强了错误处理、超时控制和资源管理

    Args:
        url: YouTube视频链接
        chat_id: Telegram聊天ID，用于发送状态更新
        download_type: 下载类型，'audio'（默认）或 'video'

    Raises:
        Exception: 如果处理过程中发生错误
    """
    temp_dir = None
    progress_task = None
    sent_messages = set()  # 用于跟踪已发送的消息，避免重复

    try:
        # 验证输入参数
        if not url or not isinstance(url, str):
            raise ValueError("无效的YouTube链接")

        if not chat_id:
            raise ValueError("无效的聊天ID")

        # 验证下载类型
        if download_type not in ['audio', 'video']:
            download_type = 'audio'  # 默认使用音频下载

        # 根据下载类型发送开始处理的通知
        if download_type == 'audio':
            process_msg = "开始处理视频并提取音频，请稍候..."
        else:
            process_msg = "开始处理并下载视频，请稍候..."
        await send_message_safely(chat_id, process_msg, sent_messages)

        with tempfile.TemporaryDirectory() as temp_dir:
            # 根据下载类型配置yt-dlp
            if download_type == 'audio':
                # 音频下载配置
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                    'postprocessors': [
                        {
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'mp3',
                            'preferredquality': '192',
                        }
                    ],
                    'quiet': False,
                    'no_warnings': True,
                    'retries': 5,
                    'fragment_retries': 10,
                    'timeout': 600,
                    'socket_timeout': 30,
                    'http_headers': {
                        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                                       'Chrome/91.0.4472.124 Safari/537.36')
                    },
                    'ignoreerrors': False,
                }
            else:
                # 视频下载配置
                ydl_opts = {
                    'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                    'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                    'postprocessors': [
                        {
                            'key': 'FFmpegVideoConvertor',
                            'preferedformat': 'mp4',  # 将视频转换为mp4
                        }
                    ],
                    'quiet': False,
                    'no_warnings': True,
                    'retries': 5,
                    'fragment_retries': 10,
                    'timeout': 600,
                    'socket_timeout': 30,
                    'http_headers': {
                        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                                       'Chrome/91.0.4472.124 Safari/537.36')
                    },
                    'ignoreerrors': False,
                }

            # 发送下载开始的通知
            await send_message_safely(chat_id, "开始下载视频...", sent_messages)

            # 创建一个线程安全的队列来传递进度信息
            progress_queue = asyncio.Queue()

            # 修改ydl_opts，添加不使用async的进度钩子
            ydl_opts['progress_hooks'] = [
                lambda d: progress_queue.put_nowait((d, chat_id))
            ]

            # 定义一个处理进度队列的协程
            async def process_progress_queue():
                last_percent = -1  # 用于限制进度更新频率
                last_status = None  # 用于跟踪状态变化

                while True:
                    try:
                        # 非阻塞获取队列中的进度信息
                        d, cid = await asyncio.wait_for(progress_queue.get(), timeout=2.0)  # 增加超时时间

                        # 增加进度信息处理的容错性
                        if not isinstance(d, dict):
                            logger.warning(f"process_progress_queue: 无效的进度数据类型: {type(d)}")
                            continue

                        # 检查状态是否有效
                        status = d.get('status', '')
                        if not status:
                            continue

                        # 只在状态变化或进度显著变化时更新
                        if status == 'downloading':
                            downloaded_bytes = d.get('downloaded_bytes', 0)
                            total_bytes = d.get('total_bytes', d.get('total_bytes_estimate', 1))
                            percent = downloaded_bytes / total_bytes * 100 if total_bytes else 0

                            # 每增加10%进度或速度/ETA有显著变化时更新
                            if percent - last_percent >= 10 or percent >= 95:
                                last_percent = percent
                                speed = d.get('speed', 0)
                                eta = d.get('eta', 0)

                                # 格式化速度显示
                                if speed > 1024 * 1024:
                                    speed_str = f"{speed / 1024 / 1024:.2f} MB/s"
                                elif speed > 1024:
                                    speed_str = f"{speed / 1024:.2f} KB/s"
                                else:
                                    speed_str = f"{speed:.2f} B/s"

                                # 格式化剩余时间显示
                                if eta > 3600:
                                    eta_str = f"{eta / 3600:.1f} 小时"
                                elif eta > 60:
                                    eta_str = f"{eta / 60:.1f} 分钟"
                                else:
                                    eta_str = f"{eta} 秒"

                                progress_msg = (
                                    f"🎵 下载进度: {percent:.1f}%\n"  # 添加emoji增强可读性
                                    f"⚡ 速度: {speed_str}\n"
                                    f"⏱️ 预计剩余: {eta_str}"
                                )
                                logger.debug(progress_msg)
                                # 不发送详细进度消息，只打印到日志

                        elif status == 'finished' and status != last_status:
                            last_status = status
                            await send_message_safely(cid, "下载完成，正在转换音频...", sent_messages)

                        progress_queue.task_done()
                    except asyncio.TimeoutError:
                        # 超时说明队列为空，继续检查
                        continue
                    except Exception as e:
                        logger.error(f"处理进度队列时出错: {str(e)}")
                        # 出错时继续，不影响主流程
                        continue

            # 启动进度处理任务
            progress_task = asyncio.create_task(process_progress_queue())

            # 在单独的线程中执行下载和转换操作
            async def download_in_thread():
                def _sync_download():
                    try:
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            # 首先尝试提取信息而不下载，检查视频是否可访问
                            info = ydl.extract_info(url, download=False)
                            logger.info(f"成功获取视频信息: {info.get('title', 'unknown')}")

                            # 然后下载视频
                            return ydl.extract_info(url, download=True)
                    except yt_dlp.utils.DownloadError as de:
                        error_msg = f"下载错误: {str(de)}"
                        logger.error(error_msg)
                        # 根据错误类型提供更具体的提示
                        if 'unavailable' in str(de).lower():
                            raise Exception("视频不可用或已被删除")
                        elif 'age' in str(de).lower():
                            raise Exception("视频受年龄限制，无法下载")
                        elif 'copyright' in str(de).lower():
                            raise Exception("视频受版权保护，无法下载")
                        else:
                            raise Exception(error_msg)
                    except yt_dlp.utils.ExtractorError as ee:
                        error_msg = f"解析视频信息时出错: {str(ee)}"
                        logger.error(error_msg)
                        raise Exception("无法解析视频链接，请检查链接是否正确")
                    except Exception as e:
                        logger.error(f"下载过程中出错: {str(e)}")
                        raise Exception(f"下载失败: {str(e)}")

                # 使用to_thread在线程中执行同步下载操作
                # 添加超时控制
                try:
                    return await asyncio.wait_for(
                        asyncio.to_thread(_sync_download),
                        timeout=1200  # 20分钟超时
                    )
                except asyncio.TimeoutError:
                    raise Exception("下载超时，请尝试较短的视频或稍后再试")

            # 执行下载并获取结果
            info = await download_in_thread()

            # 取消进度处理任务
            if progress_task:
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    logger.debug("进度任务已取消")
                except Exception as e:
                    logger.warning(f"取消进度任务时出错: {str(e)}")

            # 确保info不为None
            if not info:
                raise Exception("未能获取视频信息")

            title = info.get('title', 'unknown')
            target_file = None
            target_files = []

            try:
                # 根据下载类型查找对应的文件
                if download_type == 'audio':
                    # 查找MP3文件
                    for root, _, files in os.walk(temp_dir):
                        for file in files:
                            if file.lower().endswith('.mp3'):
                                target_files.append(os.path.join(root, file))

                    # 如果没找到MP3，尝试查找其他音频文件
                    if not target_files:
                        for root, _, files in os.walk(temp_dir):
                            for file in files:
                                if file.lower().endswith(('.mp3', '.m4a', '.wav', '.ogg')):
                                    target_files.append(os.path.join(root, file))
                else:
                    # 查找视频文件
                    for root, _, files in os.walk(temp_dir):
                        for file in files:
                            if file.lower().endswith('.mp4'):
                                target_files.append(os.path.join(root, file))

                    # 如果没找到MP4，尝试查找其他视频文件
                    if not target_files:
                        for root, _, files in os.walk(temp_dir):
                            for file in files:
                                if file.lower().endswith(('.mp4', '.mkv', '.webm')):
                                    target_files.append(os.path.join(root, file))
            except Exception as e:
                logger.error(f"列出临时目录文件时出错: {str(e)}")
                raise Exception(f"访问临时文件失败: {str(e)}")

            if not target_files:
                if download_type == 'audio':
                    raise Exception("转换后的音频文件未找到")
                else:
                    raise Exception("下载的视频文件未找到")

            # 选择第一个找到的文件
            target_file = target_files[0]

            # 对文件名进行规范化处理，确保符合Nextcloud要求
            original_filename = os.path.basename(target_file)
            sanitized_filename = sanitize_filename(original_filename)

            # 如果文件名发生了变化，重命名文件
            if original_filename != sanitized_filename:
                sanitized_file_path = os.path.join(temp_dir, sanitized_filename)
                try:
                    os.rename(target_file, sanitized_file_path)
                    target_file = sanitized_file_path
                    logger.info(f"文件名已规范化: {original_filename} -> {sanitized_filename}")
                except Exception as e:
                    logger.warning(f"重命名文件失败: {str(e)}")
                    # 即使重命名失败，也继续使用原文件
                    # 尝试创建一个新的副本，而不是重命名
                    try:
                        import shutil
                        shutil.copy2(target_file, sanitized_file_path)
                        target_file = sanitized_file_path
                        logger.info(f"文件已复制并重命名: {original_filename} -> {sanitized_filename}")
                    except Exception as copy_err:
                        logger.warning(f"复制文件失败: {str(copy_err)}")
                        # 继续使用原文件
            else:
                logger.info(f"文件名符合要求: {sanitized_filename}")

            # 获取文件大小
            try:
                file_size = os.path.getsize(target_file) / (1000 * 1000)  # 转换为MB
                file_size_str = f"{file_size:.2f} MB"
            except Exception as e:
                logger.error(f"获取文件大小失败: {str(e)}")
                file_size_str = "未知大小"

            # 根据下载类型发送完成通知
            if download_type == 'audio':
                completion_msg = f"✅ 音频 '{title}' 下载转换完成，开始上传到Nextcloud...\n📁 文件大小: {file_size_str}"
            else:
                completion_msg = f"✅ 视频 '{title}' 下载完成，开始上传到Nextcloud...\n📁 文件大小: {file_size_str}"
            logger.info(completion_msg)
            await send_message_safely(chat_id, completion_msg, sent_messages)

            # 上传到Nextcloud
            try:
                # 再次验证Nextcloud连接
                nextcloud_ok, _ = check_nextcloud_connection()
                if not nextcloud_ok:
                    raise Exception("Nextcloud连接不可用，请稍后再试")

                client = get_nextcloud_client()

                # 上传文件
                remote_path = os.path.join(NEXTCLOUD_UPLOAD_DIR, os.path.basename(target_file))

                # 由于webdavclient3的upload_sync方法会自动创建必要的目录结构
                # 所以我们直接尝试上传文件
                # 添加上传超时控制
                upload_success = False
                max_upload_attempts = 2
                for attempt in range(max_upload_attempts):
                    try:
                        # 创建一个函数来包装上传操作，以便添加超时
                        def _sync_upload():
                            client.upload_sync(remote_path=remote_path, local_path=target_file)

                        # 使用asyncio.wait_for添加超时控制
                        await asyncio.wait_for(
                            asyncio.to_thread(_sync_upload),
                            timeout=600  # 10分钟上传超时
                        )
                        upload_success = True
                        break
                    except asyncio.TimeoutError:
                        if attempt == max_upload_attempts - 1:
                            raise Exception("上传超时，请尝试较小的文件或稍后再试")
                        logger.warning(f"上传超时，第{attempt + 2}次尝试...")
                    except Exception as upload_err:
                        if attempt == max_upload_attempts - 1:
                            raise upload_err
                        logger.warning(f"上传失败，第{attempt + 2}次尝试...")
                        # 等待一段时间后重试
                        await asyncio.sleep(5)

                if not upload_success:
                    raise Exception("上传失败，所有重试都已失败")

                # 验证文件是否成功上传
                if client.check(remote_path):
                    # 根据下载类型设置不同的通知消息
                    if download_type == 'audio':
                        file_type_text = '音乐文件'
                    else:
                        file_type_text = '视频文件'

                    # 发送完成通知
                    send_msg = (f"🎉 {file_type_text} '{os.path.basename(target_file)}' "
                                f"已成功上传到Nextcloud！\n"
                                f"📌 路径：{NEXTCLOUD_UPLOAD_DIR}")
                    logger.info(send_msg)
                    await send_message_safely(chat_id, send_msg, sent_messages)
                    logger.warning(f"用户 {chat_id} 上传了文件: {os.path.basename(target_file)}")
                else:
                    raise Exception("上传后的文件验证失败")
            except Exception as e:
                error_msg = f"上传到Nextcloud失败: {str(e)}"
                logger.error(error_msg)
                # 检查是否是临时问题，可以重试
                if 'timeout' in str(e).lower() or 'connection' in str(e).lower():
                    error_msg += "\n\n这可能是临时网络问题，请稍后再试。"
                await send_message_safely(chat_id, error_msg, sent_messages)
                raise
    except Exception as e:
        error_msg = f"处理失败: {str(e)}"
        # 避免重复发送错误消息
        if 'already sent' not in str(e).lower():
            try:
                await send_message_safely(chat_id, error_msg, sent_messages)
            except Exception as msg_err:
                logger.error(f"发送错误消息失败: {str(msg_err)}")
        logger.error(error_msg)
        # 重新抛出异常以便重试装饰器可以处理
        raise
    finally:
        # 确保进度任务被取消
        if progress_task and not progress_task.done():
            try:
                progress_task.cancel()
                # 等待任务取消完成
                await asyncio.wait([progress_task], timeout=1.0)
            except Exception:
                pass


async def send_message_safely(chat_id, text, sent_messages=None):
    """
    安全地发送消息，避免重复发送和处理常见错误

    Args:
        chat_id: Telegram聊天ID
        text: 要发送的消息文本
        sent_messages: 已发送消息的集合，用于避免重复
    """
    # 限制消息长度
    if len(text) > 4096:
        text = text[:4093] + "..."

    # 检查是否重复发送
    if sent_messages and text in sent_messages:
        logger.debug("避免重复发送消息")
        return

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text
        )

        # 记录已发送的消息
        if sent_messages:
            sent_messages.add(text)
    except BadRequest as e:
        # 处理消息过长或其他格式错误
        logger.error(f"发送消息格式错误: {str(e)}")
        # 尝试发送更短的消息
        if len(text) > 100:
            short_text = "操作已尝试，但无法发送详细状态。"
            if short_text not in sent_messages:
                try:
                    await bot.send_message(chat_id=chat_id, text=short_text)
                    if sent_messages:
                        sent_messages.add(short_text)
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"发送消息失败: {str(e)}, 消息: {text}")
        # 不重新抛出异常，避免影响主流程


# 更新进度
async def update_progress(d, chat_id):
    # if d['status'] == 'downloading':
    #     downloaded_bytes = d.get('downloaded_bytes', 0)
    #     total_bytes = d.get('total_bytes', 1)
    #     percent = downloaded_bytes / total_bytes * 100 if total_bytes else 0
    #     speed = d.get('speed', 0)
    #     eta = d.get('eta', 0)

    #     if percent % 20 < 1:  # 每20%进度更新一次
    #         progress_msg = (
    #             f"下载进度: {percent:.1f}%\n"
    #             f"速度: {speed/1024/1024:.2f} MB/s\n"
    #             f"剩余时间: {eta}秒"
    #         )
    #         print(progress_msg)
    #         # await bot.send_message(chat_id=chat_id, text=progress_msg)
    # el
    if d['status'] == 'finished':
        print("下载完成，正在转换音频...")
        await bot.send_message(chat_id=chat_id, text="下载完成，正在转换音频...")


# 处理/start命令
async def start(chat_id):
    await bot.send_message(
        chat_id=chat_id,
        text="欢迎使用YTBot！请发送YouTube链接，我会帮您下载音频并上传到Nextcloud。"
    )


# 处理/help命令
async def help_command(chat_id):
    await bot.send_message(
        chat_id=chat_id,
        text="使用说明：\n1. 发送YouTube链接\n2. 等待下载和转换\n3. 接收上传完成通知\n\n提示：支持普通YouTube视频链接。"
    )


# 处理消息更新
async def process_update(update):
    """
    处理来自Telegram的消息更新，增强了输入验证和错误处理

    Args:
        update: 来自Telegram的更新数据字典
    """
    try:
        # 检查更新数据是否有效
        if not isinstance(update, dict):
            logger.warning("process_update: 无效的更新数据类型: %s", type(update))
            return

        # 提取消息和相关信息
        message = update.get('message', {})

        # 检查消息是否有效
        if not isinstance(message, dict):
            logger.warning("process_update: 无效的消息数据类型: %s", type(message))
            return

        # 提取文本内容（优先从reply_to_message中获取转发的链接）
        text = message.get('text', '')

        # 如果没有文本，检查是否有转发的消息
        if not text:
            reply_to_message = message.get('reply_to_message', {})
            if reply_to_message:
                text = reply_to_message.get('text', '')

        # 提取聊天信息
        chat = message.get('chat', {})
        if not isinstance(chat, dict):
            logger.warning("process_update: 无效的聊天数据类型: %s", type(chat))
            return

        # 获取聊天ID和用户信息
        chat_id = chat.get('id')
        chat_type = chat.get('type', '')
        user = message.get('from', {})
        user_id = user.get('id')
        username = user.get('username', 'unknown')
        
        # 验证必要的字段
        if not chat_id:
            logger.warning("process_update: 无法获取聊天ID")
            return

        # 记录收到的消息（不记录文本内容以保护隐私）
        logger.info("process_update: 收到来自用户 %s (ID: %s) 的消息，聊天类型: %s", username, user_id, chat_type)

        # 处理不同类型的聊天（可选：仅允许私聊）
        if chat_type not in ['private', 'group', 'supergroup']:
            logger.warning(f"process_update: 不支持的聊天类型: {chat_type}")
            return

        # 处理命令
        if text.startswith('/'):
            command = text.split()[0].lower()

            if command == '/start':
                await start(chat_id)
            elif command == '/help':
                await help_command(chat_id)
            else:
                # 未知命令处理
                logger.info(f"process_update: 收到未知命令: {command}")
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"未知命令: {command}\n请使用 /help 查看可用命令。"
                )
        # 处理用户状态
        if user_id in user_states:
            user_state = user_states[user_id]
            # 处理用户选择下载类型的回复
            if user_state.get('state') == 'waiting_for_download_type':
                try:
                    # 使用并发控制
                    async with semaphore:
                        # 获取用户选择和保存的URL
                        choice = text.strip().lower()
                        url = user_state.get('url')

                        # 清除用户状态
                        del user_states[user_id]

                        # 根据用户选择调用不同的下载逻辑
                        if choice == '1' or choice == '音频' or choice == 'mp3':
                            await bot.send_message(
                                chat_id=chat_id,
                                text="您选择了音频MP3格式，开始处理...\n\n请耐心等待，处理时间取决于视频长度和网络状况。"
                            )
                            await download_and_convert(url, chat_id, download_type='audio')
                        elif choice == '2' or choice == '视频' or choice == 'mp4':
                            await bot.send_message(
                                chat_id=chat_id,
                                text="您选择了视频MP4格式，开始处理...\n\n请耐心等待，处理时间取决于视频长度和网络状况。"
                            )
                            await download_and_convert(url, chat_id, download_type='video')
                        else:
                            await bot.send_message(
                                chat_id=chat_id,
                                text="无效的选择。请重新发送YouTube链接，然后回复1(音频MP3)或2(视频MP4)。"
                            )
                except Exception as e:
                    logger.error(f"process_update: 处理用户选择时出错: {str(e)}")
                    # 发送更友好的错误消息
                    error_msg = "处理您的选择时出错，请稍后再试。"
                    try:
                        await bot.send_message(chat_id=chat_id, text=error_msg)
                    except Exception:
                        pass  # 如果发送错误消息也失败，就忽略
        # 处理YouTube链接
        elif text and is_youtube_url(text):
            try:
                # 保存用户状态，等待用户选择下载类型
                user_states[user_id] = {
                    'state': 'waiting_for_download_type',
                    'url': text,
                    'timestamp': time.time()  # 添加时间戳用于过期清理
                }
                # 发送选择消息
                await bot.send_message(
                    chat_id=chat_id,
                    text="检测到YouTube链接！请选择下载类型：\n1. 音频MP3\n2. 视频MP4\n\n请回复1或2，或者直接回复'音频'/'视频'。"
                )

            except Exception as e:
                logger.error(f"process_update: 处理YouTube链接时出错: {str(e)}")
                # 发送更友好的错误消息
                error_msg = "处理视频时出错，请稍后再试。\n\n如果问题持续，请检查链接是否有效，或联系管理员。"
                try:
                    await bot.send_message(chat_id=chat_id, text=error_msg)
                except Exception:
                    pass  # 如果发送错误消息也失败，就忽略
        # 处理空消息或非YouTube链接
        else:
            # 避免对每个非链接消息都回复，减少消息量
            if text:
                logger.info("process_update: 收到非YouTube链接消息")
                await bot.send_message(
                    chat_id=chat_id,
                    text="请发送有效的YouTube链接，或使用 /help 查看使用说明。"
                )
    except BadRequest as e:
        # 处理Telegram API的BadRequest错误（例如消息过长）
        logger.error(f"process_update: Telegram API错误: {str(e)}")
        try:
            if chat_id:
                await bot.send_message(
                    chat_id=chat_id,
                    text="处理您的消息时遇到问题，请尝试发送更短的内容或另一个链接。"
                )
        except Exception:
            pass
    except Exception as e:
        # 捕获所有其他异常
        logger.error(f"process_update: 处理更新时出错: {str(e)}")
        # 记录详细的错误栈信息，便于调试
        import traceback
        logger.debug(traceback.format_exc())

        # 尝试发送错误通知给用户（如果有chat_id）
        if 'chat_id' in locals() and chat_id:
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text="抱歉，处理您的请求时发生了内部错误。\n\n我们已记录此问题，将尽快修复。"
                )
            except Exception:
                pass  # 如果发送错误消息也失败，就忽略


# 消息轮询器
async def message_poller():
    """
    轮询新的Telegram消息并处理，增强了错误处理和稳定性
    """
    last_update_id = None
    # 失败计数器，用于实现指数退避策略
    failure_count = 0
    max_failures = 5
    max_retry_delay = 60  # 最大重试延迟时间（秒）
    # 跟踪正在处理的更新ID及其开始处理时间，防止重复处理和清理超时更新
    processing_updates = set()
    processing_updates_with_time = {}
    
    # 定义清理僵尸更新的协程
    async def cleanup_stale_updates():
        """清理长时间未完成处理的更新"""
        current_time = time.time()
        timeout = 300  # 5分钟超时
        stale_updates = [
            uid for uid, start_time in processing_updates_with_time.items() 
            if current_time - start_time > timeout
        ]
        
        for uid in stale_updates:
            logger.warning(f"清理超时更新: {uid}")
            if uid in processing_updates:
                processing_updates.remove(uid)
            if uid in processing_updates_with_time:
                del processing_updates_with_time[uid]

    while True:
        try:
            # 根据失败次数动态调整超时和重试延迟
            timeout = 30  # 默认超时时间
            retry_delay = min(2 ** failure_count, max_retry_delay)  # 指数退避

            # 获取更新
            updates = await bot.get_updates(offset=last_update_id, timeout=timeout)

            # 重置失败计数器
            if updates or failure_count > 0:
                failure_count = 0
                logger.debug(f"成功获取更新，当前失败计数重置为 {failure_count}")

            for update in updates:
                try:
                    # 检查更新ID是否已经在处理中，避免重复处理
                    if update.update_id in processing_updates:
                        continue

                    # 添加到处理中的集合并记录开始时间
                    processing_updates.add(update.update_id)
                    processing_updates_with_time[update.update_id] = time.time()

                    # 更新last_update_id，确保不重复处理
                    last_update_id = update.update_id + 1

                    # 异步处理每个更新
                    await process_update(update.to_dict())

                    # 短暂休眠，避免处理过于频繁
                    await asyncio.sleep(0.1)
                except Exception as e:
                    # 单独处理每个更新的错误，不影响其他更新
                    logger.error(f"处理单个更新时出错: {str(e)}")
                    # 记录详细的错误栈信息，便于调试
                    import traceback
                    logger.debug(traceback.format_exc())
                finally:
                    # 无论成功或失败，都从处理中集合移除
                    if update.update_id in processing_updates:
                        processing_updates.remove(update.update_id)
                    if update.update_id in processing_updates_with_time:
                        del processing_updates_with_time[update.update_id]

            # 定期清理处理中集合，避免内存泄漏
            if len(processing_updates) > 0 and int(time.time()) % 300 == 0:  # 每5分钟清理一次
                logger.debug(f"开始清理长时间未处理的更新，当前处理中: {len(processing_updates)}")
                await cleanup_stale_updates()
                logger.debug(f"清理完成，剩余处理中: {len(processing_updates)}")

            # 短暂休眠，避免请求过于频繁
            await asyncio.sleep(1)
        except NetworkError as e:
            # 网络错误处理
            failure_count += 1
            logger.error(f"网络错误: {str(e)}. 第{failure_count}次失败，{retry_delay}秒后重试...")
            await asyncio.sleep(retry_delay)
        except RetryAfter as e:
            # 速率限制错误，需要等待指定时间
            retry_after = int(e.retry_after) if hasattr(e, 'retry_after') else 30
            logger.warning(f"请求过于频繁，需要等待 {retry_after} 秒后重试")
            await asyncio.sleep(retry_after)
        except Exception as e:
            # 其他错误处理
            failure_count += 1
            logger.error(f"轮询消息时出错: {str(e)}. 第{failure_count}次失败，{retry_delay}秒后重试...")
            # 记录详细的错误栈信息，便于调试
            import traceback
            logger.debug(traceback.format_exc())

            # 如果失败次数过多，发送通知给管理员
            if failure_count >= max_failures and ADMIN_CHAT_ID:
                try:
                    await bot.send_message(
                        chat_id=ADMIN_CHAT_ID,
                        text=f"⚠️ YTBot警告：\n\n连续{failure_count}次消息轮询失败\n\n最后错误: {str(e)}"
                    )
                except Exception:
                    pass

            await asyncio.sleep(retry_delay)
        finally:
            # 定期记录系统状态
            if int(time.time()) % 3600 == 0:  # 每小时记录一次
                logger.info("消息轮询器运行正常，处理中更新: %d, 最后处理ID: %s", 
                           len(processing_updates), last_update_id)


# 主函数 - 完全异步实现，使用低级API避免事件循环问题
async def main_async():
    global bot, semaphore, main_event_loop

    try:
        # 保存主事件循环的引用
        main_event_loop = asyncio.get_event_loop()

        # 创建Bot实例
        bot = create_bot(TELEGRAM_BOT_TOKEN)
        if not bot:
            raise Exception("无法创建Telegram Bot实例")

        # 创建并发控制信号量
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

        # 启动资源监控任务
        logger.info("启动资源监控任务...")
        monitor_task = asyncio.create_task(resource_monitor())
        
        # 启动网络监控任务
        logger.info("启动网络监控任务...")
        network_task = asyncio.create_task(network_monitor())

        logger.info("YTBot已启动，等待消息...")
        # 启动消息轮询器
        try:
            await message_poller()
        except Exception as e:
            logger.error(f"消息轮询器异常: {str(e)}")
            import traceback
            logger.debug(traceback.format_exc())
        finally:
            # 确保资源监控任务被取消
            if monitor_task:
                monitor_task.cancel()
                try:
                    await monitor_task
                except asyncio.CancelledError:
                    logger.info("资源监控任务已关闭")
            
            # 确保网络监控任务被取消
            if network_task:
                network_task.cancel()
                try:
                    await network_task
                except asyncio.CancelledError:
                    logger.info("网络监控任务已关闭")
    except Exception as e:
        logger.error(f"Bot启动失败: {str(e)}")


def main():
    print("YTBot正在启动...")

    # 设置信号处理
    setup_signal_handlers()

    # 检查必需的配置
    missing_configs, admin_chat_id = check_required_config()

    if missing_configs:
        print(f"错误: 缺少必需的配置项: {', '.join(missing_configs)}")
        print("请编辑config.py文件，填写所有必需的配置项")
        return

    # 不再提前检查bot，而是在main_async中创建

    # 在异步事件循环外执行所有同步操作
    try:
        # 检查yt_dlp版本
        print("检查yt_dlp版本...")
        yt_dlp_ok, yt_dlp_msg = check_yt_dlp_version()
        print(f"yt_dlp检查结果: {yt_dlp_msg}")

        # 检测Nextcloud连接
        print("检测Nextcloud连接...")
        nextcloud_ok, nextcloud_msg = check_nextcloud_connection()
        print(f"Nextcloud连接检查结果: {nextcloud_msg}")

        # 检查系统资源
        try:
            process = psutil.Process(os.getpid())
            memory_info = process.memory_info()
            memory_used_mb = memory_info.rss / 1024 / 1024
            logger.info(f"启动时内存使用: {memory_used_mb:.2f} MB")
        except Exception as e:
            logger.warning(f"无法获取初始内存使用情况: {str(e)}")

        # 发送启动通知给管理员
        if admin_chat_id:
            try:
                # 使用一个完全独立的函数发送启动通知，避免事件循环冲突
                send_start_notification(admin_chat_id, f"{yt_dlp_msg}\n{nextcloud_msg}")
            except Exception as e:
                logger.warning(f"发送启动通知失败: {str(e)}")
        else:
            logger.warning("未设置有效的ADMIN_CHAT_ID，无法发送启动通知")

        print("YTBot已启动，等待telegram消息...")
        # 启动异步事件循环运行机器人
        try:
            asyncio.run(main_async())
        except KeyboardInterrupt:
            logger.info("Bot已停止")
        except asyncio.CancelledError:
            logger.info("任务被取消，正在停止...")
        except Exception as e:
            logger.error(f"Bot运行出错: {str(e)}")
            print(f"错误: Bot运行出错: {str(e)}")
            
            # 发送错误通知给管理员
            if admin_chat_id:
                try:
                    send_start_notification(admin_chat_id, f"❌ YTBot运行错误: {str(e)}")
                except Exception:
                    pass
        finally:
            # 清理资源
            if 'user_states' in globals():
                user_states.clear()
                logger.info("用户状态已清理")
    except Exception as e:
        logger.critical(f"Bot初始化失败: {str(e)}")
        print(f"严重错误: Bot初始化失败: {str(e)}")
        
        # 发送初始化失败通知给管理员
        if admin_chat_id:
            try:
                send_start_notification(admin_chat_id, f"❌ YTBot初始化失败: {str(e)}")
            except Exception:
                pass


# 网络连接检查函数
def check_network_connection(timeout=5):
    """
    检查网络连接状态
    
    Args:
        timeout: 连接超时时间（秒）
    
    Returns:
        bool: True表示网络连接正常，False表示网络连接异常
    """
    try:
        # 尝试连接几个常用的外部服务，提高可靠性
        services = [
            ('8.8.8.8', 53),  # Google DNS
            ('1.1.1.1', 53),  # Cloudflare DNS
            ('9.9.9.9', 53)   # Quad9 DNS
        ]
        
        for host, port in services:
            try:
                socket.create_connection((host, port), timeout=timeout)
                logger.debug(f"网络连接检查成功: {host}:{port}")
                return True
            except (socket.timeout, socket.error):
                continue
        
        # 所有服务都连接失败
        logger.warning("所有网络连接检查点都失败")
        return False
    except Exception as e:
        logger.error(f"执行网络连接检查时出错: {str(e)}")
        return False


# 周期性网络状态检查和恢复协程
async def network_monitor():
    """
    定期检查网络连接状态，如果发现异常则尝试恢复
    """
    logger.info("网络监控任务已启动")
    
    # 记录连续失败次数
    failure_count = 0
    
    while True:
        try:
            # 检查网络连接
            if check_network_connection():
                # 连接恢复
                if failure_count > 0:
                    logger.info(f"网络连接已恢复，之前失败了 {failure_count} 次")
                    failure_count = 0
                    
                    # 如果设置了管理员聊天ID，发送恢复通知
                    if ADMIN_CHAT_ID and bot is not None:
                        try:
                            await bot.send_message(
                                chat_id=ADMIN_CHAT_ID,
                                text="✅ YTBot网络连接已恢复"
                            )
                        except Exception as e:
                            logger.error(f"发送网络恢复通知失败: {str(e)}")
            else:
                # 连接失败
                failure_count += 1
                logger.warning(f"网络连接检查失败，连续失败 {failure_count} 次")
                
                # 如果连续失败超过阈值，发送警告
                if failure_count >= 3 and ADMIN_CHAT_ID and bot is not None:
                    try:
                        await bot.send_message(
                            chat_id=ADMIN_CHAT_ID,
                            text=f"⚠️ YTBot网络连接警告：\n连续 {failure_count} 次检查失败\n请检查服务器网络连接"
                        )
                    except Exception as e:
                        logger.error(f"发送网络警告失败: {str(e)}")
                
                # 尝试执行一些恢复操作
                if failure_count >= 5:
                    logger.info("尝试执行网络恢复操作...")
                    # 重置DNS缓存（在不同系统上可能需要不同的命令）
                    try:
                        if sys.platform.startswith('linux'):
                            os.system('systemd-resolve --flush-caches')
                        elif sys.platform.startswith('darwin'):
                            os.system('dscacheutil -flushcache')
                        elif sys.platform.startswith('win'):
                            os.system('ipconfig /flushdns')
                        logger.info("已尝试刷新DNS缓存")
                    except Exception as e:
                        logger.error(f"执行DNS缓存刷新失败: {str(e)}")
        except Exception as e:
            logger.error(f"网络监控过程中出错: {str(e)}")
            import traceback
            logger.debug(traceback.format_exc())
        
        # 每30秒检查一次网络连接
        await asyncio.sleep(30)


# 资源监控协程
async def resource_monitor():
    """
    定期监控系统资源使用情况，清理过期的用户状态
    防止内存泄漏和资源耗尽
    """
    global user_states
    
    # 设置内存使用阈值（MB）
    MEMORY_THRESHOLD = 512  # 512MB
    # 设置用户状态超时时间（秒）
    USER_STATE_TIMEOUT = 300  # 5分钟
    
    logger.info("资源监控任务已启动")
    
    while True:
        try:
            # 获取当前进程内存使用情况
            process = psutil.Process(os.getpid())
            memory_info = process.memory_info()
            memory_used_mb = memory_info.rss / 1024 / 1024  # 转换为MB
            
            # 记录内存使用情况
            logger.debug("当前内存使用: %.2f MB", memory_used_mb)
            
            # 检查用户状态是否过期
            current_time = time.time()
            expired_users = [user_id for user_id, state_info in user_states.items()
                           if (current_time - state_info.get('timestamp', current_time)) >
                           USER_STATE_TIMEOUT]
            
            # 清理过期用户状态
            for user_id in expired_users:
                logger.debug("清理过期用户状态: %s", user_id)
                del user_states[user_id]
            
            # 如果清理后仍有较多过期状态，记录警告
            if len(expired_users) > 10:
                logger.warning("清理了 %d 个过期用户状态", len(expired_users))
            
            # 检查内存使用是否超过阈值
            if memory_used_mb > MEMORY_THRESHOLD:
                logger.warning("内存使用警告: %.2f MB 超过阈值 %d MB", 
                             memory_used_mb, MEMORY_THRESHOLD)
                
                # 执行更激进的清理
                # 1. 清理所有用户状态
                if user_states:
                    logger.info("内存压力大，清理所有 %d 个用户状态", len(user_states))
                    user_states.clear()
                
                # 2. 尝试清理其他缓存（如果有）
                # 例如: _nextcloud_client_cache 等
                if '_nextcloud_client_cache' in globals() and globals()['_nextcloud_client_cache'].get('client'):
                    logger.info("内存压力大，清理Nextcloud客户端缓存")
                    globals()['_nextcloud_client_cache']['client'] = None
                    
                # 3. 发送警告给管理员
                if ADMIN_CHAT_ID:
                    try:
                        await bot.send_message(
                            chat_id=ADMIN_CHAT_ID,
                            text=f"⚠️ YTBot内存警告：\n当前内存使用: {memory_used_mb:.2f} MB\n已执行自动清理以释放内存"
                        )
                    except Exception as e:
                        logger.error(f"发送内存警告失败: {str(e)}")
        
        except psutil.Error as e:
            logger.error(f"获取系统资源信息失败: {str(e)}")
        except Exception as e:
            logger.error(f"资源监控过程中出错: {str(e)}")
            import traceback
            logger.debug(traceback.format_exc())
        
        # 每5分钟执行一次监控
        await asyncio.sleep(300)


# 全局标志，用于防止重复执行关闭流程
_is_shutting_down = False


# 优雅关闭处理函数
def setup_signal_handlers():
    """
    设置信号处理，确保程序可以优雅地关闭
    清理资源并保存状态
    """
    global _is_shutting_down
    
    def signal_handler(sig, frame):
        global _is_shutting_down
        
        # 防止重复执行关闭流程
        if _is_shutting_down:
            logger.warning("关闭流程已在进行中，忽略重复的信号 %s", sig)
            return
        
        _is_shutting_down = True
        logger.info("收到信号 %s，准备优雅关闭", sig)
        
        # 记录关闭前的状态
        logger.info(
            "关闭前 - 处理中更新数: %d", 
            len(globals().get('processing_updates', []))
        )
        logger.info(
            "关闭前 - 活跃用户状态数: %d", 
            len(globals().get('user_states', {}))
        )
        
        # 发送关闭通知给管理员（如果有）
        if 'ADMIN_CHAT_ID' in globals() and ADMIN_CHAT_ID and 'bot' in globals() and bot is not None:
            try:
                # 简化实现，使用同步方式发送消息
                # 避免复杂的线程和事件循环操作
                try:
                    # 尝试直接发送消息
                    bot.send_message(
                        chat_id=ADMIN_CHAT_ID,
                        text="🛑 YTBot正在关闭，可能是由于系统重启或更新。\n将在完成当前任务后停止。"
                    )
                    logger.info("关闭通知已发送")
                except Exception as msg_e:
                    logger.warning("无法发送关闭通知: %s", 
                             str(msg_e))
            except Exception as e:
                logger.error("处理关闭通知时出错: %s", str(e))
        
        logger.info("YTBot已开始关闭流程")
        
        # 设置全局变量，通知主循环退出
        if 'should_continue' in globals():
            globals()['should_continue'] = False
        
        # 给当前任务一些时间完成
        import time
        time.sleep(1)
        
        # 强制退出
        logger.info("强制退出程序")
        import sys
        sys.exit(0)
    
    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # 终止信号


# 异步版本的启动通知函数
async def send_start_notification_async(chat_id, message):
    """使用主事件循环发送启动通知，避免创建独立的事件循环"""
    try:
        # 验证chat_id格式
        chat_id_int = int(chat_id)

        # 使用现有的bot实例（如果已创建），否则创建一个新实例
        if 'bot' in globals() and bot is not None:
            notification_bot = bot
            logger.debug("使用现有的Bot实例发送启动通知")
        else:
            # 如果主Bot实例尚未创建，创建一个新的实例
            notification_bot = create_bot(TELEGRAM_BOT_TOKEN)
            if not notification_bot:
                raise Exception("无法创建Bot实例发送通知")
            logger.debug("创建新的Bot实例发送启动通知")

        bot_info = None
        try:
            # 获取Bot信息
            bot_info = await notification_bot.get_me()
        except Exception as e:
            logger.warning("获取Bot信息失败: %s", str(e))

        # 构建通知消息
        base_message = "🚀 YTBot已成功启动！\n\n"
        if bot_info:
            base_message += "🤖 机器人名称: %s\n" % bot_info.first_name
            base_message += "🔍 用户名: @%s\n" % bot_info.username
            base_message += "🆔 Bot ID: %s\n\n" % bot_info.id
        base_message += "📊 系统状态:\n%s\n\n" % message
        base_message += "💡 提示: 发送YouTube链接开始下载音乐"

        # 发送消息
        await notification_bot.send_message(
            chat_id=chat_id_int,
            text=base_message
        )
        logger.info("启动通知已成功发送到用户 %s", chat_id_int)
    except Exception as e:
        logger.error("发送启动通知失败: %s", str(e))
        import traceback
        logger.debug(traceback.format_exc())
        raise

# 兼容旧版本的同步函数，用于在同步上下文中调用

def send_start_notification(chat_id, message):
    """同步包装函数，在新线程中运行异步通知函数"""
    import threading
    import traceback
    
    def _send_in_thread():
        try:
            # 创建新的事件循环（在新线程中）
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # 运行异步通知函数
                loop.run_until_complete(send_start_notification_async(chat_id, message))
            finally:
                # 确保关闭事件循环
                loop.close()
        except ValueError as e:
            logger.error(f"值错误: {str(e)}")
        except Exception as e:
            logger.error(f"在线程中发送启动通知失败: {str(e)}")
            logger.debug(traceback.format_exc())

    # 创建并启动线程
    thread = threading.Thread(target=_send_in_thread)
    thread.daemon = True  # 设置为守护线程，主程序结束时自动终止
    thread.start()

    # 等待一小段时间确保线程启动
    time.sleep(0.1)


# 规范化文件名，确保符合Nextcloud要求
def sanitize_filename(filename):
    """
    安全地清理文件名，增强了对各种边缘情况的处理

    Args:
        filename: 原始文件名

    Returns:
        str: 清理后的安全文件名
    """
    # 处理None或空输入
    if filename is None:
        logger.debug("sanitize_filename: 输入为None，使用默认名称")
        return "unknown_file.mp3"

    # 转换为字符串
    filename_str = str(filename)

    # 处理空字符串情况
    if not filename_str.strip():
        logger.debug("sanitize_filename: 输入为空字符串，使用默认名称")
        return "unknown_file.mp3"

    # 移除前后空格
    filename_str = filename_str.strip()

    # 不支持的字符列表（常见于Windows和Linux文件系统）
    invalid_chars = r'<>"/\|?*'

    # 替换不支持的字符为下划线
    for char in invalid_chars:
        filename_str = filename_str.replace(char, '_')

    # 使用正则表达式去除连续的下划线，更高效
    import re
    filename_str = re.sub(r'_+', '_', filename_str)

    # 去除控制字符
    filename_str = ''.join(char for char in filename_str if ord(char) >= 32)

    # 限制文件名长度（Nextcloud推荐不超过255个字符）
    max_length = 150  # 进一步减少长度限制，确保即使URL编码后也不会超过Nextcloud限制
    name, ext = os.path.splitext(filename_str)

    # 计算扩展名长度（包括点号）
    ext_length = len(ext)

    # 为文件名主体计算最大允许长度
    max_name_length = max_length - ext_length

    # 如果扩展名太长，保留基础文件名
    if ext_length > max_length:
        logger.warning(f"sanitize_filename: 扩展名过长: {ext}")
        return "file.mp3"

    # 如果文件名主体太长，截断它
    if len(name) > max_name_length:
        # 保留前一部分和后一部分，中间用...连接
        if max_name_length > 10:  # 确保有足够空间保留有意义的部分
            name = name[:max_name_length - 3] + "..."
        else:
            name = name[:max_name_length]
        filename_str = f"{name}{ext}"
        logger.debug(f"sanitize_filename: 文件名过长，已截断: {filename_str}")

    # 避免使用操作系统保留文件名
    reserved_names = [
        'CON', 'PRN', 'AUX', 'NUL',
        'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
        'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
    ]

    # 不区分大小写地检查保留文件名
    name_without_ext = os.path.splitext(os.path.basename(filename_str))[0].upper()
    counter = 1
    while name_without_ext in reserved_names:
        # 保持原文件名的大小写，但添加数字后缀
        name, ext = os.path.splitext(filename_str)
        filename_str = f"{name}_{counter}{ext}"
        name_without_ext = os.path.splitext(os.path.basename(filename_str))[0].upper()
        counter += 1
        # 避免无限循环
        if counter > 100:
            break

    # 确保文件名不为空且有效
    if not filename_str or filename_str == '.mp3' or filename_str == '_':
        filename_str = 'unnamed_file.mp3'
        logger.debug("sanitize_filename: 文件名无效，使用默认名称")

    # 去除开头和结尾的下划线
    filename_str = filename_str.strip('_')

    # 再次检查文件名是否有效
    if not filename_str or filename_str == '.mp3':
        filename_str = 'unnamed_file.mp3'

    logger.debug(f"sanitize_filename: 原始文件名 '{filename}' 已清理为 '{filename_str}'")
    return filename_str


if __name__ == '__main__':
    main()

# 导入必要的模块
import os
import re
import tempfile
import asyncio
import logging
import requests
from telegram import Bot
from webdav3.client import Client
import yt_dlp

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

# 创建Bot实例
bot = Bot(token=TELEGRAM_BOT_TOKEN)


# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, LOG_LEVEL)
)
logger = logging.getLogger(__name__)


# 并发控制
semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)


# 规范化版本号，去除前导零
def normalize_version(version):
    # 分割版本号并去除每个部分的前导零
    parts = version.split('.')
    normalized_parts = [str(int(part)) if part.isdigit() else part for part in parts]
    return '.'.join(normalized_parts)


# 检查yt_dlp版本是否为最新
def check_yt_dlp_version():
    try:
        # 获取当前安装的yt_dlp版本
        current_version = yt_dlp.version.__version__
        logger.info(f"当前yt_dlp版本: {current_version}")

        # 获取PyPI上的最新版本
        response = requests.get('https://pypi.org/pypi/yt-dlp/json', timeout=5)
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
    youtube_regex = (
        r'(https?://)?(www\.)?'
        r'(youtube|youtu|youtube-nocookie)\.(com|be)/'
        r'(watch\?v=|embed/|v/|.+/|watch\?.+&v=|watch\?v=|embed/|v/|.+/)?'
        r'([^&=%\?]{11})'
    )
    return re.match(youtube_regex, url) is not None


# 初始化NextCloud客户端
def get_nextcloud_client():
    options = {
        'webdav_hostname': f'{NEXTCLOUD_URL}/remote.php/dav/files/{NEXTCLOUD_USERNAME}/',
        'webdav_login': NEXTCLOUD_USERNAME,
        'webdav_password': NEXTCLOUD_PASSWORD
    }
    client = Client(options)
    return client


# 检测Nextcloud连接和扫描目标目录（同步函数）
def check_nextcloud_connection():
    try:
        client = get_nextcloud_client()
        # 测试连接 - 列出根目录内容
        root_items = client.list('/')
        logger.info(f"Nextcloud连接成功，根目录包含 {len(root_items)} 个项目")

        # 检查上传目录是否存在
        try:
            # 尝试列出上传目录内容
            upload_dir_items = client.list(NEXTCLOUD_UPLOAD_DIR)
            logger.info(f"Nextcloud上传目录 '{NEXTCLOUD_UPLOAD_DIR}' 存在，包含 {len(upload_dir_items)} 个项目")
            return True, f"Nextcloud连接成功，上传目录 '{NEXTCLOUD_UPLOAD_DIR}' 存在"
        except Exception:
            logger.warning(f"Nextcloud上传目录 '{NEXTCLOUD_UPLOAD_DIR}' 不存在，将在首次上传时自动创建")
            return True, f"Nextcloud连接成功，但上传目录 '{NEXTCLOUD_UPLOAD_DIR}' 不存在，将在首次上传时自动创建"
    except Exception as e:
        error_msg = f"Nextcloud连接失败: {str(e)}"
        logger.error(error_msg)
        return False, error_msg


# 下载YouTube视频并转换为MP3
async def download_and_convert(url, chat_id):
    try:
        # 发送开始处理的通知
        await bot.send_message(chat_id=chat_id, text="开始处理视频，请稍候...")

        with tempfile.TemporaryDirectory() as temp_dir:
            # 配置yt-dlp
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
            }

            # 发送下载开始的通知
            await bot.send_message(chat_id=chat_id, text="开始下载视频...")

            # 创建一个线程安全的队列来传递进度信息
            progress_queue = asyncio.Queue()

            # 修改ydl_opts，添加不使用async的进度钩子
            ydl_opts['progress_hooks'] = [
                lambda d: progress_queue.put_nowait((d, chat_id))
            ]

            # 定义一个处理进度队列的协程
            async def process_progress_queue():
                while True:
                    try:
                        # 非阻塞获取队列中的进度信息
                        d, cid = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
                        await update_progress(d, cid)
                        progress_queue.task_done()
                    except asyncio.TimeoutError:
                        # 超时说明队列为空，继续检查
                        continue

            # 启动进度处理任务
            progress_task = asyncio.create_task(process_progress_queue())

            # 在单独的线程中执行下载和转换操作
            async def download_in_thread():
                def _sync_download():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        return ydl.extract_info(url, download=True)

                # 使用to_thread在线程中执行同步下载操作
                return await asyncio.to_thread(_sync_download)

            # 执行下载并获取结果
            info = await download_in_thread()

            # 取消进度处理任务
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass
                title = info.get('title', 'unknown')
                mp3_file = os.path.join(temp_dir, f"{title}.mp3")

                # 确保文件存在
                if not os.path.exists(mp3_file):
                    # 尝试查找可能的文件名变体
                    for file in os.listdir(temp_dir):
                        if file.endswith('.mp3'):
                            mp3_file = os.path.join(temp_dir, file)
                            break
                        else:
                            raise Exception("转换后的MP3文件未找到")

                # 对文件名进行规范化处理，确保符合Nextcloud要求
                original_filename = os.path.basename(mp3_file)
                sanitized_filename = sanitize_filename(original_filename)

                # 如果文件名发生了变化，重命名文件
                if original_filename != sanitized_filename:
                    sanitized_file_path = os.path.join(temp_dir, sanitized_filename)
                    os.rename(mp3_file, sanitized_file_path)
                    mp3_file = sanitized_file_path
                    logger.info(f"文件名已规范化: {original_filename} -> {sanitized_filename}")
                else:
                    logger.info(f"文件名符合要求: {sanitized_filename}")

                # 获取MP3文件大小
                file_size = os.path.getsize(mp3_file) / (1000 * 1000)  # 转换为MB

            # 发送转换完成的通知
            send_msg = f"视频 '{title}' 下载转换完成，开始上传到Nextcloud...\n文件大小: {file_size:.2f} MB"
            print(send_msg)
            await bot.send_message(
                chat_id=chat_id,
                text=send_msg
            )

            # 上传到Nextcloud
            try:
                client = get_nextcloud_client()

                # 上传文件
                remote_path = os.path.join(NEXTCLOUD_UPLOAD_DIR, os.path.basename(mp3_file))

                # 由于webdavclient3的upload_sync方法会自动创建必要的目录结构
                # 所以我们直接尝试上传文件
                client.upload_sync(remote_path=remote_path, local_path=mp3_file)

                # 发送完成通知
                send_msg = f"文件 '{mp3_file}' 已成功上传到Nextcloud！\n路径：{NEXTCLOUD_UPLOAD_DIR}"
                print(send_msg)
                await bot.send_message(
                    chat_id=chat_id,
                    text=send_msg
                )
                logger.warning(f"用户 {chat_id} 上传了文件: {mp3_file}")
            except Exception as e:
                error_msg = f"上传到Nextcloud失败: {str(e)}"
                await bot.send_message(chat_id=chat_id, text=error_msg)
                logger.error(error_msg)
                raise
    except Exception as e:
        error_msg = f"处理失败: {str(e)}"
        await bot.send_message(chat_id=chat_id, text=error_msg)
        logger.error(error_msg)
        raise


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
    try:
        message = update.get('message', {})
        text = message.get('text', '')
        chat = message.get('chat', {})
        chat_id = chat.get('id')

        if not chat_id:
            return

        # 处理命令
        if text.startswith('/start'):
            await start(chat_id)
        elif text.startswith('/help'):
            await help_command(chat_id)
        # 处理YouTube链接
        elif is_youtube_url(text):
            # 使用并发控制
            async with semaphore:
                await bot.send_message(
                    chat_id=chat_id,
                    text="检测到YouTube链接，排队处理中..."
                )
                await download_and_convert(text, chat_id)
        else:
            await bot.send_message(chat_id=chat_id, text="请发送有效的YouTube链接。")
    except Exception as e:
        logger.error(f"处理更新时出错: {str(e)}")


# 消息轮询器
async def message_poller():
    last_update_id = None

    while True:
        try:
            # 获取更新
            updates = await bot.get_updates(offset=last_update_id, timeout=30)

            for update in updates:
                last_update_id = update.update_id + 1
                # 异步处理每个更新
                await process_update(update.to_dict())

            # 短暂休眠，避免请求过于频繁
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"轮询消息时出错: {str(e)}")
            # 发生错误时等待一段时间再重试
            await asyncio.sleep(5)


# 主函数 - 完全异步实现，使用低级API避免事件循环问题
async def main_async():
    try:
        logger.info("YTBot已启动，等待消息...")
        # 启动消息轮询器
        await message_poller()
    except Exception as e:
        logger.error(f"Bot启动失败: {str(e)}")


def main():
    print("YTBot正在启动...")
    # 在异步事件循环外执行所有同步操作
    # 检查yt_dlp版本
    print("检查yt_dlp版本...")
    yt_dlp_ok, yt_dlp_msg = check_yt_dlp_version()
    print(f"yt_dlp检查结果: {yt_dlp_msg}")

    # 检测Nextcloud连接
    print("检测Nextcloud连接...")
    nextcloud_ok, nextcloud_msg = check_nextcloud_connection()
    print(f"Nextcloud连接检查结果: {nextcloud_msg}")

    # 发送启动通知给管理员
    if ADMIN_CHAT_ID and ADMIN_CHAT_ID != 'YOUR_TELEGRAM_USER_ID':
        try:
            # 使用一个完全独立的函数发送启动通知，避免事件循环冲突
            send_start_notification(ADMIN_CHAT_ID, f"{yt_dlp_msg}\n{nextcloud_msg}")
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


def send_start_notification(chat_id, message):
    """使用独立的线程发送启动通知，完全避免事件循环冲突"""
    import threading

    def _send_in_thread():
        try:
            # 创建一个全新的Bot实例和事件循环
            from telegram import Bot
            thread_bot = Bot(token=TELEGRAM_BOT_TOKEN)

            # 创建并运行一个独立的事件循环
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(
                thread_bot.send_message(
                    chat_id=chat_id,
                    text=f"YTBot已成功启动！\n{message}"
                )
            )
            loop.close()
        except Exception as e:
            logger.warning(f"在线程中发送启动通知失败: {str(e)}")

    # 创建并启动线程
    thread = threading.Thread(target=_send_in_thread)
    thread.daemon = True  # 设置为守护线程，主程序结束时自动终止
    thread.start()


# 规范化文件名，确保符合Nextcloud要求
def sanitize_filename(filename):
    # Nextcloud支持的文件名规则（基于常见文件系统限制）
    # 1. 去除或替换不支持的字符
    # 2. 限制文件名长度
    # 3. 避免使用保留文件名

    # 不支持的字符列表（常见于Windows和Linux文件系统）
    # 使用原始字符串避免转义问题
    invalid_chars = r'<>"/\|?*'

    # 替换不支持的字符为下划线
    for char in invalid_chars:
        filename = filename.replace(char, '_')

    # 去除连续的下划线
    while '__' in filename:
        filename = filename.replace('__', '_')

    # 去除控制字符
    filename = ''.join(char for char in filename if ord(char) >= 32)

    # 限制文件名长度（Nextcloud推荐不超过255个字符）
    max_length = 150  # 进一步减少长度限制，确保即使URL编码后也不会超过Nextcloud限制
    name, ext = os.path.splitext(filename)
    if len(name) > max_length:
        name = name[:max_length]
        filename = f"{name}{ext}"

    # 避免使用操作系统保留文件名
    reserved_names = [
        'CON', 'PRN', 'AUX', 'NUL', 
        'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9', 
        'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
    ]
    # 不区分大小写地检查保留文件名
    name_without_ext = os.path.splitext(os.path.basename(filename))[0].upper()
    if name_without_ext in reserved_names:
        # 保持原文件名的大小写，但添加后缀
        name, ext = os.path.splitext(filename)
        filename = f"{name}_1{ext}"

    # 确保文件名不为空
    if not filename or filename == '.mp3':
        filename = 'unnamed_file.mp3'

    return filename


if __name__ == '__main__':
    main()

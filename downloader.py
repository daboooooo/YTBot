import os
import re
import tempfile
import asyncio
import threading
import yt_dlp
from logger import get_logger
from config import CONFIG


logger = get_logger(__name__)


class DownloadContext:
    """
    下载上下文类，用于管理单个下载任务的状态
    替代全局变量，支持每个下载任务独立控制
    """
    def __init__(self):
        self._cancelled = False
        self._lock = threading.Lock()

    def cancel(self):
        """取消当前下载任务"""
        with self._lock:
            self._cancelled = True
            logger.info("已设置下载取消标志，当前下载任务将在适当时机停止")

    def reset(self):
        """重置下载取消标志"""
        with self._lock:
            self._cancelled = False

    def is_cancelled(self):
        """检查当前下载任务是否已取消"""
        with self._lock:
            return self._cancelled


# 全局下载上下文，用于兼容旧的全局取消机制
_global_download_context = DownloadContext()


# 下载控制函数，兼容旧的全局取消机制
def cancel_all_downloads():
    """取消所有正在进行的下载"""
    _global_download_context.cancel()


def reset_download_cancelled():
    """重置下载取消标志"""
    _global_download_context.reset()


def is_download_cancelled():
    """检查是否已取消下载"""
    return _global_download_context.is_cancelled()


# 验证YouTube链接格式
def is_youtube_url(url):
    # 首先确保url不为空且为字符串类型
    if not url or not isinstance(url, str):
        return False

    # 去除可能的前后空格
    url = url.strip()

    # 检查是否是播放列表链接
    if is_youtube_playlist(url):
        return True

    youtube_regex = (
        r'(https?://)?(www\.)?'
        r'(youtube|youtu|youtube-nocookie)\.(com|be)/'
        r'(watch\?v=|embed/|v/|.+/|watch\?.+&v=|watch\?v=|embed/|v/|.+/)?'
        r'([^&=%\?]{11})'
    )
    return re.match(youtube_regex, url) is not None


# 验证YouTube播放列表链接
def is_youtube_playlist(url):
    """
    检查URL是否为YouTube播放列表链接

    Args:
        url: 待检查的URL

    Returns:
        bool: 如果是播放列表链接返回True，否则返回False
    """
    if not url or not isinstance(url, str):
        return False

    url = url.strip()

    # 播放列表链接格式正则表达式
    playlist_regex = (
        r'(https?://)?(www\.)?'
        r'(youtube|youtu|youtube-nocookie)\.(com|be)/'
        r'(playlist|watch\?.*list=)'
    )

    return re.match(playlist_regex, url) is not None


# 获取播放列表ID
def get_playlist_id(url):
    """
    从播放列表URL中提取播放列表ID

    Args:
        url: YouTube播放列表URL

    Returns:
        str: 播放列表ID，如果不是播放列表返回None
    """
    if not is_youtube_playlist(url):
        return None

    # 尝试从URL中提取list参数
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    query_params = urllib.parse.parse_qs(parsed.query)

    if 'list' in query_params:
        return query_params['list'][0]

    return None


def sanitize_filename(filename):
    """
    清理文件名，移除非法字符并限制长度

    Args:
        filename: 原始文件名

    Returns:
        str: 清理后的文件名
    """
    # 移除或替换非法字符
    sanitized = re.sub(r'[\\/:*?"<>|]', '_', filename)
    # 移除不可打印字符
    sanitized = ''.join(char for char in sanitized if ord(char) >= 32)

    # 限制文件名长度
    if len(sanitized) > 200:
        name, ext = os.path.splitext(sanitized)
        sanitized = name[:200 - len(ext)] + ext

    return sanitized


def _setup_download_options(temp_dir, download_type):
    """
    设置yt-dlp下载选项

    Args:
        temp_dir: 临时目录路径
        download_type: 下载类型，'audio'或'video'

    Returns:
        dict: 配置好的yt-dlp选项
    """
    # 创建基础配置
    ydl_opts = {
        'outtmpl': os.path.join(temp_dir, '%(title).50s.%(ext)s'),  # 限制标题长度为50个字符
        'quiet': CONFIG['download']['quiet'],
        'no_warnings': False,  # 设为False以查看更多警告信息
        'retries': CONFIG['download']['retries'],
        'fragment_retries': CONFIG['download']['fragment_retries'],
        'timeout': CONFIG['download']['timeout'],
        'socket_timeout': CONFIG['download']['socket_timeout'],
        'http_headers': CONFIG['download']['http_headers'],
        'ignoreerrors': CONFIG['download']['ignore_errors'],
        'ignore_no_formats_error': CONFIG['download'].get('ignore_no_formats_error', True),
        'allow_playlist_files': CONFIG['download'].get('allow_playlist_files', True),
        'sleep_interval_requests': CONFIG['download'].get('sleep_interval_requests', 2),
        'sleep_interval': CONFIG['download'].get('sleep_interval', 5),
        'max_sleep_interval': CONFIG['download'].get('max_sleep_interval', 30),
        'prefer_ffmpeg': CONFIG['download'].get('prefer_ffmpeg', True),
        # 添加格式选择参数，避免SABR streaming格式问题
        'format': 'bestvideo[ext!=webm][height<=1080]+bestaudio[ext!=webm]/best[ext!=webm]',
        'postprocessors': [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}],
    }

    # 根据下载类型添加特定配置
    if download_type == 'audio':
        # 音频下载配置
        ydl_opts.update({
            'format': CONFIG['download']['audio_format'],
            'postprocessors': [
                {
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': CONFIG['download']['audio_codec'],
                    'preferredquality': CONFIG['download']['audio_quality'],
                }
            ],
        })
    else:
        # 视频下载配置
        # 使用配置中的视频格式，默认优化为优先选择mp4格式
        video_format = CONFIG['download']['video_format']

        ydl_opts.update({
            'format': video_format,
            'merge_output_format': CONFIG['download'].get('merge_output_format', 'mp4'),
        })

        # 添加后处理配置以确保格式正确
        ydl_opts['postprocessors'] = [
            {
                'key': 'FFmpegVideoConvertor',
                'preferedformat': CONFIG['download']['video_output_format'],
            }
        ]
        logger.info("使用视频+音频合并模式，可能需要较长处理时间")

    return ydl_opts


async def _process_progress_queue(progress_queue, progress_callback, check_cancel,
                                  download_context=None):
    """
    处理进度队列，向用户报告下载进度

    Args:
        progress_queue: 进度信息队列
        progress_callback: 进度回调函数
        check_cancel: 是否检查取消标志
        download_context: 下载上下文对象，用于检查取消状态
    """
    last_percent = -1  # 用于限制进度更新频率
    last_status = None  # 用于跟踪状态变化

    # 如果没有提供下载上下文，使用全局上下文
    if download_context is None:
        download_context = _global_download_context

    # 辅助函数，检查是否取消下载
    def _is_cancelled():
        return download_context.is_cancelled()

    while True:
        # 检查是否取消下载
        if check_cancel and _is_cancelled():
            logger.info("下载已取消，停止进度处理")
            break

        try:
            # 非阻塞获取队列中的进度信息
            d = await asyncio.wait_for(progress_queue.get(), timeout=2.0)

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

                # 根据配置的进度更新频率更新
                update_interval = CONFIG['download']['progress_update_interval']
                if (percent - last_percent >= update_interval or percent >= 95):
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

                    progress_info = {
                        'status': 'downloading',
                        'percent': percent,
                        'speed': speed_str,
                        'eta': eta_str
                    }
                    # 调用进度回调
                    try:
                        await progress_callback(progress_info)
                    except Exception as e:
                        logger.error(f"进度回调执行失败: {str(e)}")

            elif status == 'finished' and status != last_status:
                last_status = status
                progress_info = {'status': 'finished'}
                try:
                    await progress_callback(progress_info)
                except Exception as e:
                    logger.error(f"进度回调执行失败: {str(e)}")

            progress_queue.task_done()
        except asyncio.TimeoutError:
            # 超时说明队列为空，继续检查
            continue
        except Exception as e:
            logger.error(f"处理进度队列时出错: {str(e)}")
            # 出错时继续，不影响主流程
            continue


async def download_video(url, download_type='audio', progress_callback=None,
                         video_info=None, check_cancel=False, download_context=None):
    """
    下载YouTube视频并转换为指定格式
    修复了上传失败后重复下载的问题，返回本地文件路径以便后续上传

    Args:
        url: YouTube视频链接
        download_type: 下载类型，'audio'（默认）或 'video'
        progress_callback: 可选的进度回调函数
        video_info: 可选的已提取的视频信息，如果提供则避免重复提取
        check_cancel: 是否检查取消标志
        download_context: 可选的下载上下文对象，用于管理下载状态

    Returns:
        tuple: (文件路径, 视频信息)

    Raises:
        Exception: 如果下载过程中发生错误
    """
    # 如果没有提供下载上下文，使用全局上下文
    if download_context is None:
        download_context = _global_download_context

    # 辅助函数，检查是否取消下载
    def _is_cancelled():
        return download_context.is_cancelled()
    temp_dir = None

    try:
        # 验证输入参数
        if not url or not isinstance(url, str):
            raise ValueError("无效的YouTube链接")

        # 检查是否取消下载
        if check_cancel and _is_cancelled():
            logger.info(f"下载已取消，停止下载视频: {url}")
            if progress_callback:
                await progress_callback({'status': 'cancelled', 'error': '下载已取消'})
            raise Exception("下载已取消")

        # 验证下载类型
        if download_type not in ['audio', 'video']:
            download_type = 'audio'  # 默认使用音频下载

        # 创建临时目录
        temp_dir = tempfile.mkdtemp()
        logger.info(f"创建临时目录: {temp_dir}")

        # 设置下载选项
        ydl_opts = _setup_download_options(temp_dir, download_type)

        # 如果提供了进度回调，设置进度钩子
        progress_queue = None
        if progress_callback:
            progress_queue = asyncio.Queue()
            ydl_opts['progress_hooks'] = [
                lambda d: progress_queue.put_nowait(d)
            ]

        # 启动进度处理任务
        progress_task = (
            asyncio.create_task(_process_progress_queue(progress_queue, progress_callback,
                                                        check_cancel, download_context))
            if progress_queue else None
        )

        def _sync_download():
            """
            同步下载视频，包含取消检查和错误处理

            Returns:
                tuple: (文件路径, 视频信息)
            """
            try:
                # 添加取消检查钩子
                def cancel_check_hook(d):
                    # 检查取消标志
                    if _is_cancelled():
                        logger.info("检测到取消标志，停止下载")
                        # 引发异常来停止下载
                        raise yt_dlp.utils.DownloadError("下载已取消")

                # 保存原始的progress_hooks
                original_hooks = ydl_opts.get('progress_hooks', [])
                # 添加取消检查钩子
                ydl_opts['progress_hooks'] = original_hooks + [cancel_check_hook]

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # 如果已经提供了视频信息，直接使用，避免重复提取
                    if video_info:
                        info = video_info
                        logger.info(f"使用已提供的视频信息: {info.get('title', 'unknown')}")
                        # 直接下载视频
                        info = ydl.extract_info(url, download=True)
                        logger.info(f"视频下载完成: {info.get('title', 'unknown')}")
                    else:
                        # 首先尝试提取信息而不下载，检查视频是否可访问
                        info = ydl.extract_info(url, download=False)
                        logger.info(f"成功获取视频信息: {info.get('title', 'unknown')}")

                        # 然后下载视频
                        info = ydl.extract_info(url, download=True)
                        logger.info(f"视频下载完成: {info.get('title', 'unknown')}")
                        # 返回文件路径和视频信息
                        if 'entries' in info:
                            # 处理播放列表（通常不应该到达这里，但为了健壮性保留）
                            files = []
                            for entry in info['entries']:
                                if entry:
                                    files.append(ydl.prepare_filename(entry))
                            file_path = files[0] if files else None
                        else:
                            # 单个视频
                            file_path = ydl.prepare_filename(info)
                        return file_path, info
            except yt_dlp.utils.DownloadError as de:
                error_msg = f"下载错误: {str(de)}"
                logger.error(f"[下载错误] URL: {url}, 错误详情: {str(de)}")
                # 检查是否是因为取消而失败
                if '下载已取消' in str(de):
                    raise Exception("下载已取消")
                # 根据错误类型提供更具体的提示
                error_lower = str(de).lower()
                if 'unavailable' in error_lower or 'not found' in error_lower:
                    raise Exception("视频不可用或已被删除")
                elif 'age' in error_lower:
                    raise Exception("视频受年龄限制，无法下载")
                elif 'copyright' in error_lower:
                    raise Exception("视频受版权保护，无法下载")
                elif 'format' in error_lower or 'no formats' in error_lower:
                    raise Exception("无法找到可用的视频格式，请尝试不同的视频")
                elif 'network' in error_lower or 'connection' in error_lower:
                    raise Exception("网络连接错误，请检查您的网络设置")
                elif 'sabr' in error_lower:
                    raise Exception("视频使用了不支持的SABR流媒体格式，请尝试其他视频")
                else:
                    raise Exception(f"下载失败: {error_msg}")
            except yt_dlp.utils.ExtractorError as ee:
                error_msg = f"解析视频信息时出错: {str(ee)}"
                logger.error(f"[解析错误] URL: {url}, 错误详情: {str(ee)}")
                if 'invalid url' in str(ee).lower():
                    raise Exception("无效的视频链接格式，请检查链接是否正确")
                else:
                    raise Exception("无法解析视频链接，请检查链接是否正确")
            except Exception as e:
                logger.error(f"[未知错误] URL: {url}, 错误类型: {type(e).__name__}, 详情: {str(e)}")
                raise Exception(f"下载过程中发生未知错误: {str(e)}")

        # 在单独的线程中执行下载和转换操作
        async def download_in_thread():
            """
            在单独的线程中执行下载操作，添加超时控制

            Returns:
                tuple: (文件路径, 视频信息)
            """
            # 使用to_thread在线程中执行同步下载操作
            # 添加超时控制
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(_sync_download),
                    timeout=CONFIG['download']['timeout']  # 从配置中获取超时设置
                )
            except asyncio.TimeoutError:
                raise Exception(f"下载超时，已超过配置的{CONFIG['download']['timeout']}秒限制")

        def _find_target_files(temp_dir, download_type):
            """
            根据下载类型查找对应的目标文件

            Args:
                temp_dir: 临时目录路径
                download_type: 下载类型，'audio'或'video'

            Returns:
                list: 找到的目标文件路径列表
            """
            target_files = []
            try:
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

            return target_files

        def _sanitize_target_file(target_file, temp_dir):
            """
            对目标文件进行规范化处理，确保文件名符合要求

            Args:
                target_file: 原始文件路径
                temp_dir: 临时目录路径

            Returns:
                str: 规范化后的文件路径
            """
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

            return target_file

        # 执行下载并获取结果
        result = await download_in_thread()

        # 检查是否取消下载
        if check_cancel and _is_cancelled():
            logger.info("下载已取消，处理结果")
            if progress_callback:
                await progress_callback({'status': 'cancelled', 'error': '下载已取消'})
            raise Exception("下载已取消")

        # 取消进度处理任务
        if progress_task:
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                logger.debug("进度任务已取消")
            except Exception as e:
                logger.warning(f"取消进度任务时出错: {str(e)}")

        # 确保result不为None
        if not result:
            raise Exception("未能获取视频信息")

        # 解析返回的元组
        if isinstance(result, tuple) and len(result) == 2:
            # 只需要info部分，file_path暂不使用
            _, info = result
        else:
            # 兼容旧的返回格式
            info = result

        # 根据下载类型查找对应的文件
        target_files = _find_target_files(temp_dir, download_type)

        if not target_files:
            if download_type == 'audio':
                raise Exception("转换后的音频文件未找到")
            else:
                raise Exception("下载的视频文件未找到")

        # 选择第一个找到的文件
        target_file = target_files[0]

        # 对文件名进行规范化处理
        target_file = _sanitize_target_file(target_file, temp_dir)

        # 返回文件路径和视频信息，使用字典格式以便调用方通过.get()方法访问
        return {'file_path': target_file, 'info': info, 'cancelled': False}
    except Exception:
        # 清理临时目录
        if temp_dir and os.path.exists(temp_dir):
            try:
                import shutil
                shutil.rmtree(temp_dir)
                logger.info(f"已清理临时目录: {temp_dir}")
            except Exception as cleanup_err:
                logger.warning(f"清理临时目录失败: {str(cleanup_err)}")
        raise


async def download_playlist(url, download_type='audio', progress_callback=None,
                            max_videos=None, upload_callback=None):
    """
    下载YouTube播放列表中的视频

    Args:
        url: YouTube播放列表链接
        download_type: 下载类型，'audio'（默认）或 'video'
        progress_callback: 可选的进度回调函数
        max_videos: 最大下载视频数量，None表示下载所有视频
        upload_callback: 可选的上传回调函数，用于边下载边上传

    Returns:
        dict: 包含播放列表信息和视频下载结果的字典
    """
    # 创建播放列表专用的下载上下文
    playlist_download_context = DownloadContext()

    # 重置全局取消标志（兼容旧代码）
    reset_download_cancelled()

    if not is_youtube_playlist(url):
        raise ValueError("提供的链接不是YouTube播放列表")

    playlist_id = get_playlist_id(url)
    logger.info(f"开始下载播放列表: {playlist_id}")

    # 创建临时目录存储所有视频
    temp_dir = None
    downloaded_files = []
    video_results = []

    try:
        temp_dir = tempfile.mkdtemp()
        logger.info(f"为播放列表创建临时目录: {temp_dir}")

        # 获取播放列表信息
        ydl_opts = {
            'quiet': CONFIG['download']['quiet'],
            'no_warnings': CONFIG['download']['no_warnings'],
            'extract_flat': True,  # 只提取信息不下载
            'ignoreerrors': CONFIG['download']['ignore_errors'],
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                playlist_info = ydl.extract_info(url, download=False)

            if playlist_info is None:
                raise Exception("无法获取播放列表信息，请检查URL是否有效或播放列表是否可访问")

            playlist_title = playlist_info.get('title', 'Unknown Playlist')
        except Exception as e:
            # 捕获并重新抛出带有详细信息的异常
            error_message = str(e)
            logger.error(f"获取播放列表信息时出错: {error_message}")

            # 针对常见错误类型提供更明确的错误信息
            if 'unviewable' in error_message.lower():
                raise Exception(f"播放列表无法查看: {error_message}")
            elif 'This playlist type is unviewable' in error_message:
                raise Exception("该类型的播放列表无法查看，请尝试其他播放列表")
            elif 'private' in error_message.lower():
                raise Exception("播放列表是私有的，无法访问")
            elif 'not found' in error_message.lower():
                raise Exception("播放列表未找到，请检查链接是否正确")
            else:
                raise Exception(f"获取播放列表信息失败: {error_message}")

        # 检查是否取消下载
        if playlist_download_context.is_cancelled():
            logger.info("下载已取消，退出下载播放列表")
            if progress_callback:
                await progress_callback({'status': 'cancelled', 'error': '下载已取消'})
            return {'playlist_id': playlist_id, 'videos': video_results,
                    'title': playlist_title}

        entries = playlist_info.get('entries', [])

        if not entries:
            raise Exception("播放列表为空")

        total_videos = len(entries)
        actual_max_videos = max_videos if max_videos and max_videos > 0 else total_videos
        actual_max_videos = min(actual_max_videos, total_videos)

        logger.info(f"播放列表 '{playlist_title}' 包含 {total_videos} 个视频，将下载其中 {actual_max_videos} 个")

        # 向用户报告开始下载播放列表
        if progress_callback:
            await progress_callback({
                'status': 'playlist_start',
                'title': playlist_title,
                'total_videos': total_videos,
                'videos_to_download': actual_max_videos
            })

        # 逐个下载视频
        for index, entry in enumerate(entries[:actual_max_videos]):
            # 检查是否取消下载
            if playlist_download_context.is_cancelled():
                logger.info(f"下载已取消，停止在视频 {index + 1}/{actual_max_videos}")
                if progress_callback:
                    await progress_callback({'status': 'cancelled', 'error': '下载已取消'})
                break

            video_url = entry.get('url', f"https://www.youtube.com/watch?v={entry.get('id')}")
            video_title = entry.get('title', f"Video {index + 1}")

            logger.info(f"开始下载视频 {index + 1}/{actual_max_videos}: {video_title}")

            # 报告当前正在下载的视频信息
            if progress_callback:
                await progress_callback({
                    'status': 'video_start',
                    'index': index + 1,
                    'total': actual_max_videos,
                    'title': video_title
                })

            # 尝试下载视频
            max_retries = CONFIG['download'].get('video_retries', 3)
            retry_interval = CONFIG['download'].get('retry_interval', 10)
            retry_count = 0
            success = False
            video_result = {'success': False, 'file_path': None, 'info': None, 'error': None}

            while retry_count <= max_retries and not success:
                # 检查是否取消下载
                if playlist_download_context.is_cancelled():
                    logger.info(f"下载已取消，停止在视频 {index + 1}/{actual_max_videos}")
                    if progress_callback:
                        await progress_callback({
                            'status': 'cancelled',
                            'video_index': index + 1,
                            'total_videos': actual_max_videos,
                            'error': '下载已取消'
                        })
                    break
                try:
                    # 使用视频特定的进度回调包装器
                    def video_progress_wrapper(info):
                        # 添加视频索引信息到进度回调
                        wrapped_info = info.copy()
                        wrapped_info['video_index'] = index + 1
                        wrapped_info['total_videos'] = actual_max_videos
                        wrapped_info['video_title'] = video_title
                        if retry_count > 0:
                            wrapped_info['retry_count'] = retry_count
                        return progress_callback(wrapped_info)

                    # 下载单个视频，传入已提取的视频信息避免重复提取和播放列表专用的下载上下文
                    result = await download_video(
                        video_url,
                        download_type=download_type,
                        progress_callback=video_progress_wrapper if progress_callback else None,
                        video_info=entry,  # 将从播放列表中提取的视频信息直接传入
                        check_cancel=True,  # 添加取消检查标志
                        download_context=playlist_download_context  # 传入播放列表专用的下载上下文
                    )

                    # 检查是否取消下载
                    if playlist_download_context.is_cancelled():
                        logger.info(f"下载已取消，处理视频 {index + 1}/{actual_max_videos}")
                        if progress_callback:
                            await progress_callback({
                                'status': 'cancelled',
                                'video_index': index + 1,
                                'total_videos': actual_max_videos,
                                'error': '下载已取消'
                            })
                        break

                    # 确保正确处理download_video的返回值
                    if isinstance(result, tuple) and len(result) >= 2:
                        file_path, video_info = result
                    else:
                        # 兼容处理，确保有有效的返回值
                        is_collection = isinstance(result, (list, tuple))
                        file_path = result[0] if is_collection and result else None
                        video_info = result[1] if is_collection and len(result) > 1 else None

                    # 更新视频结果信息
                    video_result = {
                        'success': True,
                        'file_path': file_path,
                        'info': video_info,
                        'title': video_title,
                        'index': index + 1,
                        'duration_string': video_info.get('duration_string', '未知时长')
                    }

                    downloaded_files.append((file_path, video_info))
                    logger.info(f"视频 {index + 1}/{actual_max_videos} 下载完成: {video_title}")

                    # 报告视频下载完成
                    if progress_callback:
                        await progress_callback({
                            'status': 'video_complete',
                            'index': index + 1,
                            'total': actual_max_videos,
                            'title': video_title,
                            'retry_count': retry_count if retry_count > 0 else None
                        })

                    # 如果提供了上传回调，则立即上传
                    if upload_callback and file_path:
                        logger.info(f"开始上传视频 {index + 1}/{actual_max_videos}: {video_title}")
                        # 调用上传回调函数
                        await upload_callback(video_result)

                    success = True

                except Exception as e:
                    retry_count += 1
                    error_msg = (f"下载视频 {index + 1}/{actual_max_videos} "
                                 f"失败（尝试 {retry_count}/{max_retries + 1}）: {str(e)}")
                    logger.error(error_msg)

                    # 报告错误
                    if progress_callback:
                        await progress_callback({
                            'status': 'video_error',
                            'index': index + 1,
                            'total': actual_max_videos,
                            'title': video_title,
                            'error': str(e),
                            'retry_count': retry_count,
                            'max_retries': max_retries
                        })

                    # 如果还有重试机会，等待一段时间后重试
                    if retry_count <= max_retries:
                        wait_time = retry_interval * (2 ** (retry_count - 1))  # 指数退避
                        max_wait = CONFIG['download'].get('max_retry_interval', 60)
                        wait_time = min(wait_time, max_wait)

                        logger.info(
                            f"将在 {wait_time:.2f} 秒后重试下载视频 "
                            f"{index + 1}/{actual_max_videos}: {video_title}"
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        # 重试次数用完，跳过这个视频
                        video_result['error'] = str(e)
                        logger.warning(
                            f"所有重试都失败，跳过视频 "
                            f"{index + 1}/{actual_max_videos}: {video_title}"
                        )
                        if progress_callback:
                            await progress_callback({
                                'status': 'video_skipped',
                                'index': index + 1,
                                'total': actual_max_videos,
                                'title': video_title,
                                'error': str(e)
                            })
                        # 继续下载其他视频

            # 保存每个视频的结果
            video_results.append(video_result)

        # 报告播放列表下载完成
        if progress_callback:
            await progress_callback({
                'status': 'playlist_complete',
                'downloaded_count': len([v for v in video_results if v['success']]),
                'total_count': actual_max_videos
            })

        logger.info(f"播放列表下载完成，成功下载 {len(downloaded_files)}/{actual_max_videos} 个视频")
        return {'playlist_id': playlist_id, 'videos': video_results, 'title': playlist_title}

    except Exception as e:
        logger.error(f"播放列表下载失败: {str(e)}")

        if progress_callback:
            await progress_callback({
                'status': 'playlist_error',
                'error': str(e)
            })

        raise
    finally:
        # 注意：这里不清理临时目录，因为下载的文件需要在外部使用
        # 外部调用者负责在使用完后清理这些文件
        pass


# 规范化版本号，去除前导零
def normalize_version(version):
    # 分割版本号并去除每个部分的前导零
    parts = version.split('.')
    normalized_parts = [str(int(part)) if part.isdigit() else part for part in parts]
    return '.'.join(normalized_parts)


# 检查yt_dlp版本是否为最新
def check_yt_dlp_version():
    if not CONFIG['download']['check_yt_dlp_version']:
        logger.info("yt_dlp版本检查已禁用")
        return True, "yt_dlp版本检查已禁用"

    import requests
    try:
        # 获取当前安装的yt_dlp版本
        # yt_dlp.version模块中定义了__version__属性
        current_version = yt_dlp.version.__version__
        logger.info(f"当前yt_dlp版本: {current_version}")

        # 获取PyPI上的最新版本，添加超时设置
        response = requests.get(
            'https://pypi.org/pypi/yt-dlp/json',
            timeout=CONFIG['download']['version_check_timeout']
        )
        response.raise_for_status()
        latest_version = response.json()['info']['version']
        logger.info(f"最新yt_dlp版本: {latest_version}")

        # 规范化版本号并比较
        normalized_current = normalize_version(current_version)
        normalized_latest = normalize_version(latest_version)

        if normalized_current < normalized_latest:
            logger.warning(f"yt_dlp版本已过时! 当前版本: {current_version}, 最新版本: {latest_version}")
            logger.warning("建议运行: pip install --upgrade yt-dlp")
            warning_msg = (f"yt_dlp版本已过时! 当前版本: {current_version}, "
                           f"最新版本: {latest_version}\n建议运行: "
                           f"pip install --upgrade yt-dlp")
            logger.warning(warning_msg)
            return False, warning_msg
        else:
            logger.info("yt_dlp已是最新版本")
            return True, f"yt_dlp已是最新版本: {current_version}"
    except Exception as e:
        error_msg = f"检查yt_dlp版本时出错: {str(e)}"
        logger.error(error_msg)
        return False, error_msg

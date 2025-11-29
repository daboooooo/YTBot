import os
import re
import tempfile
import asyncio
import yt_dlp
from logger import get_logger
from config import CONFIG

logger = get_logger(__name__)


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


def sanitize_filename(filename):
    """
    规范化文件名，确保符合Nextcloud要求

    Args:
        filename: 原始文件名

    Returns:
        str: 规范化后的文件名
    """
    # 移除或替换不允许的字符
    invalid_chars = r'[\\/:*?"<>|]'
    sanitized = re.sub(invalid_chars, '_', filename)

    # 移除控制字符
    sanitized = ''.join(char for char in sanitized if ord(char) >= 32)

    # 限制文件名长度
    if len(sanitized) > 200:
        name, ext = os.path.splitext(sanitized)
        sanitized = name[:200 - len(ext)] + ext

    return sanitized


async def download_video(url, download_type='audio', progress_callback=None):
    """
    下载YouTube视频并转换为指定格式
    修复了上传失败后重复下载的问题，返回本地文件路径以便后续上传

    Args:
        url: YouTube视频链接
        download_type: 下载类型，'audio'（默认）或 'video'
        progress_callback: 可选的进度回调函数

    Returns:
        tuple: (文件路径, 视频信息)

    Raises:
        Exception: 如果下载过程中发生错误
    """
    temp_dir = None

    try:
        # 验证输入参数
        if not url or not isinstance(url, str):
            raise ValueError("无效的YouTube链接")

        # 验证下载类型
        if download_type not in ['audio', 'video']:
            download_type = 'audio'  # 默认使用音频下载

        # 创建临时目录
        temp_dir = tempfile.mkdtemp()
        logger.info(f"创建临时目录: {temp_dir}")

        # 根据下载类型配置yt-dlp
        if download_type == 'audio':
            # 音频下载配置
            ydl_opts = {
                'format': CONFIG['download']['audio_format'],
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'postprocessors': [
                    {
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': CONFIG['download']['audio_codec'],
                        'preferredquality': CONFIG['download']['audio_quality'],
                    }
                ],
                'quiet': CONFIG['download']['quiet'],
                'no_warnings': CONFIG['download']['no_warnings'],
                'retries': CONFIG['download']['retries'],
                'fragment_retries': CONFIG['download']['fragment_retries'],
                'timeout': CONFIG['download']['timeout'],
                'socket_timeout': CONFIG['download']['socket_timeout'],
                'http_headers': {
                    'User-Agent': CONFIG['download']['user_agent']
                },
                'ignoreerrors': CONFIG['download']['ignore_errors'],
            }
        else:
            # 视频下载配置
            # 优先选择包含音频的mp4格式视频，避免使用ffmpeg转换，提高下载速度
            video_format = CONFIG['download']['video_format']
            if video_format == "bestvideo+bestaudio/best":
                # 修改为优先选择包含音频的mp4格式
                video_format = "best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best"
            
            # 根据是否使用已经包含音频的mp4格式决定是否需要后处理
            needs_postprocessing = "bestvideo" in video_format  # 如果包含bestvideo，可能需要合并
            
            ydl_opts = {
                'format': video_format,
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'quiet': CONFIG['download']['quiet'],
                'no_warnings': CONFIG['download']['no_warnings'],
                'retries': CONFIG['download']['retries'],
                'fragment_retries': CONFIG['download']['fragment_retries'],
                'timeout': CONFIG['download']['timeout'],
                'socket_timeout': CONFIG['download']['socket_timeout'],
                'http_headers': {
                    'User-Agent': CONFIG['download']['user_agent']
                },
                'ignoreerrors': CONFIG['download']['ignore_errors'],
            }
            
            # 只在需要时添加后处理配置
            if needs_postprocessing:
                ydl_opts['postprocessors'] = [
                    {
                        'key': 'FFmpegVideoConvertor',
                        'preferedformat': CONFIG['download']['video_output_format'],
                    }
                ]
                logger.info("使用视频+音频合并模式，可能需要较长处理时间")
            else:
                logger.info("使用包含音频的mp4格式，无需额外转换处理")

        # 如果提供了进度回调，设置进度钩子
        progress_queue = None
        if progress_callback:
            progress_queue = asyncio.Queue()
            ydl_opts['progress_hooks'] = [
                lambda d: progress_queue.put_nowait(d)
            ]

        # 定义一个处理进度队列的协程
        async def process_progress_queue():
            last_percent = -1  # 用于限制进度更新频率
            last_status = None  # 用于跟踪状态变化

            while True:
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

        # 启动进度处理任务
        progress_task = asyncio.create_task(process_progress_queue()) if progress_queue else None

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
                    timeout=CONFIG['download']['timeout']  # 从配置中获取超时设置
                )
            except asyncio.TimeoutError:
                raise Exception(f"下载超时，已超过配置的{CONFIG['download']['timeout']}秒限制")

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

        # 根据下载类型查找对应的文件
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

        # 返回文件路径和视频信息
        return target_file, info
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

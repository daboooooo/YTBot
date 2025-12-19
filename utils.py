import os
import time
import asyncio
import functools
from config import CONFIG
from logger import get_logger

logger = get_logger(__name__)


# 重试装饰器
def retry(max_retries=None, initial_delay=None):
    """
    通用重试装饰器，支持同步和异步函数

    Args:
        max_retries: 最大重试次数
        initial_delay: 初始延迟时间（秒）
    """
    if max_retries is None:
        max_retries = CONFIG['download']['max_retry_count']
    if initial_delay is None:
        initial_delay = CONFIG['download']['initial_retry_delay']

    def decorator(func):
        # 检查是否为异步函数
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                last_exception = None

                for attempt in range(max_retries):
                    try:
                        return await func(*args, **kwargs)
                    except Exception as e:
                        last_exception = e
                        logger.warning(
                            f"异步函数 {func.__name__} 执行失败 (尝试 {attempt + 1}/{max_retries}): {str(e)}"
                        )

                        # 不是最后一次尝试时才重试
                        if attempt < max_retries - 1:
                            # 指数退避
                            delay = initial_delay * (2 ** attempt)
                            logger.info(f"{delay:.2f}秒后重试...")
                            await asyncio.sleep(delay)
                        else:
                            logger.error(f"达到最大重试次数，函数 {func.__name__} 执行失败")

                # 如果所有尝试都失败，抛出最后一个异常
                raise last_exception

            return async_wrapper
        else:
            # 同步函数的装饰器
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                last_exception = None

                for attempt in range(max_retries):
                    try:
                        return func(*args, **kwargs)
                    except Exception as e:
                        last_exception = e
                        logger.warning(
                            f"同步函数 {func.__name__} 执行失败 (尝试 {attempt + 1}/{max_retries}): {str(e)}"
                        )

                        # 不是最后一次尝试时才重试
                        if attempt < max_retries - 1:
                            # 指数退避
                            delay = initial_delay * (2 ** attempt)
                            logger.info(f"{delay:.2f}秒后重试...")
                            time.sleep(delay)
                        else:
                            logger.error(f"达到最大重试次数，函数 {func.__name__} 执行失败")

                # 如果所有尝试都失败，抛出最后一个异常
                raise last_exception

            return sync_wrapper

    return decorator


# 规范化文件名
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


# 检查目录大小
def get_directory_size(path):
    """
    获取目录大小

    Args:
        path: 目录路径

    Returns:
        int: 目录大小（字节）
    """
    total_size = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                try:
                    total_size += os.path.getsize(filepath)
                except (OSError, IOError) as e:
                    logger.warning(f"获取文件大小失败: {filepath}, 错误: {str(e)}")
    except (OSError, IOError) as e:
        logger.error(f"获取目录大小失败: {path}, 错误: {str(e)}")
    return total_size


# 格式化文件大小
def format_file_size(size_bytes):
    """
    格式化文件大小为人类可读的格式

    Args:
        size_bytes: 文件大小（字节）

    Returns:
        str: 格式化后的大小
    """
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    size = float(size_bytes)
    unit_index = 0

    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    return f"{size:.2f} {units[unit_index]}"


def safe_truncate_filename(filename, max_bytes=64):
    """
    根据字节数安全截断文件名，考虑中文等多字节字符

    Args:
        filename: 原始文件名
        max_bytes: 最大允许字节数（默认64字节）

    Returns:
        str: 截断后的安全文件名
    """
    if not filename:
        return "unknown_file.mp3"

    # 先清理文件名
    filename = sanitize_filename(filename)

    # 分离文件名和扩展名
    name, ext = os.path.splitext(filename)

    # 计算扩展名的字节长度
    ext_bytes = len(ext.encode('utf-8'))

    # 计算文件名主体的最大允许字节数
    max_name_bytes = max_bytes - ext_bytes

    # 如果扩展名本身就超过最大字节数，使用默认文件名
    if max_name_bytes <= 0:
        return f"file{ext}"

    # 如果文件名主体不超过最大字节数，直接返回
    if len(name.encode('utf-8')) <= max_name_bytes:
        return filename

    # 否则，截断文件名主体
    # 初始尝试使用二分法找到合适的截断点
    left = 0
    right = len(name)
    best_len = 0

    while left <= right:
        mid = (left + right) // 2
        current_bytes = len(name[:mid].encode('utf-8'))

        if current_bytes <= max_name_bytes:
            best_len = mid
            left = mid + 1
        else:
            right = mid - 1

    # 确保截断后的文件名有意义
    if best_len == 0:
        return f"file{ext}"

    # 截断文件名并添加省略号（如果需要）
    truncated_name = name[:best_len]

    # 检查是否需要添加省略号
    ellipsis = "..."
    ellipsis_bytes = len(ellipsis.encode('utf-8'))

    # 如果添加省略号后仍在限制内，添加省略号
    if len(truncated_name.encode('utf-8')) + ellipsis_bytes <= max_name_bytes:
        truncated_name += ellipsis

    return f"{truncated_name}{ext}"


# 清理临时目录
def cleanup_temp_files(temp_dir):
    """
    清理临时目录

    Args:
        temp_dir: 临时目录路径
    """
    if temp_dir and os.path.exists(temp_dir):
        try:
            import shutil
            shutil.rmtree(temp_dir)
            logger.info(f"已清理临时目录: {temp_dir}")
            return True
        except Exception as e:
            logger.error(f"清理临时目录失败: {str(e)}")
            return False
    return True


# 规范化版本号
def normalize_version(version):
    """
    规范化版本号，去除前导零

    Args:
        version: 版本号字符串

    Returns:
        str: 规范化后的版本号
    """
    # 分割版本号并去除每个部分的前导零
    parts = version.split('.')
    normalized_parts = [str(int(part)) if part.isdigit() else part for part in parts]
    return '.'.join(normalized_parts)


# 检查网络连接
def check_network_connection(timeout=5):
    """
    检查网络连接

    Args:
        timeout: 超时时间（秒）

    Returns:
        bool: 是否连接成功
    """
    try:
        import socket
        # 使用多个公共DNS服务器测试连接
        test_hosts = ['8.8.8.8', '1.1.1.1', '208.67.222.222']

        for host in test_hosts:
            try:
                with socket.create_connection((host, 53), timeout=timeout):
                    logger.debug(f"网络连接测试成功: {host}")
                    return True
            except (socket.timeout, socket.error):
                continue

        logger.warning("所有测试主机连接失败")
        return False
    except Exception as e:
        logger.error(f"网络连接测试失败: {str(e)}")
        return False

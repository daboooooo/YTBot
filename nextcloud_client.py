import os
import time
from webdav3.client import Client as NextcloudClient
from config import CONFIG
from logger import get_logger


# 检查目录是否存在
def check_directory_exists(path):
    """
    检查指定路径是否存在且是目录

    Args:
        path: 要检查的路径

    Returns:
        bool: 路径是否存在且是目录
    """
    try:
        return os.path.exists(path) and os.path.isdir(path)
    except Exception as e:
        logger.error(f"检查目录存在性失败: {path}, 错误: {str(e)}")
        return False


logger = get_logger(__name__)

# 全局缓存
_nextcloud_client_cache = {
    'client': None,
    'timestamp': 0,
    'ttl': 3600  # 1小时缓存
}


def get_nextcloud_client():
    """
    获取Nextcloud客户端实例，带缓存机制

    Returns:
        nextcloud_client.Client: Nextcloud客户端实例
    """
    global _nextcloud_client_cache
    current_time = time.time()

    # 检查缓存是否有效
    if (_nextcloud_client_cache['client'] is not None and
            (current_time - _nextcloud_client_cache['timestamp']) < _nextcloud_client_cache['ttl']):
        logger.debug("使用缓存的Nextcloud客户端")
        return _nextcloud_client_cache['client']

    logger.info("创建新的Nextcloud客户端连接")

    # 解析Nextcloud URL
    nextcloud_url = CONFIG['nextcloud']['url']
    if not nextcloud_url.startswith('http'):
        nextcloud_url = f'http://{nextcloud_url}'

    # 确保URL格式正确
    if not nextcloud_url.endswith('/'):
        nextcloud_url = f'{nextcloud_url}/'

    try:
        # 创建客户端配置
        options = {
            'webdav_hostname': nextcloud_url + 'remote.php/webdav/',
            'webdav_login': CONFIG['nextcloud']['username'],
            'webdav_password': CONFIG['nextcloud']['password']
        }
        # 创建客户端
        client = NextcloudClient(options)

        # webdav3客户端的超时配置在options中设置，这里不需要额外设置

        # 更新缓存
        _nextcloud_client_cache['client'] = client
        _nextcloud_client_cache['timestamp'] = current_time

        logger.info("Nextcloud客户端创建成功")
        return client
    except Exception as e:
        logger.error(f"创建Nextcloud客户端失败: {str(e)}")
        # 清除缓存
        _nextcloud_client_cache['client'] = None
        raise


def check_client_validity(client):
    """
    检查Nextcloud客户端连接是否有效

    Args:
        client: Nextcloud客户端实例

    Returns:
        bool: 连接是否有效
    """
    try:
        # 使用list方法测试连接是否有效
        client.list()
        logger.debug("Nextcloud连接测试成功")
        return True
    except Exception as e:
        logger.error(f"Nextcloud连接测试失败: {str(e)}")
        return False


def check_nextcloud_connection():
    """
    检查Nextcloud连接

    Returns:
        bool: 连接是否成功
    """
    try:
        # 获取客户端
        client = get_nextcloud_client()

        # 检查连接有效性
        if not check_client_validity(client):
            logger.warning("Nextcloud连接无效，尝试重新连接")
            # 清除缓存并重新获取
            _nextcloud_client_cache['client'] = None
            client = get_nextcloud_client()
            if not check_client_validity(client):
                return False

        # 检查上传目录
        upload_dir = CONFIG['nextcloud']['upload_dir']
        # 移除前导斜杠，因为webdav3不接受带前导斜杠的路径
        if upload_dir.startswith('/'):
            upload_dir = upload_dir[1:]

        # 检查目录是否存在，使用list方法尝试列出目录内容
        logger.debug(f"检查Nextcloud上传目录: {upload_dir}")
        try:
            # 尝试列出目录内容，如果成功则目录存在
            client.list(upload_dir)
            logger.debug(f"Nextcloud目录已存在: {upload_dir}")
        except Exception:
            # 如果列出目录失败，尝试创建目录
            logger.info(f"创建Nextcloud上传目录: {upload_dir}")
            client.mkdir(upload_dir)
            logger.debug(f"Nextcloud目录创建成功: {upload_dir}")

        # 简化测试方法，只检查连接和目录操作，不进行文件上传测试
        # 因为目录操作成功就已经表明连接正常
        logger.info("Nextcloud连接测试成功")
        return True

    except Exception as e:
        logger.error(f"Nextcloud连接测试失败: {str(e)}")
        # 清理测试文件
        # if 'test_file_path' in locals() and os.path.exists(test_file_path):
        #     os.remove(test_file_path)
        return False


def upload_to_nextcloud(local_file_path, remote_file_path):
    """
    上传文件到Nextcloud

    Args:
        local_file_path: 本地文件路径
        remote_file_path: 远程文件路径

    Returns:
        str: 上传后的文件URL
    """
    max_retries = CONFIG['download']['max_retry_count']
    retry_delay = CONFIG['download']['initial_retry_delay']

    for attempt in range(max_retries):
        try:
            client = get_nextcloud_client()

            # 确保远程目录存在
            remote_dir = os.path.dirname(remote_file_path)
            if remote_dir and not remote_dir == '/':
                # 使用list方法检查目录是否存在
                try:
                    client.list(remote_dir)
                    directory_exists = True
                except Exception:
                    directory_exists = False

                if not directory_exists:
                    # 创建目录层次结构
                    path_parts = remote_dir.split('/')[1:]  # 去掉开头的'/'
                    current_path = ''
                    for part in path_parts:
                        current_path = f"{current_path}/{part}"
                        try:
                            client.list(current_path)
                            dir_exists = True
                        except Exception:
                            dir_exists = False
                        if not dir_exists:
                            client.mkdir(current_path)

            # 上传文件
            logger.info(f"上传文件到Nextcloud: {remote_file_path}")
            client.upload_sync(remote_path=remote_file_path, local_path=local_file_path)

            # 验证上传
            try:
                # 尝试列出远程文件所在目录，检查文件是否在列表中
                parent_dir = os.path.dirname(remote_file_path)
                files_list = client.list(parent_dir)
                file_name = os.path.basename(remote_file_path)
                if file_name not in files_list:
                    raise Exception("文件上传失败，远程文件不存在")
            except Exception as verify_error:
                logger.error(f"验证上传失败: {verify_error}")
                raise Exception("文件上传失败，远程文件不存在")

            # 获取本地文件大小作为参考
            local_size = os.path.getsize(local_file_path)
            logger.info(f"文件上传成功: {remote_file_path}, 本地大小: {local_size} 字节")

            # 构建文件URL
            base_url = CONFIG['nextcloud']['url'].rstrip('/')
            file_url = f"{base_url}/remote.php/dav/files/" + \
                f"{CONFIG['nextcloud']['username']}{remote_file_path}"
            return file_url

        except Exception as e:
            logger.error(f"上传文件失败 (尝试 {attempt + 1}/{max_retries}): {str(e)}")

            # 清除缓存
            _nextcloud_client_cache['client'] = None

            if attempt < max_retries - 1:
                # 指数退避
                delay = retry_delay * (2 ** attempt)
                logger.info(f"{delay:.2f}秒后重试...")
                time.sleep(delay)
            else:
                logger.error("达到最大重试次数，上传失败")
                raise

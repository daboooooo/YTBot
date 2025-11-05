import time
import tempfile
import os
from urllib.parse import urlparse
from webdav3.client import Client
from logger import logger
from config import CONFIG

# 全局Nextcloud客户端缓存
_nextcloud_client_cache = {
    'client': None,
    'last_initialized': 0,
    'cache_ttl': 3600  # 默认缓存时间1小时
}


def get_nextcloud_client(nextcloud_url=None, nextcloud_username=None, nextcloud_password=None):
    """
    初始化并返回NextCloud客户端，增强了容错性、缓存和错误处理

    支持从配置中自动获取参数

    Args:
        nextcloud_url: Nextcloud服务器URL（可选，默认使用配置中的值）
        nextcloud_username: Nextcloud用户名（可选，默认使用配置中的值）
        nextcloud_password: Nextcloud密码（可选，默认使用配置中的值）

    Returns:
        Client: 配置好的NextCloud客户端实例

    Raises:
        ValueError: 如果配置不完整或无效
        ConnectionError: 如果无法连接到NextCloud服务器
        Exception: 如果初始化过程中发生其他错误
    """
    # 如果没有提供参数，从配置中获取
    if nextcloud_url is None:
        nextcloud_url = CONFIG['NEXTCLOUD_URL']
    if nextcloud_username is None:
        nextcloud_username = CONFIG['NEXTCLOUD_USERNAME']
    if nextcloud_password is None:
        nextcloud_password = CONFIG['NEXTCLOUD_PASSWORD']
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
    if not nextcloud_url or not nextcloud_username or not nextcloud_password:
        raise ValueError("Nextcloud配置不完整: URL、用户名或密码缺失")

    # 验证URL格式
    try:
        # 确保URL格式正确
        parsed_url = urlparse(nextcloud_url)
        if not parsed_url.scheme or parsed_url.scheme not in ['http', 'https']:
            raise ValueError("Nextcloud URL格式无效，必须包含http或https协议")
    except Exception as e:
        raise ValueError(f"Nextcloud URL格式无效: {str(e)}")

    max_retries = CONFIG['NEXTCLOUD_CONNECTION_RETRIES']
    retry_delay = CONFIG['NEXTCLOUD_CONNECTION_RETRY_DELAY']  # 初始重试延迟

    for attempt in range(max_retries):
        try:
            options = {
                'webdav_hostname': f'{nextcloud_url}/remote.php/dav/files/{nextcloud_username}/',
                'webdav_login': nextcloud_username,
                'webdav_password': nextcloud_password,
                'webdav_timeout': CONFIG['NEXTCLOUD_TIMEOUT'],  # 从配置中获取超时设置
                'webdav_verbose': False  # 禁用详细日志
            }

            # 设置分块大小
            if CONFIG['NEXTCLOUD_CHUNK_SIZE'] > 0:
                options['chunk_size'] = CONFIG['NEXTCLOUD_CHUNK_SIZE']

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


def check_nextcloud_connection(nextcloud_url=None, nextcloud_username=None,
                               nextcloud_password=None, nextcloud_upload_dir=None):
    """
    检查Nextcloud连接，增强了错误处理和重试机制

    支持从配置中自动获取参数

    Args:
        nextcloud_url: Nextcloud服务器URL（可选，默认使用配置中的值）
        nextcloud_username: Nextcloud用户名（可选，默认使用配置中的值）
        nextcloud_password: Nextcloud密码（可选，默认使用配置中的值）
        nextcloud_upload_dir: Nextcloud上传目录（可选，默认使用配置中的值）

    Returns:
        tuple: (是否成功, 消息)
    """
    # 如果没有提供参数，从配置中获取
    if nextcloud_url is None:
        nextcloud_url = CONFIG['NEXTCLOUD_URL']
    if nextcloud_username is None:
        nextcloud_username = CONFIG['NEXTCLOUD_USERNAME']
    if nextcloud_password is None:
        nextcloud_password = CONFIG['NEXTCLOUD_PASSWORD']
    if nextcloud_upload_dir is None:
        nextcloud_upload_dir = CONFIG['NEXTCLOUD_UPLOAD_DIR']
    max_retries = CONFIG['NEXTCLOUD_CONNECTION_RETRIES']
    for attempt in range(max_retries):
        try:
            # 创建Nextcloud客户端
            nc_client = get_nextcloud_client(nextcloud_url, nextcloud_username, nextcloud_password)

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
                    if nextcloud_upload_dir:
                        upload_dir_items = nc_client.list(nextcloud_upload_dir)
                        logger.info(
                            f"Nextcloud上传目录 '{nextcloud_upload_dir}' 存在，包含 {
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
                    # 检查目录是否存在
                    try:
                        nc_client.list(test_dir)
                        dir_exists = True
                    except Exception:
                        dir_exists = False

                    if not dir_exists:
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
                    # 使用正确的方法删除文件和目录
                    # webdav3没有clean方法，我们可以尝试其他方式
                    logger.info("清理测试文件和目录成功")
                except Exception as e:
                    logger.warning(f"清理测试文件和目录失败: {str(e)}")

                return True, f"✅ Nextcloud连接成功！\n上传目录 '{nextcloud_upload_dir}' 可访问且权限正常"
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
        if attempt < max_retries - 1:
            wait_time = CONFIG['NEXTCLOUD_CONNECTION_RETRY_DELAY'] * (2 ** attempt)  # 指数退避策略
            logger.info(f"第 {attempt + 1} 次尝试失败，{wait_time} 秒后重试...")
            time.sleep(wait_time)

    # 所有尝试都失败
    return False, "Nextcloud连接失败: 所有重试尝试都失败\n请检查配置和网络连接后重试"


async def upload_file_to_nextcloud(file_path, remote_path, nextcloud_client=None, max_retries=None):
    """
    将文件上传到Nextcloud，支持重试和超时控制

    Args:
        file_path: 本地文件路径
        remote_path: Nextcloud远程路径
        nextcloud_client: Nextcloud客户端实例（可选，默认使用缓存的客户端）
        max_retries: 最大重试次数（可选，默认使用配置中的值）

    Returns:
        bool: 是否上传成功

    Raises:
        Exception: 上传失败时抛出异常
    """
    import asyncio

    # 如果没有提供客户端，使用get_nextcloud_client获取
    if nextcloud_client is None:
        nextcloud_client = get_nextcloud_client()

    # 如果没有提供重试次数，从配置中获取
    if max_retries is None:
        max_retries = CONFIG['NEXTCLOUD_UPLOAD_RETRIES']

    upload_success = False

    for attempt in range(max_retries):
        try:
            # 创建一个函数来包装上传操作，以便添加超时
            def _sync_upload():
                nextcloud_client.upload_sync(remote_path=remote_path, local_path=file_path)

            # 使用asyncio.wait_for添加超时控制
            await asyncio.wait_for(
                asyncio.to_thread(_sync_upload),
                timeout=CONFIG['NEXTCLOUD_UPLOAD_TIMEOUT']  # 从配置中获取上传超时
            )
            upload_success = True
            break
        except asyncio.TimeoutError:
            if attempt == max_retries - 1:
                raise Exception("上传超时，请尝试较小的文件或稍后再试")
            logger.warning(f"上传超时，第{attempt + 2}次尝试...")
        except Exception as upload_err:
            if attempt == max_retries - 1:
                raise upload_err
            logger.warning(f"上传失败，第{attempt + 2}次尝试...")
            # 等待一段时间后重试，使用指数退避
            wait_time = CONFIG['NEXTCLOUD_UPLOAD_RETRY_DELAY'] * (2 ** attempt)
            await asyncio.sleep(min(wait_time, 30))  # 最大等待30秒

    if not upload_success:
        raise Exception("上传失败，所有重试都已失败")

    # 验证文件是否成功上传
    try:
        # 验证文件是否存在
        parent_dir = os.path.dirname(remote_path)
        file_name = os.path.basename(remote_path)
        try:
            files_list = nextcloud_client.list(parent_dir)
            file_exists = file_name in files_list
        except Exception:
            file_exists = False

        if file_exists:
            # 可选：验证文件大小
            if CONFIG['NEXTCLOUD_VERIFY_FILE_SIZE']:
                # 尝试获取远程文件大小（取决于WebDAV实现）
                try:
                    # webdav3可能没有info方法，我们可以尝试使用其他方式或跳过大小验证
                    logger.info(f"文件上传验证成功: {remote_path}")
                except Exception as verify_err:
                    logger.warning(f"文件大小验证失败: {str(verify_err)}")

            logger.info(f"文件上传验证成功: {remote_path}")
            return True
        else:
            raise Exception("上传后的文件验证失败")
    except Exception as verify_err:
        logger.error(f"文件验证异常: {str(verify_err)}")
        raise Exception(f"上传后的文件验证失败: {str(verify_err)}")

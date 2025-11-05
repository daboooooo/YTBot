import os
import sys
import logging
import argparse
from webdav3.client import Client
from config import (
    NEXTCLOUD_URL,
    NEXTCLOUD_USERNAME,
    NEXTCLOUD_PASSWORD,
    NEXTCLOUD_UPLOAD_DIR
)

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def init_nextcloud_client():
    """
    初始化Nextcloud WebDAV客户端连接

    Returns:
        Client: 初始化后的Nextcloud客户端实例
    """
    try:
        # 配置Nextcloud WebDAV客户端
        options = {
            'webdav_hostname': f'{NEXTCLOUD_URL}/remote.php/dav/files/{NEXTCLOUD_USERNAME}/',
            'webdav_login': NEXTCLOUD_USERNAME,
            'webdav_password': NEXTCLOUD_PASSWORD,
            'webdav_timeout': 30
        }

        client = Client(options)
        # 验证连接是否成功
        client.list()
        logger.info(f"成功连接到Nextcloud服务器: {NEXTCLOUD_URL}")
        return client
    except Exception as e:
        logger.error(f"连接Nextcloud服务器失败: {str(e)}")
        raise


def ensure_directory_exists(client, remote_dir):
    """
    确保Nextcloud上的目录存在，如果不存在则创建

    Args:
        client (Client): Nextcloud客户端实例
        remote_dir (str): 远程目录路径
    """
    try:
        # 检查目录是否存在（使用list方法替代不存在的check方法）
        try:
            client.list(remote_dir)
            directory_exists = True
        except Exception:
            directory_exists = False

        if not directory_exists:
            # 创建目录，包括可能的父目录
            client.mkdir(remote_dir)
            logger.info(f"在Nextcloud上创建目录: {remote_dir}")
    except Exception as e:
        logger.error(f"确保目录存在失败: {str(e)}")
        raise


def upload_file_to_nextcloud(client, local_file_path, remote_file_path=None):
    """
    上传单个文件到Nextcloud

    Args:
        client (Client): Nextcloud客户端实例
        local_file_path (str): 本地文件路径
        remote_file_path (str, optional): 远程文件路径，如果未提供则使用默认上传目录

    Returns:
        str: 上传后的远程文件路径
    """
    try:
        # 验证本地文件存在
        if not os.path.isfile(local_file_path):
            raise FileNotFoundError(f"本地文件不存在: {local_file_path}")

        # 确定远程文件路径
        if remote_file_path is None:
            # 使用默认上传目录
            filename = os.path.basename(local_file_path)
            remote_file_path = f"{NEXTCLOUD_UPLOAD_DIR}/{filename}"

        # 确保目标目录存在
        remote_dir = os.path.dirname(remote_file_path)
        ensure_directory_exists(client, remote_dir)

        # 上传文件
        logger.info(f"开始上传文件: {local_file_path} -> {remote_file_path}")
        client.upload_sync(remote_path=remote_file_path, local_path=local_file_path)
        logger.info(f"文件上传成功: {local_file_path} -> {remote_file_path}")

        return remote_file_path
    except Exception as e:
        logger.error(f"文件上传失败: {str(e)}")
        raise


def upload_directory_to_nextcloud(client, local_dir_path, remote_dir_path=None):
    """
    上传整个文件夹到Nextcloud

    Args:
        client (Client): Nextcloud客户端实例
        local_dir_path (str): 本地文件夹路径
        remote_dir_path (str, optional): 远程文件夹路径，如果未提供则使用默认上传目录

    Returns:
        str: 上传后的远程文件夹路径
    """
    try:
        # 验证本地文件夹存在
        if not os.path.isdir(local_dir_path):
            raise NotADirectoryError(f"本地文件夹不存在: {local_dir_path}")

        # 确定远程文件夹路径
        if remote_dir_path is None:
            # 使用默认上传目录
            dirname = os.path.basename(local_dir_path)
            remote_dir_path = f"{NEXTCLOUD_UPLOAD_DIR}/{dirname}"

        # 确保目标目录存在
        ensure_directory_exists(client, remote_dir_path)

        logger.info(f"开始上传文件夹: {local_dir_path} -> {remote_dir_path}")

        # 遍历本地文件夹并上传所有文件
        for root, dirs, files in os.walk(local_dir_path):
            # 计算相对路径，用于保持目录结构
            relative_path = os.path.relpath(root, local_dir_path)

            # 为当前目录创建对应的远程目录
            if relative_path != '.':
                current_remote_dir = f"{remote_dir_path}/{relative_path}"
                ensure_directory_exists(client, current_remote_dir)
            else:
                current_remote_dir = remote_dir_path

            # 上传当前目录下的所有文件
            for file in files:
                local_file = os.path.join(root, file)
                remote_file = f"{current_remote_dir}/{file}"
                upload_file_to_nextcloud(client, local_file, remote_file)

        logger.info(f"文件夹上传成功: {local_dir_path} -> {remote_dir_path}")

        return remote_dir_path
    except Exception as e:
        logger.error(f"文件夹上传失败: {str(e)}")
        raise


def upload_to_nextcloud(path, remote_path=None):
    """
    统一的上传接口，可以上传文件或文件夹到Nextcloud

    Args:
        path (str): 本地文件或文件夹路径
        remote_path (str, optional): 远程文件或文件夹路径

    Returns:
        str: 上传后的远程路径
    """
    client = init_nextcloud_client()

    if os.path.isfile(path):
        return upload_file_to_nextcloud(client, path, remote_path)
    elif os.path.isdir(path):
        return upload_directory_to_nextcloud(client, path, remote_path)
    else:
        raise FileNotFoundError(f"路径不存在: {path}")


if __name__ == "__main__":
    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(
        description='上传文件或文件夹到Nextcloud',
        epilog="""使用示例:
  python3 send_file_nextcloud.py /path/to/local/file
  python3 send_file_nextcloud.py /path/to/local/folder
  python3 send_file_nextcloud.py /path/to/local/file -r /custom/remote/path
  python3 send_file_nextcloud.py /path/to/local/folder -r /custom/remote/folder"""
    )
    parser.add_argument('path', help='要上传的本地文件或文件夹路径')
    parser.add_argument('-r', '--remote', help='远程目标路径（可选）', default=None)

    # 解析命令行参数
    args = parser.parse_args()

    try:
        # 使用命令行参数中的路径
        local_path = args.path
        remote_path = args.remote

        # 上传文件或文件夹
        uploaded_path = upload_to_nextcloud(local_path, remote_path)
        logger.info(f"上传完成，远程路径: {uploaded_path}")
    except Exception as e:
        logger.error(f"上传过程中发生错误: {str(e)}")
        # 以非零状态码退出，指示出错
        sys.exit(1)

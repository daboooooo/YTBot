# 配置文件示例 - 请复制并重命名为config.py，然后填入您的实际配置
import os
from typing import List

# 加载.env文件中的环境变量
from dotenv import load_dotenv
load_dotenv()  # 加载.env文件中的环境变量


# 从环境变量加载配置，如果环境变量不存在则使用默认值
def get_env_or_default(env_name: str, default: str) -> str:
    """
    获取环境变量值或默认值
    """
    value = os.environ.get(env_name)
    return value if value is not None else default


# 从环境变量加载布尔值
def get_env_bool_or_default(env_name: str, default: bool) -> bool:
    """
    获取环境变量的布尔值或默认值
    """
    value = os.environ.get(env_name)
    if value is None:
        return default
    value = value.lower()
    if value in ['true', 'yes', '1', 't', 'y']:
        return True
    elif value in ['false', 'no', '0', 'f', 'n']:
        return False
    return default


# 从环境变量加载整数
def get_env_int_or_default(env_name: str, default: int) -> int:
    """
    获取环境变量的整数值或默认值
    """
    value = os.environ.get(env_name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


# 从环境变量加载浮点数
def get_env_float_or_default(env_name: str, default: float) -> float:
    """
    获取环境变量的浮点数值或默认值
    """
    value = os.environ.get(env_name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


# Telegram Bot配置
TELEGRAM_BOT_TOKEN = get_env_or_default("TELEGRAM_BOT_TOKEN",
                                        '')  # 请填入您的Telegram Bot Token
ADMIN_CHAT_ID = get_env_or_default("ADMIN_CHAT_ID", '')  # 请填入您的Telegram用户ID
# 允许使用机器人的用户ID列表 - 包含管理员和其他可能的用户
ALLOWED_CHAT_IDS = [
    ADMIN_CHAT_ID,  # 管理员ID作为字符串
    # 可以在这里添加更多用户ID，作为字符串
]

# Nextcloud配置
NEXTCLOUD_URL = get_env_or_default("NEXTCLOUD_URL", '')  # 请填入您的Nextcloud服务器URL
NEXTCLOUD_USERNAME = get_env_or_default("NEXTCLOUD_USERNAME", '')  # 请填入您的Nextcloud用户名
NEXTCLOUD_PASSWORD = get_env_or_default("NEXTCLOUD_PASSWORD", '')  # 请填入您的Nextcloud密码
NEXTCLOUD_UPLOAD_DIR = get_env_or_default("NEXTCLOUD_UPLOAD_DIR", '/YTBot')
NEXTCLOUD_CHUNK_SIZE = get_env_int_or_default("NEXTCLOUD_CHUNK_SIZE", 1024 * 1024 * 8)  # 8MB 块大小
NEXTCLOUD_TIMEOUT = get_env_int_or_default("NEXTCLOUD_TIMEOUT", 600)  # 10分钟超时

# 并发控制配置
MAX_CONCURRENT_DOWNLOADS = get_env_int_or_default("MAX_CONCURRENT_DOWNLOADS", 5)

# 日志级别配置
LOG_LEVEL = get_env_or_default("LOG_LEVEL", 'INFO')  # 可选: DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_FILE = get_env_or_default("LOG_FILE", "ytbot.log")
LOG_MAX_BYTES = get_env_int_or_default("LOG_MAX_BYTES", 10485760)  # 10MB
LOG_BACKUP_COUNT = get_env_int_or_default("LOG_BACKUP_COUNT", 5)

# 下载配置
DOWNLOAD_TIMEOUT = get_env_int_or_default("DOWNLOAD_TIMEOUT", 3600)  # 1小时超时
MAX_RETRY_COUNT = get_env_int_or_default("MAX_RETRY_COUNT", 3)
INITIAL_RETRY_DELAY = get_env_float_or_default("INITIAL_RETRY_DELAY", 1.0)  # 秒

# 应用配置
APP_NAME = "YouTube Download Bot"
APP_VERSION = "1.0.0"
MAX_MESSAGE_LENGTH = 4096  # Telegram消息最大长度

# 进度更新配置
PROGRESS_UPDATE_INTERVAL = get_env_int_or_default("PROGRESS_UPDATE_INTERVAL", 10)  # 百分比更新间隔

# 系统资源监控配置
MONITOR_INTERVAL = get_env_int_or_default("MONITOR_INTERVAL", 3600)  # 1小时
MIN_DISK_SPACE = get_env_float_or_default("MIN_DISK_SPACE", 1024.0)  # MB
MAX_CPU_LOAD = get_env_float_or_default("MAX_CPU_LOAD", 0.8)  # 80%
MEMORY_THRESHOLD = get_env_int_or_default("MEMORY_THRESHOLD", 512)  # MB
USER_STATE_TIMEOUT = get_env_int_or_default("USER_STATE_TIMEOUT", 300)  # 秒

# 安全配置
CHECK_CERTIFICATE = get_env_bool_or_default("CHECK_CERTIFICATE", False)  # 本地Nextcloud可以设为False

# 构建配置字典，便于导入和使用
CONFIG = {
    "telegram": {
        "token": TELEGRAM_BOT_TOKEN,
        "allowed_chat_ids": ALLOWED_CHAT_IDS,
        "admin_chat_id": ADMIN_CHAT_ID
    },
    "nextcloud": {
        "url": NEXTCLOUD_URL,
        "username": NEXTCLOUD_USERNAME,
        "password": NEXTCLOUD_PASSWORD,
        "upload_dir": NEXTCLOUD_UPLOAD_DIR,
        "chunk_size": NEXTCLOUD_CHUNK_SIZE,
        "timeout": NEXTCLOUD_TIMEOUT,
        "connection_retries": get_env_int_or_default("NEXTCLOUD_CONNECTION_RETRIES", 3),
        "connection_retry_delay": get_env_float_or_default("NEXTCLOUD_CONNECTION_RETRY_DELAY", 5.0),
        "upload_retries": get_env_int_or_default("NEXTCLOUD_UPLOAD_RETRIES", 3),
        "upload_retry_delay": get_env_float_or_default("NEXTCLOUD_UPLOAD_RETRY_DELAY", 5.0),
        "upload_timeout": get_env_int_or_default("NEXTCLOUD_UPLOAD_TIMEOUT", 600),
        "verify_file_size": get_env_bool_or_default("NEXTCLOUD_VERIFY_FILE_SIZE", True)
    },
    "download": {
        "timeout": DOWNLOAD_TIMEOUT,
        "max_retry_count": MAX_RETRY_COUNT,
        "initial_retry_delay": INITIAL_RETRY_DELAY,
        "check_yt_dlp_version": get_env_bool_or_default("CHECK_YT_DLP_VERSION", True),
        "version_check_timeout": get_env_int_or_default("VERSION_CHECK_TIMEOUT", 10),
        "quiet": get_env_bool_or_default("DOWNLOAD_QUIET", False),
        "no_warnings": get_env_bool_or_default("DOWNLOAD_NO_WARNINGS", False),
        "retries": get_env_int_or_default("DOWNLOAD_RETRIES", 3),
        "fragment_retries": get_env_int_or_default("DOWNLOAD_FRAGMENT_RETRIES", 10),
        "ignore_errors": get_env_bool_or_default("DOWNLOAD_IGNORE_ERRORS", True),
        "user_agent": get_env_or_default(
            "DOWNLOAD_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        ),
        "http_headers": {
            "User-Agent": get_env_or_default(
                "DOWNLOAD_USER_AGENT",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            )
        },
        "audio_format": get_env_or_default("AUDIO_FORMAT", "bestaudio/best"),
        "audio_codec": get_env_or_default("AUDIO_CODEC", "mp3"),
        "audio_quality": get_env_int_or_default("AUDIO_QUALITY", 192),
        "video_format": get_env_or_default(
            "VIDEO_FORMAT",
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        ),
        "video_output_format": get_env_or_default("VIDEO_OUTPUT_FORMAT", "mp4"),
        "prefer_ffmpeg": True,
        "ignore_no_formats_error": True,
        "allow_playlist_files": True,
        "merge_output_format": "mp4",
        "sleep_interval_requests": 2,
        "sleep_interval": 5,
        "max_sleep_interval": 30,
        "socket_timeout": get_env_int_or_default("DOWNLOAD_SOCKET_TIMEOUT", 30),
        "progress_update_interval": PROGRESS_UPDATE_INTERVAL
    },
    "log": {
        "level": LOG_LEVEL,
        "format": LOG_FORMAT,
        "file": LOG_FILE,
        "max_bytes": LOG_MAX_BYTES,
        "backup_count": LOG_BACKUP_COUNT
    },
    "app": {
        "name": APP_NAME,
        "version": APP_VERSION,
        "max_message_length": MAX_MESSAGE_LENGTH,
        "max_concurrent_downloads": MAX_CONCURRENT_DOWNLOADS
    },
    "monitor": {
        "interval": MONITOR_INTERVAL,
        "min_disk_space": MIN_DISK_SPACE,
        "max_cpu_load": MAX_CPU_LOAD,
        "memory_threshold": MEMORY_THRESHOLD,
        "user_state_timeout": USER_STATE_TIMEOUT
    },
    "security": {
        "check_certificate": CHECK_CERTIFICATE
    }
}


# 验证必需的配置项
def validate_config() -> List[str]:
    """
    验证配置是否有效，返回错误消息列表
    """
    errors = []

    # 验证Telegram配置
    if not TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN 未正确配置")

    # 验证Nextcloud配置
    if not NEXTCLOUD_URL:
        errors.append("NEXTCLOUD_URL 未正确配置")
    if not NEXTCLOUD_USERNAME:
        errors.append("NEXTCLOUD_USERNAME 未正确配置")
    if not NEXTCLOUD_PASSWORD:
        errors.append("NEXTCLOUD_PASSWORD 未正确配置")

    # 验证允许的用户列表
    if not ALLOWED_CHAT_IDS:
        errors.append("ALLOWED_CHAT_IDS 未配置，机器人将无法使用")

    return errors


# 打印配置摘要（不包含敏感信息）
def print_config_summary() -> None:
    """
    打印配置摘要（不包含敏感信息）
    """
    print(f"{APP_NAME} v{APP_VERSION} 配置摘要:")
    # 检查Telegram Bot是否已配置
    bot_status = '已配置' if TELEGRAM_BOT_TOKEN else '未配置'
    print(f"- Telegram Bot: {bot_status}")
    print(f"- 允许的用户数: {len(ALLOWED_CHAT_IDS)}")
    print(f"- Nextcloud URL: {NEXTCLOUD_URL}")
    print(f"- Nextcloud 用户: {NEXTCLOUD_USERNAME}")
    print(f"- Nextcloud 上传目录: {NEXTCLOUD_UPLOAD_DIR}")
    print(f"- 日志级别: {LOG_LEVEL}")
    print(f"- 最大并发下载: {MAX_CONCURRENT_DOWNLOADS}")
    print(f"- 进度更新间隔: {PROGRESS_UPDATE_INTERVAL}%")
    print(f"- 内存阈值: {MEMORY_THRESHOLD} MB")

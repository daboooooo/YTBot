# YTBot 代码优化迁移指南

本文档指导开发者如何将现有代码迁移到新的优化系统。

## 概述

本次优化引入了以下改进：

1. **新的配置系统** - 类型安全的配置管理
2. **统一的异常处理** - 结构化的异常层次
3. **完善的类型注解** - 更好的 IDE 支持和类型检查
4. **异步工具函数** - 性能优化
5. **公共工具模块** - 代码复用

## 配置系统迁移

### 旧方式（CONFIG 字典）

```python
from ytbot.core.config import CONFIG

# 访问配置
token = CONFIG['telegram']['token']
timeout = CONFIG['download']['timeout']
```

### 新方式（类型安全配置）

```python
from ytbot.core.config import get_config

# 获取配置实例
config = get_config()

# 访问配置（有类型提示和自动补全）
token = config.telegram.token
timeout = config.download.timeout
```

### 配置类结构

```python
config.telegram.token          # Telegram Bot Token
config.telegram.admin_chat_id  # 管理员 Chat ID

config.nextcloud.url           # Nextcloud URL
config.nextcloud.username      # Nextcloud 用户名
config.nextcloud.password      # Nextcloud 密码

config.download.timeout        # 下载超时时间
config.download.max_retry_count # 最大重试次数

config.log.level               # 日志级别
config.log.format              # 日志格式
```

## 异常处理迁移

### 旧方式（通用异常）

```python
try:
    result = await download_video(url)
except Exception as e:
    logger.error(f"Download failed: {e}")
    return {"success": False, "error": str(e)}
```

### 新方式（特定异常）

```python
from ytbot.core.exceptions import (
    YouTubeError,
    DownloadError,
    get_user_friendly_message
)

try:
    result = await download_video(url)
except YouTubeError as e:
    # 处理 YouTube 特定错误
    logger.error(f"YouTube error: {e.message}", extra={"video_id": e.video_id})
    return {"success": False, "error": get_user_friendly_message(e)}
except DownloadError as e:
    # 处理下载错误
    logger.error(f"Download error: {e.message}", extra={"download_id": e.download_id})
    return {"success": False, "error": get_user_friendly_message(e)}
```

### 可用的异常类型

```python
# 配置异常
ConfigError                    # 基础配置错误
ConfigValidationError          # 配置验证错误
ConfigTypeError               # 配置类型错误

# 平台异常
PlatformError                  # 基础平台错误
YouTubeError                   # YouTube 错误
TwitterError                   # Twitter/X 错误
UnsupportedURLError           # 不支持的 URL
ContentNotFoundError          # 内容未找到

# 下载异常
DownloadError                  # 基础下载错误
DownloadCancelledError        # 下载取消
DownloadTimeoutError          # 下载超时
FormatSelectionError          # 格式选择错误
FFmpegError                   # FFmpeg 错误

# 存储异常
StorageError                   # 基础存储错误
NextcloudError                # Nextcloud 错误
LocalStorageError             # 本地存储错误
StorageQuotaError             # 存储配额错误
```

## 类型注解使用

### 导入类型定义

```python
from ytbot.core.types import (
    ContentType,      # 内容类型枚举
    ContentInfo,      # 内容信息数据类
    DownloadResult,   # 下载结果数据类
    StorageResult,    # 存储结果数据类
    JSONDict,         # JSON 字典类型别名
)
```

### 使用示例

```python
from typing import Optional, List
from ytbot.core.types import ContentInfo, DownloadResult

async def process_content(url: str) -> Optional[ContentInfo]:
    """处理内容并返回信息"""
    pass

async def download_batch(urls: List[str]) -> List[DownloadResult]:
    """批量下载内容"""
    pass
```

## 异步工具函数

### 运行同步代码

```python
from ytbot.utils.async_utils import run_in_thread

# 在线程池中运行同步函数
result = await run_in_thread(sync_function, arg1, arg2)
```

### 超时控制

```python
from ytbot.utils.async_utils import run_with_timeout

# 运行协程并设置超时
try:
    result = await run_with_timeout(
        long_running_task(),
        timeout=30.0,
        timeout_message="Operation timed out"
    )
except asyncio.TimeoutError:
    logger.error("Task timed out")
```

### 重试机制

```python
from ytbot.utils.async_utils import retry_with_backoff

# 带指数退避的重试
result = await retry_with_backoff(
    unstable_function,
    max_retries=3,
    initial_delay=1.0,
    backoff_factor=2.0
)
```

## 公共工具函数

### 文件操作

```python
from ytbot.utils.common import (
    sanitize_filename,      # 清理文件名
    format_file_size,       # 格式化文件大小
    format_duration,        # 格式化时长
    ensure_directory,       # 确保目录存在
    safe_delete,           # 安全删除文件
)

# 使用示例
filename = sanitize_filename("video<title>.mp4")  # "videotitle.mp4"
size_str = format_file_size(1572864)              # "1.5 MB"
duration = format_duration(3665)                  # "1:01:05"
```

### 文本处理

```python
from ytbot.utils.common import (
    truncate_text,         # 截断文本
    escape_markdown,       # 转义 Markdown
    escape_html_text,      # 转义 HTML
)

text = truncate_text("very long text...", max_length=20)  # "very long te..."
escaped = escape_markdown("*bold*")  # "\*bold\*"
```

### URL 处理

```python
from ytbot.utils.common import (
    parse_url,            # 解析 URL
    is_valid_url,         # 验证 URL
)

parsed = parse_url("https://example.com/path?key=value")
# {'scheme': 'https', 'hostname': 'example.com', ...}

is_valid = is_valid_url("https://example.com")  # True
```

## 完整迁移示例

### 迁移前

```python
from ytbot.core.config import CONFIG
from ytbot.core.logger import get_logger

logger = get_logger(__name__)

async def download_video(url: str, content_type: str):
    try:
        timeout = CONFIG['download']['timeout']
        retries = CONFIG['download']['retries']
        
        # 下载逻辑
        result = await perform_download(url, timeout, retries)
        
        return {"success": True, "file": result}
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return {"success": False, "error": str(e)}
```

### 迁移后

```python
from ytbot.core.config import get_config
from ytbot.core.logger import get_logger
from ytbot.core.exceptions import DownloadError, get_user_friendly_message
from ytbot.core.types import ContentType, DownloadResult
from ytbot.utils.common import sanitize_filename
from typing import Optional

logger = get_logger(__name__)

async def download_video(
    url: str,
    content_type: ContentType
) -> DownloadResult:
    config = get_config()
    
    try:
        timeout = config.download.timeout
        retries = config.download.retries
        
        # 下载逻辑
        file_path = await perform_download(url, timeout, retries)
        
        return DownloadResult(
            success=True,
            file_path=file_path,
            content_info=ContentInfo(
                url=url,
                title=sanitize_filename("video_title"),
                content_type=content_type
            )
        )
    except DownloadError as e:
        logger.error(
            f"Download failed: {e.message}",
            extra={"url": url, "error_code": e.error_code}
        )
        return DownloadResult(
            success=False,
            error_message=get_user_friendly_message(e)
        )
```

## 测试编写指南

### 使用 pytest 编写测试

```python
import pytest
from unittest.mock import Mock, patch
from ytbot.platforms.youtube import YouTubeHandler

class TestYouTubeHandler:
    @pytest.fixture
    def handler(self):
        return YouTubeHandler()
    
    def test_can_handle_youtube_url(self, handler):
        assert handler.can_handle("https://youtube.com/watch?v=test")
    
    @pytest.mark.asyncio
    async def test_get_content_info(self, handler):
        with patch('yt_dlp.YoutubeDL') as mock_ydl:
            # 测试逻辑
            pass
```

### 运行测试

```bash
# 运行所有测试
python -m pytest tests/unit/ -v

# 运行特定测试文件
python -m pytest tests/unit/test_youtube.py -v

# 生成覆盖率报告
python -m pytest tests/unit/ --cov=ytbot --cov-report=html
```

## 最佳实践

1. **始终使用类型注解** - 提高代码可读性和 IDE 支持
2. **使用特定异常** - 便于错误处理和调试
3. **利用配置系统** - 避免硬编码配置值
4. **编写单元测试** - 确保代码质量
5. **使用工具函数** - 避免重复代码

## 常见问题

### Q: 新的配置系统是否向后兼容？

A: 是的，旧的 `CONFIG` 字典仍然可用，但建议使用新的类型安全配置。

### Q: 如何处理运行时配置修改？

A: 当前配置是冻结的（frozen），运行时修改需要重新加载配置。

### Q: 是否需要更新所有现有代码？

A: 不需要一次性更新所有代码。可以逐步迁移，新旧系统可以共存。

## 获取帮助

如有问题，请：

1. 查看本文档的相关章节
2. 参考 `tests/unit/` 中的测试示例
3. 查看已迁移的代码（如 `ytbot/cli.py`, `ytbot/platforms/youtube.py`）

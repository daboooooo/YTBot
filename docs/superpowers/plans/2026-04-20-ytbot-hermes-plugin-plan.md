# YTBot → Hermes-Agent Plugin 集成实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 ytbot 的 YouTube 下载/查询/转录能力集成到 hermes-agent 中作为目录插件，同时保留 ytbot 独立 CLI 运行能力。

**Architecture:** 双模式架构 — `cli.py`（独立CLI）和 `~/.hermes/plugins/ytbot/__init__.py`（Hermes 插件）作为两个平等入口，共享同一套核心业务逻辑（`platforms/`, `services/`, `storage/`, `core/`）。移除 Rich UI 层（`ui/terminal.py`, `ui/formatter.py`, `ui/widgets.py`），将日志系统从自定义 `YTBotLogger`+`ColoredConsoleHandler` 迁移为标准 `logging` 模块。

**Tech Stack:** Python 3.11+, yt-dlp, hermes-agent Plugin API (PluginContext), standard logging

---

## Task 1: 日志系统改造 — 移除 Rich/ColoredConsoleHandler 依赖

**Files:**
- Modify: `ytbot/core/enhanced_logger.py`
- Modify: `ytbot/platforms/youtube.py`

- [ ] **Step 1: 改造 enhanced_logger.py — 移除 ColoredConsoleHandler**

`ColoredConsoleHandler` 使用 ANSI 转义码着色，虽然不直接依赖 rich 库，但与终端 UI 的设计哲学一致。替换为标准 `logging.StreamHandler`：

```python
# ytbot/core/enhanced_logger.py
# 删除整个 ColoredConsoleHandler 类 (原第269-289行)
# 替换 _setup_handlers 方法中的 console_handler 创建逻辑

def _setup_handlers(self):
    detailed_format = (
        "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s"
    )

    console_handler = logging.StreamHandler(sys.stdout)  # 标准 StreamHandler
    console_handler.setLevel(getattr(logging, self.config.log.level))
    console_formatter = logging.Formatter(detailed_format)
    console_handler.setFormatter(console_formatter)
    self.logger.addHandler(console_handler)

    # File handler 保持不变 (RotatingFileHandler 无需改动)
    try:
        log_dir = os.path.dirname(self.config.log.file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
        file_handler = logging.handlers.RotatingFileHandler(
            filename=self.config.log.file,
            maxBytes=self.config.log.max_bytes,
            backupCount=self.config.log.backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(getattr(logging, self.config.log.level))
        file_formatter = logging.Formatter(detailed_format)
        file_handler.setFormatter(file_formatter)
        self.logger.addHandler(file_handler)
    except Exception as e:
        self.logger.warning(f"Failed to set up file logging: {e}")
```

- [ ] **Step 2: 运行测试验证日志输出正常**

Run: `cd /Users/horsenli/Works/ytbot && python -c "from ytbot.core.enhanced_logger import get_logger; l = get_logger('test'); l.info('hello')"`
Expected: 输出带时间戳和级别的日志行，无报错

- [ ] **Step 3: 清理 youtube.py 中的 emoji 日志前缀（可选）**

`youtube.py` 中的 `logger.info("🎬 yt-dlp download options:")` 等 emoji 前缀可以保留（它们是字符串内容而非 Rich 渲染），但如果希望更简洁的日志可统一移除。此步可选。

- [ ] **Step 4: 全局搜索确认无 Rich 残留**

Run: `cd /Users/horsenli/Works/ytbot && grep -r "from rich\|import rich\|from .terminal import\|from .formatter import\|from .widgets import" ytbot/ --include="*.py" | grep -v __pycache__`
Expected: 仅 `ui/` 目录下的文件有这些导入（即将被删除），其他文件干净

- [ ] **Step 5: Commit**

```bash
git add ytbot/core/enhanced_logger.py
git commit -m "refactor: replace ColoredConsoleHandler with standard logging.StreamHandler"
```

---

## Task 2: 删除 Rich UI 层

**Files:**
- Delete: `ytbot/ui/terminal.py`
- Delete: `ytbot/ui/formatter.py`
- Delete: `ytbot/ui/widgets.py`
- Modify: `ytbot/cli.py`
- Modify: `ytbot/ui/__init__.py` (如存在)

- [ ] **Step 1: 删除三个 UI 文件**

```bash
rm ytbot/ui/terminal.py ytbot/ui/formatter.py ytbot/ui/widgets.py
```

- [ ] **Step 2: 改造 cli.py — 移除 TerminalUI 依赖**

`cli.py` 第 24 行导入了 `TerminalUI`，第 454-478 行创建并运行 TerminalUI。需要移除这些引用，让 CLI 在独立模式下仅运行 Telegram + 后台服务（无本地 TUI）。

关键修改点在 `main()` 函数中（约第 452-486 行），删除以下代码块：

```python
# ===== 删除这段代码 =====
logger.info("🖥️  Initializing Terminal UI...")
try:
    terminal_ui = TerminalUI(
        event_bus=get_event_bus(),
        refresh_rate=4.0
    )
    
    terminal_ui.set_services(
        download_service=bot.download_service,
        storage_service=bot.storage_service,
        health_monitor=bot.health_monitor,
        telegram_service=bot.telegram_service
    )
    
    logger.info("✅ Terminal UI initialized successfully")
except Exception as e:
    logger.error(f"❌ Failed to initialize Terminal UI: {e}")
    terminal_ui = None

if terminal_ui:
    logger.info("🎮 Starting Terminal UI...")
    terminal_task = asyncio.create_task(terminal_ui.run())
else:
    terminal_task = None

try:
    done, pending = await asyncio.wait(
        [t for t in [terminal_task] if t],
        return_when=asyncio.FIRST_COMPLETED
    )
except Exception as e:
    logger.error(f"Error in main loop: {e}")
# ===== 删除结束 =====
```

替换为：等待 shutdown event 即可

```python
# 替换为:
logger.info("✅ YTBot running (Telegram + background services)")
logger.info("   Press Ctrl+C to stop")

await bot.wait_for_shutdown()
```

同时删除第 24 行的 `from ytbot.ui.terminal import TerminalUI` 和第 25 行的 `from ytbot.core.event_bus import get_event_bus`（如果不再需要）。

- [ ] **Step 3: 验证 cli.py 语法正确**

Run: `python -c "import ast; ast.parse(open('ytbot/cli.py').read()); print('OK')"`
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add ytbot/cli.py ytbot/ui/terminal.py ytbot/ui/formatter.py ytbot/ui/widgets.py
git commit -m "refactor: remove Rich-based TUI layer, keep CLI with Telegram-only mode"
```

---

## Task 3: 创建 Hermes 插件目录结构

**Files:**
- Create: `~/.hermes/plugins/ytbot/plugin.yaml`
- Create: `~/.hermes/plugins/ytbot/__init__.py`
- Create: `~/.hermes/plugins/ytbot/tools/__init__.py`
- Create: `~/.hermes/plugins/ytbot/skills/youtube/SKILL.md`

- [ ] **Step 1: 创建插件目录**

```bash
mkdir -p ~/.hermes/plugins/ytbot/tools
mkdir -p ~/.hermes/plugins/ytbot/skills/youtube
```

- [ ] **Step 2: 编写 plugin.yaml**

```yaml
name: ytbot
version: 0.1.0
description: "YouTube video/audio download, info query, and transcription toolkit"
author: horsenli
requires_env:
  - name: YOUTUBE_COOKIES_FILE
    description: "Path to YouTube cookies file for authenticated downloads"
    optional: true
  - name: YTBOT_OUTPUT_DIR
    description: "Default output directory for downloads"
    optional: true
provides_tools:
  - yt_download
  - yt_info
  - yt_transcribe
provides_hooks:
  - post_tool_call
```

写入路径: `~/.hermes/plugins/ytbot/plugin.yaml`

- [ ] **Step 3: 编写 tools/__init__.py**

空文件即可: `touch ~/.hermes/plugins/ytbot/tools/__init__.py`

- [ ] **Step 4: 编写 SKILL.md 工作流指引**

写入路径: `~/.hermes/plugins/ytbot/skills/youtube/SKILL.md`:

```markdown
---
name: youtube
description: "YouTube content downloading and processing workflow"
version: 1.0.0
metadata:
  hermes:
    tags: [video, youtube, download, media]
    requires_toolsets: [ytbot]
---

# YouTube Content Workflow

When the user asks to download or process YouTube content, follow this workflow:

## Step 1: Query Video Info
Call `yt_info` with the URL to get metadata (title, duration, available formats).
Review the results with the user before downloading.

## Step 2: Download
Call `yt_download` with appropriate parameters:
- Use `format: "audio"` for podcast/music extraction (smaller files)
- Use `format: "video"` with quality like `"1080p"` for HD video
- Playlist URLs are automatically handled as batch downloads
- Always confirm output directory for large files (>100MB)

## Step 3: Transcribe (Optional)
If the user needs text content (subtitles, notes, quotes):
1. First download audio using `yt_download` with `format: "audio"`
2. Then call `yt_transcribe` with the downloaded audio file path or original URL

## Notes
- For age-restricted or private videos, ensure YouTube cookies are configured via YOUTUBE_COOKIES_FILE env var
- Prefer audio format when only speech content is needed (much smaller files, faster)
- Large files may take significant time; inform the user about expected wait times
- If download fails, check if yt-dlp needs updating or if cookies have expired
```

- [ ] **Step 5: 验证目录结构完整**

Run: `find ~/.hermes/plugins/ytbot -type f | sort`
Expected 输出:
```
~/.hermes/plugins/ytbot/plugin.yaml
~/.hermes/plugins/ytbot/__init__.py
~/.hermes/plugins/ytbot/tools/__init__.py
~/.hermes/plugins/ytbot/skills/youtube/SKILL.md
```

---

## Task 4: 实现 yt_info Tool

**Files:**
- Create: `~/.hermes/plugins/ytbot/tools/yt_info.py`
- Modify: `~/.hermes/plugins/ytbot/__init__.py`

- [ ] **Step 1: 编写 yt_info.py tool handler**

写入路径: `~/.hermes/plugins/ytbot/tools/yt_info.py`:

```python
"""Hermes tool: Query YouTube video/playlist information."""

import json
import asyncio
import logging
import os
import sys

logger = logging.getLogger("ytbot.hermes")

# 将 ytbot 项目根目录加入 sys.path 以便 import 核心模块
_YTBOT_ROOT = os.path.expanduser("~/Works/ytbot")
if _YTBOT_ROOT not in sys.path:
    sys.path.insert(0, _YTBOT_ROOT)

from tools.registry import tool_result, tool_error


YT_INFO_SCHEMA = {
    "name": "yt_info",
    "description": "Query YouTube video or playlist metadata without downloading. Returns title, duration, uploader, available formats, filesize estimate, and thumbnail URL.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "YouTube video URL, playlist URL, or short link"
            }
        },
        "required": ["url"]
    }
}


def _check_ytdlp() -> bool:
    """Check if yt-dlp is installed."""
    try:
        import yt_dlp
        return True
    except ImportError:
        return False


def _handle_info(args, **kwargs) -> str:
    """Tool handler: extract YouTube content info."""
    url = args.get("url", "")
    if not url:
        return tool_error("Missing required parameter: url")

    try:
        from ytbot.platforms.youtube import YouTubeHandler

        handler = YouTubeHandler()

        async def _extract():
            info = await handler.get_content_info(url)
            return info

        info = asyncio.get_event_loop().run_until_complete(_extract())

        if info is None:
            return tool_error(f"Could not extract info for URL: {url}. The video may be private, region-restricted, or the URL may be invalid.")

        result = {
            "success": True,
            "title": info.title,
            "description": info.description,
            "duration_seconds": info.duration,
            "uploader": info.uploader,
            "upload_date": info.upload_date,
            "content_type": info.content_type.value if info.content_info else None,
            "thumbnail_url": info.thumbnail_url,
            "file_size_estimate": info.file_size_estimate,
            "url": info.url,
        }

        if info.formats:
            result["available_formats"] = [
                {"format_id": f.get("format_id"), "ext": f.get("ext"), "resolution": f.get("resolution"), "filesize": f.get("filesize")}
                for f in (info.formats[:10] if len(info.formats) > 10 else info.formats)
            ]
            result["total_formats"] = len(info.formats)

        return tool_result(result)

    except Exception as e:
        logger.exception("yt_info failed")
        return tool_error(f"Failed to query video info: {type(e).__name__}: {e}")
```

- [ ] **Step 2: 验证模块可导入**

Run: `cd /Users/horsenli/Works/ytbot && python -c "import sys; sys.path.insert(0, '.'); exec(open('$HOME/.hermes/plugins/ytbot/tools/yt_info.py').read().split('def _handle_info')[0]); print('Schema OK:', YT_INFO_SCHEMA['name'])"`
Expected: Schema OK: yt_info

- [ ] **Step 3: Commit**

```bash
git add ~/.hermes/plugins/ytbot/tools/yt_info.py
git commit -m "feat(hermes-plugin): add yt_info tool handler"
```

---

## Task 5: 实现 yt_download Tool

**Files:**
- Create: `~/.hermes/plugins/ytbot/tools/yt_download.py`
- Modify: `~/.hermes/plugins/ytbot/__init__.py`

- [ ] **Step 1: 编写 yt_download.py tool handler**

写入路径: `~/.hermes/plugins/ytbot/tools/yt_download.py`:

```python
"""Hermes tool: Download YouTube video/audio/playlist."""

import json
import asyncio
import logging
import os
import sys
import time

logger = logging.getLogger("ytbot.hermes")

_YTBOT_ROOT = os.path.expanduser("~/Works/ytbot")
if _YTBOT_ROOT not in sys.path:
    sys.path.insert(0, _YTBOT_ROOT)

from tools.registry import tool_result, tool_error


YT_DOWNLOAD_SCHEMA = {
    "name": "yt_download",
    "description": "Download YouTube video, audio, or playlist. Supports format selection (video/audio/best), quality control, and cookie-based authentication for age-restricted content.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "YouTube video URL, playlist URL, or short link"
            },
            "format": {
                "type": "string",
                "enum": ["video", "audio", "best"],
                "description": "Download format. 'audio' extracts sound only (smaller). 'video' downloads with visuals. 'best' lets yt-dlp decide."
            },
            "output_dir": {
                "type": "string",
                "description": "Output directory path (default: ./downloads)"
            },
            "quality": {
                "type": "string",
                "description": "Quality selector, e.g. '1080p', '720p', '320k' for audio"
            }
        },
        "required": ["url"]
    }
}


def _check_ytdlp() -> bool:
    try:
        import yt_dlp
        return True
    except ImportError:
        return False


def _handle_download(args, **kwargs) -> str:
    """Tool handler: download YouTube content."""
    url = args.get("url", "")
    if not url:
        return tool_error("Missing required parameter: url")

    fmt = args.get("format", "best")
    output_dir = args.get("output_dir") or os.environ.get("YTBOT_OUTPUT_DIR", "./downloads")
    quality = args.get("quality")

    start_time = time.time()
    logger.info(f"[yt_download] Starting: url={url}, format={fmt}, output_dir={output_dir}")

    try:
        from ytbot.platforms.youtube import YouTubeHandler
        from ytbot.core.types import ContentType

        handler = YouTubeHandler()

        # Map format string to ContentType enum
        type_map = {
            "video": ContentType.VIDEO,
            "audio": ContentType.AUDIO,
            "best": ContentType.VIDEO,
        }
        content_type = type_map.get(fmt, ContentType.VIDEO)

        async def _do_download():
            result = await handler.download_content(
                url=url,
                content_type=content_type,
                format_id=quality,
            )
            return result

        result = asyncio.get_event_loop().run_until_complete(_do_download())
        elapsed = time.time() - start_time

        if not result.success:
            return tool_error(
                f"Download failed: {result.error_message or 'unknown error'}",
                elapsed_seconds=round(elapsed, 1),
            )

        file_path = result.file_path or "unknown"
        file_size = ""
        if file_path != "unknown" and os.path.exists(file_path):
            file_size = f"{os.path.getsize(file_path) / (1024*1024):.1f} MB"

        return tool_result({
            "success": True,
            "file_path": file_path,
            "format": fmt,
            "file_size": file_size,
            "elapsed_seconds": round(elapsed, 1),
            "title": result.content_info.title if result.content_info else None,
        })

    except Exception as e:
        logger.exception("[yt_download] Exception occurred")
        return tool_error(
            f"Download failed: {type(e).__name__}: {e}",
            elapsed_seconds=round(time.time() - start_time, 1),
        )
```

- [ ] **Step 2: 验证语法**

Run: `python -c "compile(open('$HOME/.hermes/plugins/ytbot/tools/yt_download.py').read(), 'yt_download.py', 'exec'); print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add ~/.hermes/plugins/ytbot/tools/yt_download.py
git commit -m "feat(hermes-plugin): add yt_download tool handler"
```

---

## Task 6: 实现 yt_transcribe Tool

**Files:**
- Create: `~/.hermes/plugins/ytbot/tools/yt_transcribe.py`
- Modify: `~/.hermes/plugins/ytbot/__init__.py`

- [ ] **Step 1: 编写 yt_transcribe.py tool handler**

写入路径: `~/.hermes/plugins/ytbot/tools/yt_transcribe.py`:

```python
"""Hermes tool: Transcribe YouTube audio to text."""

import json
import asyncio
import logging
import os
import subprocess
import sys
import tempfile

logger = logging.getLogger("ytbot.hermes")

_YTBOT_ROOT = os.path.expanduser("~/Works/ytbot")
if _YTBOT_ROOT not in sys.path:
    sys.path.insert(0, _YTBOT_ROOT)

from tools.registry import tool_result, tool_error


YT_TRANSCRIBE_SCHEMA = {
    "name": "yt_transcribe",
    "description": "Transcribe YouTube audio to text. Can accept either a YouTube URL (downloads audio first then transcribes) or a local audio file path. Requires whisper or faster-whisper.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "YouTube URL to download audio from and transcribe"
            },
            "file_path": {
                "type": "string",
                "description": "Local audio file path to transcribe (use instead of url if you already have the file)"
            },
            "language": {
                "type": "string",
                "description": "Source language code (e.g., 'en', 'zh', 'ja'). Auto-detect if omitted."
            },
            "model": {
                "type": "string",
                "enum": ["whisper", "faster-whisper"],
                "description": "Transcription model engine (default: whisper)"
            }
        },
        "required": []  # At least one of url or file_path needed
    }
}


def _check_whisper() -> bool:
    """Check if any whisper variant is installed."""
    try:
        import whisper
        return True
    except ImportError:
        pass
    try:
        import faster_whisper
        return True
    except ImportError:
        return False


def _handle_transcribe(args, **kwargs) -> str:
    """Tool handler: transcribe audio."""
    url = args.get("url", "")
    file_path = args.get("file_path", "")
    language = args.get("language")
    model_name = args.get("model", "whisper")

    if not url and not file_path:
        return tool_error("Either 'url' or 'file_path' must be provided")

    audio_file = file_path

    # If URL given, download audio first
    if url and not audio_file:
        try:
            from ytbot.platforms.youtube import YouTubeHandler
            from ytbot.core.types import ContentType

            handler = YouTubeHandler()

            async def _download_audio():
                result = await handler.download_content(
                    url=url,
                    content_type=ContentType.AUDIO,
                )
                return result

            dl_result = asyncio.get_event_loop().run_until_complete(_download_audio())
            if not dl_result.success or not dl_result.file_path:
                return tool_error(
                    f"Audio download failed: {dl_result.error_message or 'unknown'}"
                )
            audio_file = dl_result.file_path
            logger.info(f"[yt_transcribe] Downloaded audio to {audio_file}")

        except Exception as e:
            return tool_error(f"Audio download failed before transcription: {e}")

    if not audio_file or not os.path.exists(audio_file):
        return tool_error(f"Audio file not found: {audio_file}")

    # Run transcription
    try:
        cmd = ["python", "-c", f"""
import sys
model_name = "{model_name}"
audio_file = r"{audio_file}"
language = {"language": language}["language"] if {language is not None} else None

if model_name == "faster-whisper":
    from faster_whisper import WhisperModel
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, info = model.transcribe(audio_file, language=language)
    text = "".join(seg.text for seg in segments)
else:
    import whisper
    model = whisper.load_model("base")
    result = model.transcribe(audio_file, language=language)
    text = result["text"]

print(text)
"""]
        
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if proc.returncode != 0:
            return tool_error(f"Transcription process failed (exit code {proc.returncode}): {proc.stderr.strip()}")

        text = proc.stdout.strip()
        if not text:
            return tool_error("Transcription returned empty text")

        return tool_result({
            "success": True,
            "text": text,
            "audio_file": audio_file,
            "model": model_name,
            "char_count": len(text),
        })

    except subprocess.TimeoutExpired:
        return tool_error("Transcription timed out after 300 seconds")
    except Exception as e:
        logger.exception("[yt_transcribe] Exception")
        return tool_error(f"Transcription failed: {type(e).__name__}: {e}")
```

- [ ] **Step 2: 验证语法**

Run: `python -c "compile(open('$HOME/.hermes/plugins/ytbot/tools/yt_transcribe.py').read(), 'ok', 'exec'); print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add ~/.hermes/plugins/ytbot/tools/yt_transcribe.py
git commit -m "feat(hermes-plugin): add yt_transcribe tool handler"
```

---

## Task 7: 编写插件注册入口 __init__.py

**Files:**
- Create: `~/.hermes/plugins/ytbot/__init__.py`

- [ ] **Step 1: 编写 register() 入口函数**

写入路径: `~/.hermes/plugins/ytbot/__init__.py`:

```python
"""YTBot Plugin for Hermes Agent.

Registers YouTube download/info/transcribe tools into the Hermes
tool registry so the AI agent can invoke them.
"""

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("ytbot.plugin")

# Ensure ytbot package is importable
_YTBOT_ROOT = os.path.expanduser("~/Works/ytbot")
if _YTBOT_ROOT not in sys.path:
    sys.path.insert(0, _YTBOT_ROOT)


def _log_download_hook(tool_name, args, result, **kwargs):
    """post_tool_call hook: log yt_ tool invocations."""
    if not tool_name.startswith("yt_"):
        return None
    
    try:
        parsed = result if isinstance(result, dict) else {}
        url = (args or {}).get("url", "N/A")
        success = parsed.get("success", False)
        logger.info(
            "[plugin:ytbot] tool=%s url=%s success=%s",
            tool_name, url, success,
        )
    except Exception:
        pass
    
    return None


def register(ctx):
    """Entry point called by Hermes PluginManager."""
    from tools.yt_download import (
        YT_DOWNLOAD_SCHEMA, _handle_download, _check_ytdlp
    )
    from tools.yt_info import (
        YT_INFO_SCHEMA, _handle_info
    )
    from tools.yt_transcribe import (
        YT_TRANSCRIBE_SCHEMA, _handle_transcribe, _check_whisper
    )

    ctx.register_tool(
        name="yt_download",
        toolset="ytbot",
        schema=YT_DOWNLOAD_SCHEMA,
        handler=_handle_download,
        check_fn=_check_ytdlp,
        emoji="🎬",
    )

    ctx.register_tool(
        name="yt_info",
        toolset="ytbot",
        schema=YT_INFO_SCHEMA,
        handler=_handle_info,
        check_fn=_check_ytdlp,
        emoji="📋",
    )

    ctx.register_tool(
        name="yt_transcribe",
        toolset="ytbot",
        schema=YT_TRANSCRIBE_SCHEMA,
        handler=_handle_transcribe,
        check_fn=_check_whisper,
        emoji="🎤",
    )

    skill_path = Path(__file__).parent / "skills" / "youtube" / "SKILL.md"
    if skill_path.exists():
        ctx.register_skill("youtube", skill_path)
        logger.info("[plugin:ytbot] Registered skill: youtube")

    ctx.register_hook("post_tool_call", _log_download_hook)
    logger.info("[plugin:ytbot] Plugin registered: 3 tools, 1 skill, 1 hook")
```

- [ ] **Step 2: 验证注册函数可加载**

Run: `cd ~/.hermes/plugins/ytbot && python -c "import sys; sys.path.insert(0, '$HOME/Works/ytbot'); exec(open('__init__.py').read()); print('register function defined:', callable(register))"`
Expected: register function defined: True

- [ ] **Step 3: Commit**

```bash
git add ~/.hermes/plugins/ytbot/__init__.py
git commit -m "feat(hermes-plugin): add plugin registration entry point with 3 tools + skill + hook"
```

---

## Task 8: 双模式验证

**Files:**
- Test: 独立模式 (`python -m ytbot`)
- Test: 插件模式 (hermes 加载插件)

- [ ] **Step 1: 验证独立 CLI 模式仍可启动**

Run: `cd /Users/horsenli/Works/ytbot && python -c "from ytbot.cli import cli; print('cli() function imported successfully')"`
Expected: cli() function imported successfully

注意: 完整启动需要 Telegram token 等配置，但至少验证 import 链不因删除 ui/ 文件而断裂。

- [ ] **Step 2: 验证插件可被 Hermes 发现**

Run: `cd /Users/horsenli/Works/ytbot/hermes-agent && python -c "
import sys
sys.path.insert(0, '.')
from hermes_cli.plugins import discover_plugins, get_plugin_manager
discover_plugins()
pm = get_plugin_manager()
plugins = pm.list_plugins()
for p in plugins:
    print(f\"  {p['name']} v{p['version']}: enabled={p['enabled']} tools={p['tools']} error={p.get('error', '')}\")"
Expected: 输出中包含 `ytbot v0.1.0: enabled=True tools=3` 或类似条目

- [ ] **Step 3: 验证工具注册到全局 Registry**

Run: `cd /Users/horsenli/Works/ytbot/hermes-agent && python -c "
import sys
sys.path.insert(0, '.')
from hermes_cli.plugins import discover_plugins
discover_plugins()
from tools.registry import registry
for name in ['yt_download', 'yt_info', 'yt_transcribe']:
    entry = registry.get_entry(name)
    if entry:
        print(f'  ✅ {name} (toolset={entry.toolset})')
    else:
        print(f'  ❌ {name} NOT FOUND')"
Expected: 三个工具全部显示 ✅

- [ ] **Step 4: 端到端工具调用测试 — yt_info**

Run: `cd /Users/horsenli/Works/ytbot/hermes-agent && python -c "
import sys, json
sys.path.insert(0, '.')
from hermes_cli.plugins import discover_plugins
discover_plugins()
from tools.registry import registry
result = registry.dispatch('yt_info', {'url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'})
parsed = json.loads(result)
print(json.dumps(parsed, indent=2, ensure_ascii=False)[:500])"
Expected: 返回包含 title、duration 等字段的 JSON（或错误信息说明原因）

- [ ] **Step 5: 最终 Commit（如有遗漏修复）**

```bash
git add -A
git commit -m "test: verify dual-mode operation (standalone CLI + hermes plugin)"
```

---

## 自检清单

### Spec 覆盖度
- [x] 3 个 Tool (yt_download, yt_info, yt_transcribe) — Task 4/5/6
- [x] plugin.yaml 清单 — Task 3 Step 2
- [x] SKILL.md 工作流指引 — Task 3 Step 4
- [x] post_tool_call Hook — Task 7
- [x] 双模式支持（保留 cli.py）— Task 2 Step 2
- [x] 移除 Rich 依赖 — Task 1
- [x] 删除 ui/ 目录 — Task 2 Step 1

### 占位符扫描
- [x] 无 TBD/TODO
- [x] 每个 Step 包含实际代码或命令
- [x] 文件路径均为绝对路径或相对于项目根的明确路径

### 类型一致性
- [x] handler 签名统一: `(args, **kwargs) -> str` (JSON)
- [x] check_fn 统一返回 `bool`
- [x] schema 格式统一使用 OpenAI function calling 格式

# YTBot → Hermes-Agent Plugin 集成设计文档

> 日期: 2026-04-20
> 状态: 已确认，待实施
> 方案: A - 目录插件 (Directory Plugin)

## 1. 背景与目标

### 1.1 现状

**ytbot**: 一个 YouTube/Twitter 内容下载机器人，基于 yt-dlp 实现，拥有：
- `YouTubeHandler`: 视频/音频/播放列表下载（[platforms/youtube.py](../ytbot/platforms/youtube.py)）
- Rich 终端 UI（[ui/terminal.py](../ytbot/ui/terminal.py)）
- Telegram 消息通道
- 多存储后端（本地/Nextcloud）

**hermes-agent**: Nous Research 的 AI Agent 框架，具备：
- Tool Registry: 统一工具注册与调度（[tools/registry.py](../hermes-agent/tools/registry.py)）
- Plugin System: 三种插件源（目录/Pip/Entry-Point）（[hermes_cli/plugins.py](../hermes-agent/hermes_cli/plugins.py)）
- Skills System: SKILL.md 工作流指引（[tools/skills_tool.py](../hermes-agent/tools/skills_tool.py)）
- MCP 集成: 外部服务连接（[tools/mcp_tool.py](../hermes-agent/tools/mcp_tool.py)）
- Hook 系统: 生命周期钩子

### 1.2 目标

将 ytbot 的核心能力（YouTube 下载/查询/转录）集成到 hermes-agent 中作为 **目录插件**，使 Hermes AI Agent 能够直接调用 YouTube 相关工具。同时移除 ytbot 对 Rich 库的 UI/日志依赖。

### 1.3 约束

- ytbot 核心下载逻辑必须可复用（不重写）
- 移除 `rich` 依赖，改用标准 `logging`
- 删除 `ui/` 目录下的终端 UI 代码
- 插件部署路径: `~/.hermes/plugins/ytbot/`

---

## 2. 方案选择

### 2.1 候选方案对比

| 维度 | A: 目录插件 | B: Pip 包插件 | C: MCP Server |
|------|:-:|:-:|:-:|
| 开发工作量 | 低 | 中 | 高 |
| 分发便利性 | 手动部署 | pip install | 独立进程 |
| 运行时性能 | 同进程最优 | 同进程最优 | 跨进程 IPC |
| 耦合程度 | 中 | 低 | 零 |
| Hook/Skill 支持 | 全部 | 全部 | 仅 Tool |

### 2.2 决策: 方案 A - 目录插件

**选择理由**:
1. 最小改动：现有 `YouTubeHandler` 几乎可直接包装为 Tool Handler
2. 开发阶段最灵活：修改后立即生效，无需重新安装
3. 完整的 Plugin API 支持：Tool + Skill + Hook + Command
4. 可平滑迁移到方案 B（添加 pyproject.toml + entry point 即可）

---

## 3. 详细架构设计

### 3.1 插件目录结构

```
~/.hermes/plugins/ytbot/
├── plugin.yaml              # 插件清单清单
├── __init__.py              # register(ctx) 入口
├── tools/
│   ├── __init__.py
│   ├── yt_download.py       # 视频/音频/播放列表下载
│   ├── yt_info.py           # 视频信息查询
│   └── yt_transcribe.py     # 音频转录
└── skills/
    └── youtube/
        └── SKILL.md         # Agent 工作流指引
```

### 3.2 plugin.yaml

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

### 3.3 注册入口 (__init__.py)

调用 `PluginContext` API 完成:
- `ctx.register_tool()` × 3: 注册三个工具到全局 Registry
- `ctx.register_skill()`: 注册 SKILL.md 工作流
- `ctx.register_hook()`: 注册下载日志钩子

每个 tool handler 遵循签名: `handler(args: dict, **kwargs) -> str` (JSON 字符串)

### 3.4 工具定义

#### 3.4.1 yt_download

| 字段 | 说明 |
|------|------|
| name | yt_download |
| toolset | ytbot |
| 功能 | 下载 YouTube 视频/音频/播放列表 |
| 必需参数 | url (string) |
| 可选参数 | format (video\|audio\|best), output_dir, quality |
| check_fn | 检测 yt-dlp 是否安装 |
| 复用代码 | `YouTubeHandler.download()` |

#### 3.4.2 yt_info

| 字段 | 说明 |
|------|------|
| name | yt_info |
| toolset | ytbot |
| 功能 | 查询视频元信息（标题、时长、可用格式） |
| 必需参数 | url (string) |
| 可选参数 | None |
| check_fn | 同上 |

#### 3.4.3 yt_transcribe

| 字段 | 说明 |
|------|------|
| name | yt_transcribe |
| toolset | ytbot |
| 功能 | 下载音频并转录为文本 |
| 必需参数 | url (string) 或 file_path (string) |
| 可选参数 | language, model (whisper\|faster-whisper) |
| check_fn | 检测 whisper 或 faster-whisper |

### 3.5 SKILL.md 设计

Agent 通过 `skill_view("ytbot:youtube")` 加载工作流指引:

1. **Step 1**: 调用 `yt_info` 获取视频元数据
2. **Step 2**: 调用 `yt_download` 下载（根据用户需求选格式）
3. **Step 3** (可选): 调用 `yt_transcribe` 转录

包含: 格式选择指南、Cookie 配置提示、大文件确认策略

### 3.6 Hook 设计

`post_tool_call` Hook:
- 当 `tool_name` 以 `yt_` 开头时，记录操作日志
- 记录: URL、格式、文件大小、耗时
- 输出到 hermes 日志系统（非 Rich console）

### 3.7 数据流（双模式架构）

```
┌──────────────────────────────────────────────────────┐
│                   ytbot 共享层                        │
│  (platforms/, services/, storage/, core/)            │
│                                                      │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │YouTubeHandler│  │DownloadService│  │ Storage     │ │
│  └──────┬──────┘  └──────┬───────┘  └──────┬──────┘ │
└─────────┼────────────────┼─────────────────┼─────────┘
          │                │                 │
    ┌─────▼──────┐   ┌────▼─────┐    ┌──────▼──────┐
    │  模式 1:    │   │  模式 2:  │    │             │
    │  独立 CLI   │   │Hermes 插件│    │             │
    │            │   │           │    │             │
    │ cli.py ────┼──▶│__init__.py│    │             │
    │ (print/log)│   │register() │    │             │
    └────────────┘   └─────┬─────┘    └─────────────┘
                           │
              ┌────────────▼────────────┐
              │   Hermes Agent Loop      │
              │  LLM → yt_download Tool  │
              │     → 共享层 → JSON 结果│
              └─────────────────────────┘
```

**关键设计**: `cli.py` 和插件 `__init__.py` 是两个**平等入口**，都调用同一套共享层代码。

---

## 4. ytbot 改造范围

### 4.1 保留的模块（无改动或小改）

| 文件 | 操作 | 说明 |
|------|------|------|
| `ytbot/core/config.py` | ✅ 保留 | 纯配置管理，无 Rich 依赖 |
| `ytbot/core/types.py` | ✅ 保留 | 数据类型定义 |
| `ytbot/core/logger.py` | ⚠️ 改造 | 替换为标准 logging |
| `ytbot/platforms/youtube.py` | ⚠️ 小改 | 移除 Rich logger 引用 |
| `ytbot/platforms/base.py` | ✅ 保留 | 抽象基类 |
| `ytbot/services/download_service.py` | ✅ 保留 | 下载服务逻辑 |
| `ytbot/storage/` | ✅ 保留 | 存储后端 |
| `ytbot/utils/` | ✅ 保留 | 工具函数 |

### 4.2 删除的模块

| 文件 | 原因 |
|------|------|
| `ytbot/ui/terminal.py` | 由 hermes TUI 完全替代（插件模式下） |
| `ytbot/ui/formatter.py` | 不再需要 Rich 格式化输出 |
| `ytbot/ui/widgets.py` | StatusBar/InputPrompt/MainContentArea 全部废弃 |

> **重要**: `cli.py` **保留不删除**。ytbot 必须保持**双模式运行能力**:
> - **模式 1 — 独立 CLI**: `python -m ytbot` 或 `ytbot-cli` 直接运行，使用标准 logging 输出
> - **模式 2 — Hermes 插件**: 通过 `~/.hermes/plugins/ytbot/__init__.py` 的 `register(ctx)` 加载
>
> 核心业务逻辑 (`platforms/`, `services/`, `storage/`) 是两种模式的共享层。

### 4.3 日志改造

**Before (Rich)**:
```python
from rich.console import Console
console = Console()
console.log("[green]Download complete[/green]")
```

**After (standard logging)**:
```python
import logging
logger = logging.getLogger("ytbot")
logger.info("Download complete")
```

---

## 5. 实施步骤

### Phase 1: ytbot 清理（双模式适配）
1. 将 `enhanced_logger.py` 中的 Rich Console 替换为标准 `logging` 模块
2. 更新 `youtube.py` 中所有 `console.log()` 调用为 `logger.info/error`
3. 删除 `ui/terminal.py`, `ui/formatter.py`, `ui/widgets.py`（Rich UI 层）
4. **保留并改造 `cli.py`**: 移除 Rich 依赖，使用标准 logging + print 输出，保持独立 CLI 入口能力
5. 确保 `platforms/`, `services/`, `storage/`, `core/` 无 Rich 导入残留

### Phase 2: 插件骨架
1. 创建 `~/.hermes/plugins/ytbot/` 目录结构
2. 编写 `plugin.yaml`
3. 编写 `__init__.py` register() 函数（import ytbot 核心模块）
4. 实现 `_check_ytdlp()` 检查函数

### Phase 3: Tool 实现
1. 实现 `tools/yt_download.py` — 包装 YouTubeHandler.download()
2. 实现 `tools/yt_info.py` — 包装 YouTubeHandler.get_info()
3. 实现 `tools/yt_transcribe.py` — 下载+转录流程
4. 每个 handler 返回 JSON 字符串（使用 `tool_result()` / `tool_error()`）

### Phase 4: Skill 与 Hook
1. 编写 `skills/youtube/SKILL.md` 工作流文档
2. 实现 `post_tool_call` 日志钩子
3. 在 hermes 中测试端到端流程

### Phase 5: 双模式验证
1. 独立模式测试: `python -m ytbot` 直接运行 CLI
2. 插件模式测试: hermes 中调用 yt_download / yt_info / yt_transcribe
3. 共享层验证: 核心代码在两种模式下行为一致
4. 边界测试: 无 Cookie、大文件、网络异常

---

## 6. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| yt-dlp 版本兼容性 | 下载失败 | check_fn 检测 + 错误消息引导 |
| 大文件下载阻塞 Agent | 用户等待久 | 异步执行 + 进度回调 |
| YouTube 反爬 | 403 错误 | Cookie 配置指引 + 重试机制 |
| 依赖路径冲突 | import 失败 | 插件内 sys.path 管理 |

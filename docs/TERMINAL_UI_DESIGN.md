# YTBot 本地终端交互界面设计方案

**日期**: 2026-04-18
**版本**: v1.0
**状态**: 待审批

---

## 1. 设计目标

为 YTBot 添加本地终端交互能力，使其能够：
- ✅ 通过终端直接输入链接进行下载
- ✅ 查看实时系统状态和任务进度
- ✅ 管理下载任务队列
- ✅ 与 **Telegram Bot 并行运行**，双通道接收输入

---

## 2. 终端 UI 布局设计

### 2.1 整体布局（上下分区）

```
┌─────────────────────────────────────────────────────────────┐
│ 🤖 YTBot v2.5.0 │ 🔵 Telegram: 已连接 │ 💾 存储: Nextcloud│ ← 状态栏 (1行)
├─────────────────────────────────────────────────────────────┤
│                                                             │
│                     主内容区域                                │
│              （输出/日志/任务列表）                            │
│                                                             │
│  [12:34:56] ✅ 下载完成: video.mp4 (125MB)                   │
│  [12:34:50] 📥 下载中: [████████░░] 78% | ⚡ 2.5MB/s        │
│  [12:34:45] ℹ️  检测到 YouTube 链接: youtube.com/watch?v=xxx │
│  [12:34:40] 📋 系统状态: CPU 23% | 内存 45% | 磁盘 12GB     │
│                                                             │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│ > 输入链接或命令...                                          │ ← 输入框 (1行)
└─────────────────────────────────────────────────────────────┘
```

### 2.2 布局组件说明

| 组件 | 高度 | 功能 | Rich 组件 |
|------|------|------|-----------|
| **状态栏** | 1 行 | 显示版本、连接状态、存储状态 | `Console.status` |
| **主内容区** | 动态 | 显示日志、任务列表、下载进度 | `Panel` + `Log` |
| **输入框** | 1 行 | 接收用户输入（链接/命令） | `Prompt` |

### 2.3 视觉风格

- **配色方案**：使用 Rich 默认主题 + 自定义颜色
  - 成功：✅ 绿色
  - 警告：⚠️ 黄色
  - 错误：❌ 红色
  - 信息：ℹ️ 蓝色
  - 进度：📥 青色

- **图标系统**：使用 Emoji 增强可读性
- **时间戳**：每条消息显示 `[HH:MM:SS]`
- **自动滚动**：新内容自动滚动到底部

---

## 3. 核心功能模块设计

### 3.1 支持的命令和操作

#### 📎 链接下载（核心功能）

```
# 直接粘贴链接
> https://www.youtube.com/watch?v=dQw4w9WgXcQ
→ 自动检测平台 → 显示格式选择 → 开始下载

# Twitter/X 链接
> https://twitter.com/user/status/123456
→ 提取内容 → 显示预览 → 保存/下载媒体
```

#### 📋 任务管理命令

```bash
# 查看当前任务列表
> /tasks 或 /list
→ 显示活跃任务、排队任务、已完成任务

# 取消任务
> /cancel <task_id>
→ 取消指定下载任务

# 清空已完成任务
> /clear
→ 清除历史记录
```

#### 📊 状态监控命令

```bash
# 查看系统状态
> /status 或 /info
→ CPU、内存、磁盘、网络状态

# 查看存储状态
> /storage
→ 本地存储、Nextcloud 使用情况

# 查看帮助
> /help
→ 显示所有可用命令
```

#### ⚙️ 系统控制命令

```bash
# 退出程序
> /exit 或 /quit 或 q
→ 优雅关闭所有服务

# 清屏
> /clear 或 cls
→ 清空主内容区

# 切换日志级别
> /log debug|info|warning|error
→ 调整日志详细程度
```

---

## 4. 技术架构设计

### 4.1 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                      YTBot Application                        │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌───────────────────┐    ┌──────────────────────────────┐   │
│  │  Terminal UI      │    │  Telegram Bot               │   │
│  │  (Rich REPL)      │    │  (现有功能)                  │   │
│  │                   │    │                              │   │
│  │  ┌─────────────┐  │    │  ┌────────────────────┐     │   │
│  │  │ Status Bar  │  │    │  │ Message Handler     │     │   │
│  │  ├─────────────┤  │    │  ├────────────────────┤     │   │
│  │  │ Main Panel  │◄─┼────┼──┤ Event Bus (共享)    │     │   │
│  │  │ (Log/Task)  │  │    │  ├────────────────────┤     │   │
│  │  ├─────────────┤  │    │  │ Download Service    │     │   │
│  │  │ Input Prompt│  │    │  └────────────────────┘     │   │
│  │  └─────────────┘  │    │                              │   │
│  └───────────────────┘    └──────────────────────────────┘   │
│                                                               │
│  ┌───────────────────────────────────────────────────────┐   │
│  │              Core Services (共享)                      │   │
│  │  • DownloadService  • StorageService  • Config         │   │
│  │  • HealthMonitor    • UserStateManager                │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 双通道并行运行机制

```python
class YTBot:
    async def start(self):
        # 1. 初始化所有服务（现有逻辑）
        await self._init_services()

        # 2. 启动 Telegram Bot（现有逻辑）
        telegram_task = asyncio.create_task(
            self.telegram_service.start_polling()
        )

        # 3. 启动终端 REPL（新增）
        terminal_task = asyncio.create_task(
            self.terminal_ui.run()  # Rich REPL
        )

        # 4. 等待任一通道触发关闭
        await self._shutdown_event.wait()

        # 5. 清理资源
        telegram_task.cancel()
        terminal_task.cancel()
```

### 4.3 事件总线设计（Event Bus）

为了实现终端和 Telegram 的数据同步，引入轻量级事件总线：

```python
from typing import Dict, Any, Callable, List
import asyncio

class EventBus:
    """简单的事件总线，用于组件间通信"""

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}

    def subscribe(self, event_type: str, handler: Callable):
        """订阅事件"""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)

    async def publish(self, event_type: str, data: Dict[str, Any]):
        """发布事件（异步通知所有订阅者）"""
        if event_type in self._subscribers:
            for handler in self._subscribers[event_type]:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(data)
                    else:
                        handler(data)
                except Exception as e:
                    logger.error(f"Event handler error: {e}")

# 事件类型定义
class Events:
    # 下载事件
    DOWNLOAD_STARTED = "download.started"
    DOWNLOAD_PROGRESS = "download.progress"
    DOWNLOAD_COMPLETED = "download.completed"
    DOWNLOAD_FAILED = "download.failed"

    # 系统事件
    STATUS_UPDATE = "system.status_update"
    LOG_MESSAGE = "system.log_message"

    # Telegram 事件
    TELEGRAM_MESSAGE = "telegram.message"
    TELEGRAM_COMMAND = "telegram.command"
```

---

## 5. 核心组件实现设计

### 5.1 TerminalUI 类（主控制器）

```python
# ytbot/ui/terminal.py
import asyncio
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.log import Log
from rich.prompt import Prompt
from rich.live import Live

class TerminalUI:
    """YTBot 终端交互界面"""

    def __init__(self, event_bus: EventBus):
        self.console = Console()
        self.event_bus = event_bus
        self.layout = Layout()

        # 初始化布局
        self._setup_layout()

        # 状态栏数据
        self.status_data = {
            "version": "2.5.0",
            "telegram": "未连接",
            "storage": "未知"
        }

    def _setup_layout(self):
        """设置上下分区布局"""
        self.layout.split(
            Layout(name="status", size=1),      # 状态栏
            Layout(name="main"),                  # 主内容区
            Layout(name="input", size=1)          # 输入框
        )

    def _render_status_bar(self) -> Panel:
        """渲染顶部状态栏"""
        status_text = (
            f"🤖 YTBot {self.status_data['version']} │ "
            f"🔵 Telegram: {self.status_data['telegram']} │ "
            f"💾 存储: {self.status_data['storage']}"
        )
        return Panel(status_text, style="bold blue")

    def _get_input(self) -> str:
        """获取用户输入（非阻塞式）"""
        return Prompt.ask("> ", console=self.console)

    async def run(self):
        """主循环：渲染界面 + 处理输入"""
        with Live(self.render(), console=self.console, refresh_per_second=4):
            while True:
                try:
                    # 1. 获取用户输入
                    user_input = await asyncio.to_thread(self._get_input)

                    # 2. 处理输入
                    if user_input:
                        await self._handle_input(user_input)

                except (KeyboardInterrupt, EOFError):
                    break
                except Exception as e:
                    self.console.print(f"❌ 错误: {e}", style="red")

    async def _handle_input(self, user_input: str):
        """处理用户输入"""
        user_input = user_input.strip()

        if not user_input:
            return

        # 命令处理
        if user_input.startswith("/"):
            await self._handle_command(user_input)
        else:
            # 链接处理
            await self._handle_url(user_input)

    async def _handle_command(self, command: str):
        """处理斜杠命令"""
        cmd = command.lower().split()[0]
        args = command[len(cmd):].strip()

        handlers = {
            "/help": self._cmd_help,
            "/status": self._cmd_status,
            "/tasks": self._cmd_tasks,
            "/cancel": self._cancel_task,
            "/storage": self._cmd_storage,
            "/log": self._set_log_level,
            "/clear": self._clear_screen,
            "/exit": self._cmd_exit,
            "/quit": self._cmd_exit,
            "q": self._cmd_exit,
        }

        handler = handlers.get(cmd)
        if handler:
            await handler(args)
        else:
            self.console.print(f"❌ 未知命令: {command}", style="red")
            self.console.print("💡 输入 /help 查看可用命令", style="yellow")

    async def _handle_url(self, url: str):
        """处理 URL 链接"""
        # 发布事件到事件总线（DownloadService 会监听）
        await self.event_bus.publish(Events.DOWNLOAD_STARTED, {
            "source": "terminal",
            "url": url,
            "user_id": "local_terminal"
        })

        self.console.print(f"📎 收到链接: {url}", style="cyan")
        self.console.print("⏳ 正在分析...", style="yellow")

    def render(self) -> Layout:
        """渲染整个布局"""
        self.layout["status"].update(self._render_status_bar())
        # ... 其他区域的渲染
        return self.layout
```

### 5.2 输出格式化器（Output Formatter）

```python
# ytbot/ui/formatter.py
from rich.text import Text
from rich.table import Table
from datetime import datetime

class OutputFormatter:
    """格式化输出内容"""

    @staticmethod
    def format_log_message(level: str, message: str) -> Text:
        """格式化日志消息"""
        timestamp = datetime.now().strftime("%H:%M:%S")

        emoji_map = {
            "INFO": "ℹ️",
            "SUCCESS": "✅",
            "WARNING": "⚠️",
            "ERROR": "❌",
            "DEBUG": "🔍"
        }

        color_map = {
            "INFO": "cyan",
            "SUCCESS": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "DEBUG": "dim"
        }

        emoji = emoji_map.get(level, "•")
        color = color_map.get(level, "white")

        text = Text()
        text.append(f"[{timestamp}] ", style="dim")
        text.append(f"{emoji} ", style=color)
        text.append(message)

        return text

    @staticmethod
    def format_download_progress(data: dict) -> Text:
        """格式化下载进度"""
        progress = data.get("progress", 0)
        speed = data.get("speed", "")
        eta = data.get("eta", "")

        # 创建进度条
        bar_width = 20
        filled = int(bar_width * progress / 100)
        bar = "█" * filled + "░" * (bar_width - filled)

        text = Text()
        text.append(f"[{datetime.now().strftime('%H:%M:%S')}] ", style="dim")
        text.append(f"📥 [{bar}] {progress:.1f}%")

        if speed:
            text.append(f" | ⚡ {speed}")
        if eta:
            text.append(f" | ⏱️ {eta}")

        return text

    @staticmethod
    def format_task_list(tasks: list) -> Table:
        """格式化任务列表表格"""
        table = Table(title="📋 下载任务", show_lines=True)
        table.add_column("ID", style="cyan", width=6)
        table.add_column("状态", justify="center", width=8)
        table.add_column("标题", style="white", max_width=40)
        table.add_column("进度", justify="right", width=12)
        table.add_column("速度", justify="right", width=10)

        for task in tasks:
            status_emoji = {
                "downloading": "📥",
                "completed": "✅",
                "failed": "❌",
                "queued": "⏳"
            }.get(task["status"], "•")

            table.add_row(
                task["id"],
                f"{status_emoji} {task['status']}",
                task["title"][:37] + "..." if len(task["title"]) > 40 else task["title"],
                f"{task.get('progress', 0)}%",
                task.get("speed", "-")
            )

        return table

    @staticmethod
    def format_system_status(status: dict) -> Panel:
        """格式化系统状态面板"""
        content = (
            f"🖥️  CPU: {status['cpu_percent']}%\n"
            f"💾 内存: {status['memory_percent']}% "
            f"(可用: {status['memory_available_mb']:.0f}MB)\n"
            f"💿 磁盘: {status['disk_percent']}% "
            f"(剩余: {status['disk_space_mb']:.0f}MB)\n"
            f"⏱️ 运行时间: {status['uptime']}"
        )
        return Panel(content, title="📊 系统状态", border_style="blue")
```

---

## 6. 数据流与交互流程

### 6.1 用户发送链接的完整流程

```
用户在终端输入 YouTube 链接
    ↓
TerminalUI._handle_url(url)
    ↓
EventBus.publish(DOWNLOAD_STARTED, {source: "terminal", url: ...})
    ↓
DownloadService 订阅了该事件
    ↓
DownloadService.get_content_info(url)  // 获取视频信息
    ↓
TerminalUI 显示格式选择菜单（内联键盘或选项列表）
    ↓
用户选择格式（如 "video 1080p"）
    ↓
EventBus.publish(DOWNLOAD_PROGRESS, ...)
    ↓
TerminalUI 实时更新进度条
    ↓
下载完成
    ↓
EventBus.publish(DOWNLOAD_COMPLETED, {file_path: ...})
    ↓
StorageService.store_file(file_path)  // 上传到存储
    ↓
TerminalUI 显示完成通知（包含文件路径/URL）
```

### 6.2 终端与 Telegram 的双向同步

```
场景 1: 从终端发起下载，Telegram 也收到通知
┌─────────────┐      ┌──────────────┐      ┌─────────────┐
│   终端      │      │  Event Bus   │      │  Telegram   │
│             │ ───► │              │ ───► │             │
│ 输入链接    │      │ DOWNLOAD_    │      │ 发送通知给  │
│             │      │ STARTED      │      │ admin       │
└─────────────┘      └──────────────┘      └─────────────┘

场景 2: 从 Telegram 发起下载，终端也显示进度
┌─────────────┐      ┌──────────────┐      ┌─────────────┐
│  Telegram   │      │  Event Bus   │      │    终端     │
│             │ ───► │              │ ───► │             │
│ 收到链接    │      │ DOWNLOAD_    │      │ 显示进度条  │
│             │      │ PROGRESS     │      │             │
└─────────────┘      └──────────────┘      └─────────────┘
```

---

## 7. 文件结构规划

### 7.1 新增文件

```
ytbot/
├── ui/                          # 新增：终端 UI 模块
│   ├── __init__.py
│   ├── terminal.py              # 主终端界面（TerminalUI 类）
│   ├── formatter.py             # 输出格式化器
│   ├── commands.py              # 命令处理器
│   └── widgets.py               # 自定义 Rich 组件
│
├── core/
│   └── event_bus.py             # 新增：事件总线
│
└── cli.py                       # 修改：集成终端 UI
```

### 7.2 依赖更新

```python
# requirements.txt 新增
rich>=13.0.0                    # 终端 UI 库
```

---

## 8. 关键技术点

### 8.1 非阻塞输入处理

使用 `asyncio.to_thread()` 将阻塞式的 `Prompt.ask()` 转换为异步：

```python
async def get_user_input(self) -> str:
    """异步获取用户输入（不阻塞事件循环）"""
    try:
        input_text = await asyncio.to_thread(
            Prompt.ask,
            "> ",
            console=self.console
        )
        return input_text
    except (KeyboardInterrupt, EOFError):
        return "/exit"
```

### 8.2 实时界面刷新

使用 Rich 的 `Live` 组件实现动态刷新：

```python
with Live(self.render(), console=self.console, refresh_per_second=4) as live:
    while self.running:
        # 更新数据
        self._update_status_data()
        # 重新渲染
        live.update(self.render())
        # 短暂休眠避免过度刷新
        await asyncio.sleep(0.25)
```

### 8.3 多线程安全

确保终端输出的线程安全性：

```python
from threading import Lock

class TerminalUI:
    def __init__(self):
        self._output_lock = Lock()

    def safe_print(self, content):
        """线程安全的打印方法"""
        with self._output_lock:
            self.console.print(content)
```

---

## 9. 实现优先级与计划

### Phase 1: 基础框架（1-2 天）
- [ ] 安装 rich 依赖
- [ ] 创建 `ui/` 模块目录结构
- [ ] 实现 `TerminalUI` 基础类（上下布局）
- [ ] 实现基本的输入/输出循环
- [ ] 修改 `cli.py` 集成终端 UI

### Phase 2: 核心功能（2-3 天）
- [ ] 实现 URL 链接检测和处理
- [ ] 实现格式选择交互
- [ ] 实现下载进度实时显示
- [ ] 实现基本命令（/help, /status, /exit）

### Phase 3: 任务管理（1-2 天）
- [ ] 实现任务列表显示
- [ ] 实现任务取消功能
- [ ] 实现任务历史记录

### Phase 4: 系统集成（1-2 天）
- [ ] 实现事件总线（EventBus）
- [ ] 终端与 Telegram 数据同步
- [ ] 系统状态监控集成
- [ ] 存储状态查看

### Phase 5: 优化完善（1 天）
- [ ] 键盘快捷键支持
- [ ] 输入历史记录（上下键）
- [ ] Tab 补全
- [ ] 配置项自定义（颜色、刷新频率等）

---

## 10. 测试策略

### 10.1 单元测试
- 测试 URL 检测逻辑
- 测试命令解析
- 测试输出格式化
- 测试事件发布/订阅

### 10.2 集成测试
- 测试终端输入 → 下载流程
- 测试事件总线通信
- 测试多任务并发处理

### 10.3 手动测试用例
1. 启动 YTBot，验证终端界面正常显示
2. 粘贴 YouTube 验证，验证格式选择菜单
3. 执行 `/status` 命令，验证系统状态显示
4. 同时从终端和 Telegram 发起下载，验证并行处理
5. 按 `Ctrl+C` 或输入 `/exit`，验证优雅退出

---

## 11. 风险与缓解措施

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Rich 兼容性问题 | UI 渲染异常 | 添加降级方案（纯文本模式） |
| 异步输入阻塞 | 事件循环卡死 | 使用 `asyncio.to_thread()` |
| 多线程竞争 | 输出混乱 | 使用 Lock 保护输出 |
| 性能开销 | 刷新过频导致卡顿 | 限制刷新频率（4Hz） |

---

## 12. 后续扩展方向

- 🎨 **主题系统**：支持多种颜色主题切换
- 📊 **图表展示**：使用 Rich 的 Bar 图表展示统计信息
- 🔔 **声音提醒**：下载完成时播放提示音
- 📱 **手机适配**：优化小屏幕终端显示
- 🔌 **插件系统**：支持第三方命令插件

---

**审批意见**：

□ 批准此设计方案，可以开始实施  
□ 需要修改以下部分：______________  
□ 暂缓实施，需要进一步讨论  

**签字**：________________  **日期**：__________

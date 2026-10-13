# YTBot 模块化改进建议（二次复核版）

**复核日期**: 2026-10-13
**复核范围**: `ytbot/` 包（不含仓库内独立项目 `hermes-agent/`）
**测试基线**: `tests/unit/` 共 415 个测试用例，pytest 覆盖率门禁 80%（`pytest.ini`）

---

## 〇、本次复核对初稿的勘误

初稿方向基本正确，但经过逐文件取证（行数统计、import 全仓搜索、测试覆盖核对），发现以下**误判和遗漏**，本版予以修正：

| # | 初稿结论 | 复核结果 | 证据 |
|---|----------|----------|------|
| 1 | 最大问题是 cli.py / telegram_handler.py | **遗漏真正的巨型文件 `twitter.py`（5726 行）**，其中单个方法 `_generate_html` 约 1197 行（L3681–L4877） | `wc -l` + 函数边界核对 |
| 2 | PDF 相关 9 个文件建议"合并为一个模块" | 其中 **7 个是零生产引用的死代码（约 1371 行），应删除而非合并**；真正在用的只有 `services/pdf_converter.py`（592 行）和 `pdf_preprocessor.py`（328 行） | 全仓 import 搜索：仅文件自身 docstring 互相提及，无任何生产 import |
| 3 | TelegramService 单例线程安全需修复 | **已是线程安全单例**（`__new__` + `threading.Lock`），无需改动 | [telegram_service.py:62-111](file:///Users/horsenli/Works/ytbot/ytbot/services/telegram_service.py#L62-L111) |
| 4 | 未提及 | **`core/exceptions.py`（414 行）整套异常体系无任何生产代码使用**，仅被它自己的 22 个测试引用；`config.py` 内另定义了一套 `ConfigError` | `grep "exceptions import"` 全仓 0 个生产匹配 |
| 5 | 未提及 | **死代码还有** `ui/commands.py`（403 行，无引用）、`x_content_extractor/`（169 行，无引用）；`setup.py` 入口点 `ytbot.demo:main` 指向不存在的模块 | import 搜索 + `ls ytbot/demo.py` 不存在 |
| 6 | 未提及 | **`/status` 命令存在功能性 Bug**：处理器内 `from ..cli import YTBot; YTBot()` 每次新建实例，所有服务均为 None，导致该命令**永远显示"已停止/未连接"** | [telegram_handler.py:129-131](file:///Users/horsenli/Works/ytbot/ytbot/handlers/telegram_handler.py#L129-L131) + [cli.py:32-47](file:///Users/horsenli/Works/ytbot/ytbot/cli.py#L32-L47) |
| 7 | 未提及 | **下载流程在 Bot 与 CLI 两处重复实现**：播放列表展开、共享格式选择、体积估算、继承格式回退、文件名构造、临时目录清理、进度节流各有一份拷贝 | [one_shot_download.py](file:///Users/horsenli/Works/ytbot/ytbot/services/one_shot_download.py) vs `telegram_handler._proceed_with_download` |
| 8 | 未提及 | **两套 logger 并存**：7 个文件用 `core.logger`，8 个用 `core.enhanced_logger`，配置行为不一致 | import 搜索统计 |
| 9 | 沿用旧报告"版本号不一致"问题 | **已修复**：`setup.py` 已从 `__init__.py` 动态读取版本，`cli.py` 也用 `ytbot.__version__`，该问题删除 | [setup.py:19-31](file:///Users/horsenli/Works/ytbot/setup.py#L19-L31) |
| 10 | 建议"引入 DI 容器" | 降级：项目已有成熟约定（`get_config()`、`get_cache_manager()` 等**模块级工厂函数**），再引入容器属于过度设计；沿用现有约定即可 | 现有代码模式 |

---

## 一、客观数据

### 1.1 文件行数 Top 10（总计约 24,291 行）

| 文件 | 行数 | 评估 |
|------|------|------|
| [twitter.py](file:///Users/horsenli/Works/ytbot/ytbot/platforms/twitter.py) | **5726** | 两个大类 + 1200 行单方法，首要拆分目标 |
| [telegram_handler.py](file:///Users/horsenli/Works/ytbot/ytbot/handlers/telegram_handler.py) | 1895 | 命令/消息/回调/下载/HTML 生成全包 |
| [x_user_archive/runner.py](file:///Users/horsenli/Works/ytbot/ytbot/services/x_user_archive/runner.py) | 973 | 内聚良好，暂不动 |
| [youtube.py](file:///Users/horsenli/Works/ytbot/ytbot/platforms/youtube.py) | 906 | 单类 26 方法，尚可，暂不动 |
| [startup_manager.py](file:///Users/horsenli/Works/ytbot/ytbot/core/startup_manager.py) | 836 | 与 cli.py 职责重叠 |
| [storage_service.py](file:///Users/horsenli/Works/ytbot/ytbot/services/storage_service.py) | 822 | 被 5 处重复实例化 |
| [telegram_service.py](file:///Users/horsenli/Works/ytbot/ytbot/services/telegram_service.py) | 797 | 已是单例，不动结构 |
| [cli.py](file:///Users/horsenli/Works/ytbot/ytbot/cli.py) | 721 | YTBot 上帝类 + 参数解析混合 |

### 1.2 服务实例化点（状态分裂的根源）

- `StorageService()`：[cli.py:628](file:///Users/horsenli/Works/ytbot/ytbot/cli.py#L628)、[cli.py:658](file:///Users/horsenli/Works/ytbot/ytbot/cli.py#L658)、[cli.py:686](file:///Users/horsenli/Works/ytbot/ytbot/cli.py#L686)、[startup_manager.py:529](file:///Users/horsenli/Works/ytbot/ytbot/core/startup_manager.py#L529)、[twitter.py:2824](file:///Users/horsenli/Works/ytbot/ytbot/platforms/twitter.py#L2824) —— **5 处各自 new**，各自持有独立的 Nextcloud 可用性缓存与缓存队列对象。
- `DownloadService()`：[cli.py:76](file:///Users/horsenli/Works/ytbot/ytbot/cli.py#L76)、[cli.py:686](file:///Users/horsenli/Works/ytbot/ytbot/cli.py#L686)、[runner.py:939](file:///Users/horsenli/Works/ytbot/ytbot/services/x_user_archive/runner.py#L939)（该处已自建模块级单例）。

### 1.3 死代码清单（已逐一取证）

| 文件 | 行数 | 取证结论 |
|------|------|----------|
| `platforms/twitter_pdf_extension.py` | 213 | 无生产 import |
| `platforms/twitter_pdf_patch.py` | 254 | 无生产 import |
| `platforms/twitter_pdf_integration.py` | 69 | 仅 docstring 示例引用上者 |
| `storage/storage_pdf_extension.py` | 243 | 无生产 import |
| `storage/local_storage_pdf_patch.py` | 293 | 无生产 import |
| `storage/local_storage_pdf_fix.py` | 98 | 无生产 import |
| `core/pdf_config_guide.py` | 201 | 无生产 import |
| `core/exceptions.py` | 414 | 仅 `tests/unit/test_exceptions.py` 引用 |
| `ui/commands.py` + `ui/__init__.py` | 442 | 全仓无 import |
| `x_content_extractor/x_to_notion_with_real_content.py` | 169 | 全仓无 import（独立脚本性质） |
| **合计** | **约 2396 行** | 约占全包 10% |

保留的 PDF 代码：`services/pdf_converter.py`、`services/pdf_preprocessor.py` 确认在用（twitter.py、x_user_archive/pdf_merger.py 均有 import）。

### 1.4 其他确认的问题

- `CONFIG['...']` 旧式字典访问：**9 个文件 41 处**；新代码用 `get_config().xxx.yyy`，两套并存。
- `--log-level` / `--user-state-timeout` 两个 CLI 参数是**空操作**：只打了一行日志，因配置 frozen 无法生效（[cli.py:584-593](file:///Users/horsenli/Works/ytbot/ytbot/cli.py#L584-L593) 代码注释自承）。
- `EventBus`：实现完整且有 18 个测试，但生产代码 0 发布 0 订阅，属于"架构摆设"。

---

## 二、确认可大幅改进的模块（按 ROI 排序）

| 优先级 | 改进项 | 预计工作量 | 收益 | 风险 |
|--------|--------|-----------|------|------|
| **P0** | A. 删除约 2400 行死代码 | 0.5 天 | 立刻降噪 10%，后续重构不再被干扰 | 极低（有测试门禁） |
| **P0** | B. 服务工厂统一 + 修复 `/status` 失效 Bug | 0.5–1 天 | 消除 5 份状态分裂；修好用户可见功能 | 低 |
| **P1** | C. 拆分 `twitter.py`（5726 行） | 3–5 天 | 最大维护负担解除，可测性显著提升 | 中（文件大，需纯移动分步走） |
| **P1** | D. 收敛 Bot/CLI 重复下载流程 | 2 天 | 消除两份播放列表/格式/进度逻辑的漂移风险 | 中（两侧均有测试兜底） |
| **P2** | E. 拆分 `telegram_handler.py`（在 D 之后自然瘦身） | 2–3 天 | 命令/回调/对话各司其职 | 中 |
| **P2** | F. 配置与日志访问统一，修复两个 CLI 空参数 | 1 天 | 一致性 + 修复假功能 | 低 |
| **P3** | G. EventBus 取舍 + YTBot/StartupManager 职责瘦身 | 1–2 天 | 消除架构摆设与启动职责重叠 | 低 |

---

## 三、具体实施方案

> **统一纪律（每个阶段都遵守）**：
> 1. 一个阶段一个 PR，阶段内只做该阶段的事，不顺手改文案/格式/无关逻辑；
> 2. 每步完成后运行 `pytest tests/unit -m unit`，全绿且覆盖率门禁 80% 通过才进入下一步；
> 3. 纯移动优先（move + 改 import），行为零变化；改名、改逻辑放在移动完成后的独立提交；
> 4. 不碰 `hermes-agent/`（仓库内独立项目）。

### 方案 A：删除死代码（P0，0.5 天）

**删除文件（9 + 1 个测试 + 1 个目录）**：

```
ytbot/platforms/twitter_pdf_extension.py
ytbot/platforms/twitter_pdf_patch.py
ytbot/platforms/twitter_pdf_integration.py
ytbot/storage/storage_pdf_extension.py
ytbot/storage/local_storage_pdf_patch.py
ytbot/storage/local_storage_pdf_fix.py
ytbot/core/pdf_config_guide.py
ytbot/core/exceptions.py
ytbot/ui/                       # 整个目录（commands.py + __init__.py）
ytbot/x_content_extractor/      # 整个目录
tests/unit/test_exceptions.py   # 仅测试已删除的 exceptions.py
```

**修改**：
- [setup.py:77-82](file:///Users/horsenli/Works/ytbot/setup.py#L77-L82)：移除 `ytbot-demo=ytbot.demo:main` 入口点（目标模块不存在，安装后该命令必然报错）。
- 检查 `ytbot/services/__init__.py`、`ytbot/core/__init__.py` 是否 re-export 了被删符号，有则同步清理。

**不要删除**：`services/pdf_converter.py`、`services/pdf_preprocessor.py`（确认在用）。

**步骤**：
1. 先跑一遍全量测试记录基线：`pytest tests/unit`；
2. 删除上述文件；
3. 全仓 grep 残留引用（预期只剩文档 md，不处理文档）；
4. 再跑全量测试 + `pip install -e .` 验证安装无误。

**风险点**：`core/exceptions.py` 删前最后确认无动态引用（已 grep 字符串与 import 两种形态，均无）。

---

### 方案 B：服务工厂统一 + 修复 `/status`（P0，0.5–1 天）

**沿用项目既有约定**——模块级工厂函数（参照 `get_config()`、`get_cache_manager()`、`get_browser_manager()`），**不引入 DI 容器**。

**B1. StorageService 增加工厂**（[storage_service.py](file:///Users/horsenli/Works/ytbot/ytbot/services/storage_service.py) 类定义之后）：

```python
_storage_service: Optional["StorageService"] = None

def get_storage_service() -> "StorageService":
    """进程内共享的 StorageService（Nextcloud 状态与缓存队列必须全局唯一）。"""
    global _storage_service
    if _storage_service is None:
        _storage_service = StorageService()
    return _storage_service
```

DownloadService 同理增加 `get_download_service()`。

**注意：保留类的普通 `__init__`，不要用 `__new__` 单例**——现有测试直接 `TelegramHandler(telegram_service=Mock(), storage_service=Mock(), ...)` 注入假对象（见 [test_telegram_playlist.py:57-60](file:///Users/horsenli/Works/ytbot/tests/unit/test_telegram_playlist.py#L57-L60)），构造器必须保持可替换。

**B2. 替换全部实例化点**：

| 位置 | 改为 |
|------|------|
| cli.py:628、658、686（3 处 CLI 子命令） | `get_storage_service()` |
| cli.py:76、686 | `get_download_service()` |
| startup_manager.py:529 | `get_storage_service()`，并让 StartupManager 把该实例登记到返回的 services 中，保证与 bot 内是同一对象 |
| twitter.py:2824 | `self.storage_service = get_storage_service()` |
| runner.py:939 自建的 `_download_service_singleton` | 删除，改用 `get_download_service()` |

**B3. 修复 `/status` 反向依赖 Bug**：

现状 [telegram_handler.py:129-131](file:///Users/horsenli/Works/ytbot/ytbot/handlers/telegram_handler.py#L129-L131) 新建 `YTBot()` 读状态，永远得到"已停止"。改为读活实例：

```python
# monitoring/status_provider.py（新文件，约 60 行）
def build_status_report() -> dict:
    storage = get_storage_service()
    download = get_download_service()
    return {
        "nextcloud_available": storage.nextcloud_available,
        "local_storage_enabled": get_config().local_storage.enabled,
        "supported_platforms": download.get_supported_platforms(),
        # telegram 连接状态由 telegram_service 单例提供
        "telegram_connected": TelegramService().connected,
        # cache/健康监控数据按需补充
    }
```

`status_command` 改为调用 `build_status_report()`，删除 `from ..cli import YTBot`（同时消除 handlers → cli 的反向依赖）。Bot 运行态（running、健康监控数据）在方案 E 中通过注入一个轻量 `BotContext` 补全；B 阶段先保证不再显示错误信息。

**验证**：新增一个单元测试，mock 工厂返回的对象，断言 `/status` 输出包含"已连接"（当前代码下该测试必然失败，修复后通过）。

---

### 方案 C：拆分 `twitter.py`（P1，3–5 天）

**目标结构**（`platforms/twitter.py` → `platforms/twitter/` 包）：

```
platforms/twitter/
├── __init__.py            # 仅 re-export：TwitterHandler, TwitterContentExtractor
├── handler.py             # TwitterHandler 瘦身后的平台接口实现（路由/content_info/下载编排，约 500 行）
├── extractor/
│   ├── __init__.py        # re-export TwitterContentExtractor 保持对外路径
│   ├── browser.py         # 浏览器生命周期、cookies、登录检测、standalone 管理（L118-760 相关）
│   ├── posts.py           # detect_post_type / detect_thread / detect_video / 外链提取
│   └── content.py         # expand_long_tweet / extract_formatted_content /
│                          # extract_all_posts_content / scrape_tweet 编排
├── rendering/
│   ├── html_renderer.py   # _generate_html（1200 行模板）+ _add_heading_ids/
│   │                      # _generate_toc/_clean_html_content/_convert_x_code_blocks/
│   │                      # _preserve_pre_blocks
│   └── markdown.py        # convert_to_markdown/_replace_code_in_markdown/
│                          # _generate_markdown/_html_to_markdown/_format_paragraphs 等
└── media.py               # _download_images / download_video /
                           # _detect_image_extension / _generate_video_thumbnails_mapping
```

**分步走（每步独立提交、独立跑测试）**：

1. **建空包**：新建 `platforms/twitter/__init__.py`，先把原文件移动为 `platforms/twitter/_legacy.py`，`__init__.py` re-export 全部公开符号；`ytbot/platforms/twitter.py` 删除。此时所有 import 路径不变（`from ..platforms.twitter import TwitterHandler` 仍然有效），跑测试。
2. **抽 rendering（最安全，纯函数多）**：新建 `rendering/html_renderer.py`，把 `_generate_html` 等方法以**模块函数**形式搬出（`render_html(result, local_images, local_videos)`），handler 内保留薄封装 `self._generate_html(...) -> render_html(...)`，跑 `tests/unit` 全量。
3. **抽 markdown.py**：同上。
4. **抽 media.py**。
5. **拆 extractor**：按 browser/posts/content 三块切分，`TwitterContentExtractor` 改为组合这些模块函数的协调类；这是行为风险最高的一步，每切一块跑一次 `test_twitter_browser_lifecycle.py`。
6. 删除所有薄封装、更新类型引用；全量测试 + 一次真实推文抓取冒烟（手工）。

**关键约束**：
- 类名、公开方法签名（`scrape_tweet`、`can_handle`、`download_content`、`generate_telegram_preview` 等）一律不变，因为 [telegram_handler.py:817-848](file:///Users/horsenli/Works/ytbot/ytbot/handlers/telegram_handler.py#L817-L848) 和 x_user_archive 直接依赖；
- HTML 模板字符串只允许移动，不允许改内容（避免与 `test_x_archive*` 的快照/断言冲突）；
- `_generate_html` 1200 行本轮**只搬家不重写**；模板拆分（Jinja 化）另立后续任务。

**验证**：`pytest tests/unit -k twitter` + `-k x_archive` 全绿；手工跑一次 `ytbot --download "<推文URL>" --type video`。

---

### 方案 D：收敛 Bot/CLI 重复下载流程（P1，2 天）

[one_shot_download.py](file:///Users/horsenli/Works/ytbot/ytbot/services/one_shot_download.py) 与 telegram_handler 中 `_proceed_with_download`（L1118–L1441）+ `_proceed_with_large_file_download`（L1729–L1815）存在七处重复逻辑。提取共享内核：

**新增 `services/download_pipeline.py`**：

```python
class ProgressSink(Protocol):
    """进度输出端：Telegram 编辑消息 / 终端打印只是两个实现。"""
    async def on_stage(self, text: str) -> None: ...
    async def on_progress(self, progress_data: dict) -> None: ...
    async def on_done(self, message: str) -> None: ...
    async def on_error(self, message: str) -> None: ...

def build_target_filename(file_path: str, title: str, content_type: str) -> str:
    """合并 handler._store_downloaded_file 与 one_shot._build_filename 两份拷贝。"""

def estimate_format_size(formats, format_id) -> Optional[int]:
    """合并 handler._get_file_size_estimate_from_formats 与
    one_shot._estimate_file_size 两份近乎相同的实现。"""

class PlaylistPlanner:
    """播放列表展开 + 首个视频一次性选格式 + entry 继承（两处共用）。"""

class DownloadPipeline:
    """编排：选格式 → (可选大文件确认钩子) → 下载 → 继承格式回退 →
    存储 → 临时目录清理。Bot 与 CLI 传入不同 ProgressSink 与确认策略。"""
    def __init__(self, download_service, storage_service, sink: ProgressSink,
                 large_file_policy): ...   # Bot: 弹确认；CLI: 打印警告后直接下载
    async def run(self, url, content_type, handler=None,
                  progress_label=None) -> PipelineResult: ...
```

**两个 Sink 实现**：
- `handlers/telegram_progress.py`：搬迁现有的 5 秒/2% 节流 + `edit_message(discardable=True)` 逻辑；
- `services/terminal_progress.py`：搬迁现有的 5 秒/10% 节流 + `print()` 逻辑（含上传进度回调）。

**改造后**：
- `telegram_handler._proceed_with_download` 变为：组装 sink + policy → `pipeline.run(...)`；播放列表递归、大文件状态机仍留在 handler（属于交互层），但内核只有一份；
- `OneShotDownloader._download_and_store` / `_run_playlist` 同样变为薄封装。

**验证**：[test_telegram_playlist.py](file:///Users/horsenli/Works/ytbot/tests/unit/test_telegram_playlist.py) 与 [test_one_shot_download.py](file:///Users/horsenli/Works/ytbot/tests/unit/test_one_shot_download.py) 是现成护栏——目标是这两个测试文件**无需改动**即全绿（如必须改，先确认断言行为是否等价）。

---

### 方案 E：拆分 `telegram_handler.py`（P2，2–3 天，在 D 之后）

D 完成后该文件预计从 1895 行降到约 1100 行，再按交互职责切分：

```
handlers/
├── telegram_handler.py    # 装配器：__init__ + setup_handlers（< 200 行）
├── commands.py            # /start /help /status（调用 B3 的 status_provider）
│                          # /storage /cancel
├── message_routing.py     # handle_message：URL 识别、平台路由、状态分发
├── conversations.py       # 4 种等待状态的文本/回调响应（保存链接、保存文本、
│                          # 下载类型、大文件确认）
└── text_export.py         # 文本保存 + _generate_text_html 的 HTML 模板
                           #（模板后续可移到独立文件，本轮先搬家）
```

**兼容约束**：
- `TelegramHandler` 类名、构造器三参数签名、`setup_handlers()` 必须保持（测试与 cli.py 依赖）；方法可改为委托给各模块；
- `state_manager` 属性的后置注入方式（[cli.py:242-244](file:///Users/horsenli/Works/ytbot/ytbot/cli.py#L242-L244)）保持可用，顺手在构造器增加可选的 `state_manager=None` 参数消除"先建后塞"。

**验证**：现有 handler 相关测试（playlist、flood control、polling params）全绿；为 commands 补 `/status`、`/storage` 权限分支的单测。

---

### 方案 F：配置与日志统一（P2，1 天）

1. **消灭 41 处 `CONFIG[...]`**：9 个文件逐一替换为 `get_config().section.field`；完成后删除 [config.py:355-408](file:///Users/horsenli/Works/ytbot/ytbot/core/config.py#L355-L408) 的 `ConfigDictWrapper` / `ConfigSectionWrapper` / 全局 `CONFIG`。测试 [test_config.py](file:///Users/horsenli/Works/ytbot/tests/unit/test_config.py) 中若有旧字典访问用例，同步改为新 API。
2. **统一 logger**：以 `core/enhanced_logger.py` 为唯一实现（功能是 `core/logger.py` 的超集），把 `core/logger.py` 改为薄 re-export（`from .enhanced_logger import get_logger, setup_logger`），7 个文件的 import 可不改；验证日志格式/文件轮转无变化。
3. **修复两个空操作 CLI 参数**：
   - `--log-level`：在 `cli()` 中调用日志初始化显式设置级别（当前只打日志），替代无操作；
   - `--user-state-timeout`：不要试图改 frozen 配置，直接把值透传给 [cli.py:80-84](file:///Users/horsenli/Works/ytbot/ytbot/cli.py#L80-L84) 的 `UserStateManager(timeout=args.user_state_timeout)`（从 main 向 YTBot 传参即可）。

**验证**：`test_config.py` 全绿；手工 `ytbot --log-level DEBUG --status` 确认级别生效。

---

### 方案 G：EventBus 取舍 + 启动职责瘦身（P3，1–2 天）

1. **EventBus 做"接入或删除"的明确决策**，不接受继续闲置：
   - 推荐：在 D 的 pipeline 中接入 3 个事件（`DOWNLOAD_STARTED/COMPLETED/FAILED`），让存储后台重试与未来的状态命令成为订阅者；若本轮没有真实订阅方，则**删除 `core/event_bus.py`、`test_event_bus.py` 及根目录 `test_event_bus_quick.py`**，避免架构摆设误导后续开发。
2. **YTBot 与 StartupManager 去重叠**：
   - StartupManager 已负责分阶段启动（836 行）；[cli.py](file:///Users/horsenli/Works/ytbot/ytbot/cli.py) 的 `YTBot.start()` 里又做了一遍服务初始化与通知。把启动通知拼装（`_send_startup_notification`，含中文文案）移到 `services/startup_notification.py`，YTBot 只保留"装配 + 调用 StartupManager + 等待关闭信号"，目标 < 250 行；
   - `cli()` 的 argparse 部分（约 250 行）抽到 `cli_args.py` 或保持原位但与 `YTBot` 类分段隔离。

---

## 四、路线图

```
阶段 0（0.5d）  方案 A 删死代码 ~2400 行
    ↓ 全量测试绿灯
阶段 1（1d）    方案 B 服务工厂 + /status Bug 修复
    ↓
阶段 2（1d）    方案 F 配置/日志统一 + 两个空参数修复
    ↓
阶段 3（3-5d）  方案 C twitter.py 拆包（每步可独立合入）
    ↓
阶段 4（2d）    方案 D 下载流程内核收敛
    ↓
阶段 5（2-3d）  方案 E telegram_handler 拆分
    ↓
阶段 6（1-2d）  方案 G EventBus 取舍 + 启动瘦身
```

**总量估算**：11–15 个工作日。每个阶段独立可发布，任何阶段中断都不会留下半成品（阶段内最后一步永远是"全量测试绿灯"）。

**预期结果**：
- 删除约 2400 行死代码；
- 最大文件从 5726 行降到 < 600 行/包，handler 从 1895 行降到 < 200 行装配器 + 4 个小模块；
- Bot/CLI 下载逻辑单源化；
- 修复 2 个真实功能缺陷（`/status` 永远显示停止、两个 CLI 参数无效）；
- 服务状态全局一致，消除缓存队列/Nextcloud 可用性多份拷贝的隐患。

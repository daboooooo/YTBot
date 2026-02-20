# YTBot 工作流程重构方案

## Why
当前 ytbot 的启动和工作流程逻辑分散在多个模块中，缺乏清晰的生命周期管理和状态流转。需要重构以实现更清晰的工作流程：启动 → 升级 yt-dlp → 连接 Telegram → 尝试连接 Nextcloud → 循环等待消息 → 解析处理 → 存储（Nextcloud 或本地）。

## What Changes
- 重构启动流程，明确各阶段初始化顺序
- 添加 yt-dlp 版本检查和自动升级机制
- 优化 Nextcloud 连接检测和重试逻辑
- 实现消息处理的统一入口和状态机
- 完善 YouTube 链接处理：询问下载音频还是视频
- 完善 Twitter/X 链接处理：抓取标题和完整内容
- 实现存储优先级策略：Nextcloud 优先，本地存储作为后备
- 添加缓存管理：处理 Nextcloud 断连时的本地缓存文件

## Impact
- Affected specs: 启动流程、消息处理、存储服务、平台处理器
- Affected code:
  - `ytbot/cli.py` - 主启动逻辑
  - `ytbot/handlers/telegram_handler.py` - 消息处理
  - `ytbot/services/storage_service.py` - 存储服务
  - `ytbot/platforms/youtube.py` - YouTube 处理器
  - `ytbot/platforms/twitter.py` - Twitter/X 处理器
  - `ytbot/services/download_service.py` - 下载服务

## ADDED Requirements

### Requirement: 启动流程重构
系统 SHALL 按照以下顺序执行启动流程：
1. 加载配置并验证
2. 检查 ffmpeg 是否可用，不可用时提示用户下载安装
3. 检查并升级 yt-dlp 到最新版本
4. 连接 Telegram Bot API
5. 尝试连接 Nextcloud 服务器
6. 初始化本地存储
7. 检查是否有待上传的缓存文件
8. 启动消息监听循环

#### Scenario: 启动成功
- **WHEN** 所有服务初始化成功
- **THEN** 系统进入消息监听状态，等待用户请求

#### Scenario: ffmpeg 不可用
- **WHEN** 系统启动时检测到 ffmpeg 命令不可用
- **THEN** 记录错误日志
- **AND** 提示用户下载安装 ffmpeg
- **AND** 提供下载链接（https://ffmpeg.org/download.html）

#### Scenario: Nextcloud 连接失败
- **WHEN** Nextcloud 服务器不可用
- **THEN** 系统标记 Nextcloud 为不可用，继续使用本地存储运行

### Requirement: yt-dlp 版本管理
系统 SHALL 在启动时检查 yt-dlp 版本，并提供自动升级功能。

#### Scenario: 版本检查
- **WHEN** 系统启动时
- **THEN** 检查当前 yt-dlp 版本与最新版本
- **AND** 如果版本过旧，记录警告日志

#### Scenario: 自动升级
- **WHEN** 配置启用自动升级且检测到新版本
- **THEN** 执行 yt-dlp 升级命令
- **AND** 记录升级结果

### Requirement: YouTube 链接处理流程
系统 SHALL 对 YouTube 链接提供音频/视频下载选项，使用优化的 yt-dlp 参数。

#### Scenario: YouTube 链接识别
- **WHEN** 用户发送 YouTube 链接
- **THEN** 系统识别为 YouTube 内容
- **AND** 使用 `yt-dlp --extractor-args "youtube:player_client=tv_embedded" --list-formats` 获取可用格式列表
- **AND** 获取视频信息（标题、时长、作者等）
- **AND** 询问用户下载音频还是视频

#### Scenario: 用户选择音频
- **WHEN** 用户选择下载音频
- **THEN** 从格式列表中选择最高音质的音频格式
- **AND** 使用 `yt-dlp --extractor-args "youtube:player_client=tv_embedded" -f [best_audio_format]` 下载
- **AND** 转换为 MP3 格式（如需要）
- **AND** 上传到存储服务

#### Scenario: 用户选择视频
- **WHEN** 用户选择下载视频
- **THEN** 从格式列表中选择 1080p 或低于 1080p 的最高画质视频格式
- **AND** 同时选择对应的最佳音频格式
- **AND** 使用 `yt-dlp --extractor-args "youtube:player_client=tv_embedded" -f [video_format]+[audio_format]` 下载视频和音频
- **AND** 使用 ffmpeg 将视频和音频合并为 MP4 格式
- **AND** 上传到存储服务

#### Scenario: 格式选择策略
- **WHEN** 获取到格式列表后
- **THEN** 音频格式选择：优先选择最高比特率的音频格式（如 251 > 140 > 其他）
- **AND** 视频格式选择：优先选择 1080p（如 137），如不可用则选择低于 1080p 的最高画质
- **AND** 记录选中的格式 ID 用于下载

### Requirement: Twitter/X 链接处理流程
系统 SHALL 对 Twitter/X 链接抓取标题和完整内容，并过滤无关内容。

#### Scenario: Twitter/X 链接识别
- **WHEN** 用户发送 Twitter/X 链接
- **THEN** 系统识别为 Twitter/X 内容
- **AND** 抓取推文标题和完整文本内容
- **AND** 如果有媒体内容，提供下载选项

#### Scenario: 内容抓取成功
- **WHEN** 成功抓取推文内容
- **THEN** 返回格式化的标题和内容
- **AND** 过滤掉与内容无关的 items（如 analytics、广告、推荐等）
- **AND** 保存到存储服务（文本保存为 Markdown 文件）

#### Scenario: 长文内容处理
- **WHEN** 推文为长文内容
- **THEN** 自动展开"显示更多"获取完整内容
- **AND** 过滤掉无关元素（analytics、追踪代码、广告模块等）
- **AND** 保留正文内容的格式（加粗、链接、代码块、斜体）

### Requirement: 存储策略优化
系统 SHALL 实现智能存储策略，优先使用 Nextcloud，失败时回退到本地存储。

#### Scenario: Nextcloud 可用
- **WHEN** Nextcloud 服务可用
- **THEN** 文件优先上传到 Nextcloud
- **AND** 返回 Nextcloud 访问链接

#### Scenario: Nextcloud 不可用
- **WHEN** Nextcloud 服务不可用
- **THEN** 文件保存到本地存储
- **AND** 记录到待上传缓存队列
- **AND** 返回本地文件路径

#### Scenario: Nextcloud 恢复连接
- **WHEN** Nextcloud 服务恢复连接
- **THEN** 检查缓存队列中的待上传文件
- **AND** 自动上传缓存文件到 Nextcloud
- **AND** 清理本地缓存文件

### Requirement: 缓存文件管理
系统 SHALL 管理因 Nextcloud 不可用而产生的本地缓存文件。

#### Scenario: 缓存文件记录
- **WHEN** 文件因 Nextcloud 不可用保存到本地
- **THEN** 记录文件信息到缓存队列（文件路径、时间戳、元数据）

#### Scenario: 缓存文件上传
- **WHEN** Nextcloud 恢复连接
- **THEN** 按时间顺序上传缓存文件
- **AND** 上传成功后删除本地缓存记录

## MODIFIED Requirements

### Requirement: 消息处理流程
系统 SHALL 提供统一的消息处理入口，根据链接类型路由到对应的处理器。

**修改内容**：
- 添加用户状态管理，支持多步骤交互（如选择下载类型）
- 添加超时机制，自动清理长时间未响应的用户状态
- 优化错误处理，提供友好的错误提示

## REMOVED Requirements

### Requirement: 无
本次重构不涉及功能删除，仅优化现有流程。

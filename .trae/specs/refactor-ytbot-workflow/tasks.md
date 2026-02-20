# Tasks

## Phase 1: 启动流程重构
- [x] Task 1: 重构启动流程管理器
  - [x] SubTask 1.1: 创建 StartupManager 类，管理启动流程各阶段
  - [x] SubTask 1.2: 实现阶段状态跟踪和日志记录
  - [x] SubTask 1.3: 添加启动失败的回滚和清理逻辑

- [x] Task 2: 实现依赖工具检查
  - [x] SubTask 2.1: 创建 ffmpeg 可用性检查函数
  - [x] SubTask 2.2: ffmpeg 不可用时提示用户下载安装（提供下载链接）
  - [x] SubTask 2.3: 创建 yt-dlp 版本检查函数
  - [x] SubTask 2.4: 实现 yt-dlp 自动升级功能
  - [x] SubTask 2.5: 添加版本检查配置选项

- [x] Task 3: 优化 Nextcloud 连接管理
  - [x] SubTask 3.1: 实现连接状态检测和缓存
  - [x] SubTask 3.2: 添加连接重试机制（指数退避）
  - [x] SubTask 3.3: 实现连接恢复通知

## Phase 2: 消息处理优化
- [x] Task 4: 实现用户状态管理
  - [x] SubTask 4.1: 创建 UserStateManager 类
  - [x] SubTask 4.2: 实现状态超时自动清理
  - [x] SubTask 4.3: 添加状态持久化支持（可选）

- [x] Task 5: 完善 YouTube 处理流程
  - [x] SubTask 5.1: 实现 yt-dlp 格式列表获取功能
    - 使用 `yt-dlp --extractor-args "youtube:player_client=tv_embedded" --list-formats` 获取格式
    - 解析格式列表，提取分辨率、比特率等信息
  - [x] SubTask 5.2: 实现智能格式选择策略
    - 音频格式选择：优先选择最高比特率（如 251 > 140 > 其他）
    - 视频格式选择：优先选择 1080p（如 137），如不可用则选择低于 1080p 的最高画质
  - [x] SubTask 5.3: 修改 TelegramHandler，添加下载类型询问逻辑
  - [x] SubTask 5.4: 实现用户选择处理（音频/视频）
    - 音频下载：使用 `yt-dlp --extractor-args "youtube:player_client=tv_embedded" -f [audio_format]` 下载
    - 视频下载：使用 `yt-dlp --extractor-args "youtube:player_client=tv_embedded" -f [video_format]+[audio_format]` 同时下载视频和音频
    - 视频下载：使用 ffmpeg 自动合并视频和音频为 MP4 格式
  - [x] SubTask 5.5: 优化下载进度反馈

- [x] Task 6: 完善 Twitter/X 处理流程
  - [x] SubTask 6.1: 实现 Twitter/X 内容抓取功能
    - 使用 Playwright 绕过反爬虫保护
    - 自动展开长文内容（点击"显示更多"）
  - [x] SubTask 6.2: 实现内容过滤功能
    - 过滤掉与内容无关的 items（analytics、广告、推荐等）
    - 保留正文内容的格式（加粗、链接、代码块、斜体）
  - [x] SubTask 6.3: 添加文本内容格式化和保存
    - 格式化为 Markdown 格式
    - 保存到存储服务
  - [x] SubTask 6.4: 实现媒体内容下载选项

## Phase 3: 存储策略优化
- [x] Task 7: 实现缓存文件管理
  - [x] SubTask 7.1: 创建 CacheManager 类
  - [x] SubTask 7.2: 实现缓存队列持久化（JSON 文件）
  - [x] SubTask 7.3: 添加缓存文件清理策略

- [x] Task 8: 优化存储服务
  - [x] SubTask 8.1: 修改 StorageService，添加缓存队列支持
  - [x] SubTask 8.2: 实现 Nextcloud 恢复时的自动上传
  - [x] SubTask 8.3: 添加存储状态监控和通知

## Phase 4: 集成和测试
- [x] Task 9: 更新主程序入口
  - [x] SubTask 9.1: 修改 cli.py，集成新的启动流程
  - [x] SubTask 9.2: 添加启动参数和配置选项
  - [x] SubTask 9.3: 优化错误处理和日志输出

- [x] Task 10: 编写测试和文档
  - [x] SubTask 10.1: 为新功能编写单元测试
  - [x] SubTask 10.2: 更新 README 文档
  - [x] SubTask 10.3: 编写重构方案文档到 restruct.md

# Task Dependencies
- [Task 2] depends on [Task 1]
- [Task 3] depends on [Task 1]
- [Task 4] 独立执行
- [Task 5] depends on [Task 4]
- [Task 6] depends on [Task 4]
- [Task 7] 独立执行
- [Task 8] depends on [Task 7]
- [Task 9] depends on [Task 1, Task 2, Task 3, Task 4, Task 5, Task 6, Task 7, Task 8]
- [Task 10] depends on [Task 9]

# Parallel Execution Opportunities
以下任务可以并行执行：
- Task 1, Task 4, Task 7 可以同时开始
- Task 5 和 Task 6 可以并行开发
- Task 2 和 Task 3 可以并行开发

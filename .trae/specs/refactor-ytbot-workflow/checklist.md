# YTBot 工作流程重构检查清单

## Phase 1: 启动流程重构
- [x] StartupManager 类已创建并实现阶段管理
- [x] 启动流程各阶段状态跟踪和日志记录正常工作
- [x] 启动失败的回滚和清理逻辑已实现
- [x] ffmpeg 可用性检查功能已实现
- [x] ffmpeg 不可用时提示用户下载安装（提供下载链接）
- [x] yt-dlp 版本检查功能已实现
- [x] yt-dlp 自动升级功能已实现
- [x] 版本检查配置选项已添加
- [x] Nextcloud 连接状态检测和缓存已实现
- [x] Nextcloud 连接重试机制（指数退避）已实现
- [x] Nextcloud 连接恢复通知已实现

## Phase 2: 消息处理优化
- [x] UserStateManager 类已创建
- [x] 用户状态超时自动清理已实现
- [x] YouTube 链接处理：格式列表获取功能已实现（使用 `--extractor-args "youtube:player_client=tv_embedded" --list-formats`）
- [x] YouTube 链接处理：智能格式选择策略已实现（音频最高比特率，视频优先 1080p）
- [x] YouTube 链接处理：下载类型询问逻辑已实现
- [x] YouTube 链接处理：用户选择处理（音频/视频）已实现
- [x] YouTube 链接处理：视频下载时同时下载视频和音频流
- [x] YouTube 链接处理：使用 ffmpeg 自动合并视频和音频为 MP4 格式
- [x] YouTube 链接处理：下载进度反馈已优化
- [x] Twitter/X 内容抓取功能已实现
- [x] Twitter/X 长文内容自动展开已实现
- [x] Twitter/X 内容过滤功能已实现（过滤 analytics、广告、推荐等）
- [x] Twitter/X 文本内容格式化和保存已实现
- [x] Twitter/X 媒体内容下载选项已实现

## Phase 3: 存储策略优化
- [x] CacheManager 类已创建
- [x] 缓存队列持久化（JSON 文件）已实现
- [x] 缓存文件清理策略已实现
- [x] StorageService 缓存队列支持已添加
- [x] Nextcloud 恢复时的自动上传已实现
- [x] 存储状态监控和通知已添加

## Phase 4: 集成和测试
- [x] cli.py 已更新，集成新的启动流程
- [x] 启动参数和配置选项已添加
- [x] 错误处理和日志输出已优化
- [x] 新功能单元测试已编写
- [x] README 文档已更新
- [x] 重构方案文档已写入 restruct.md

## 功能验证
- [x] 启动流程按正确顺序执行：配置加载 → ffmpeg 检查 → yt-dlp 升级 → Telegram 连接 → Nextcloud 连接 → 本地存储初始化 → 缓存检查 → 消息监听
- [x] ffmpeg 不可用时正确提示用户下载安装
- [x] YouTube 链接处理：正确获取格式列表（使用 `--extractor-args "youtube:player_client=tv_embedded"`）
- [x] YouTube 链接处理：正确询问用户下载音频还是视频
- [x] YouTube 链接处理：音频下载选择最高比特率格式
- [x] YouTube 链接处理：视频下载选择 1080p 或低于 1080p 的最高画质
- [x] YouTube 链接处理：视频下载时正确合并视频和音频为 MP4
- [x] Twitter/X 链接处理：正确抓取标题和完整内容
- [x] Twitter/X 链接处理：正确过滤无关内容（analytics 等）
- [x] Nextcloud 可用时：文件正确上传到 Nextcloud
- [x] Nextcloud 不可用时：文件正确保存到本地并记录到缓存队列
- [x] Nextcloud 恢复连接时：缓存文件自动上传
- [x] 所有单元测试通过
- [x] 集成测试通过

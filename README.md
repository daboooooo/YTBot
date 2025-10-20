# YTBot

YTBot是一个功能强大的Telegram机器人，可以帮助用户下载YouTube视频的音频或视频，并上传到Nextcloud服务器。

## 功能特点

- 监听Telegram消息，自动识别YouTube链接
- 下载YouTube视频并支持音频(MP3)或视频(MP4)格式选择
- 上传到Nextcloud服务器指定目录
- 提供处理进度通知和完成提醒
- 完善的错误处理和自动重试机制
- 支持并发控制，避免资源过载
- 支持代理设置，解决网络访问问题
- 资源监控和自动清理，避免内存泄漏
- 用户状态管理，支持会话跟踪
- 网络连接监控和自动恢复机制
- 优雅关闭和信号处理
- 全局异常捕获和管理员通知
- 详细的日志记录，便于故障排查

## 环境要求

- Python 3.7+
- FFmpeg（用于音频转换）

## 安装步骤

1. 克隆或下载项目代码

2. 安装Python依赖：
```bash
pip install -r requirements.txt
```

3. 安装FFmpeg：
   - **Windows**: 下载FFmpeg安装包并添加到环境变量
   - **macOS**: `brew install ffmpeg`
   - **Linux**: `sudo apt-get install ffmpeg` 或其他对应包管理器

## 配置方法

复制并重命名`config.py`文件，填入您的实际配置：

```python
# Telegram Bot配置
TELEGRAM_BOT_TOKEN = 'YOUR_TELEGRAM_BOT_TOKEN'  # 在@BotFather获取
ADMIN_CHAT_ID = 'YOUR_TELEGRAM_USER_ID'  # 替换为您的Telegram用户ID

# Nextcloud配置
NEXTCLOUD_URL = 'https://your-nextcloud-instance.com'  # Nextcloud服务器地址
NEXTCLOUD_USERNAME = 'your-username'  # Nextcloud用户名
NEXTCLOUD_PASSWORD = 'your-password'  # Nextcloud密码
NEXTCLOUD_UPLOAD_DIR = '/Music/YTBot'  # 上传目录

# 并发控制配置
MAX_CONCURRENT_DOWNLOADS = 5  # 最大并发下载数

# 日志级别配置
LOG_LEVEL = 'INFO'  # 日志级别：DEBUG, INFO, WARNING, ERROR, CRITICAL

# 代理配置（可选）
PROXY_URL = 'http://proxy-server:port'  # 支持HTTP、SOCKS5代理
# 或从环境变量读取代理设置：PROXY_URL, ALL_PROXY, all_proxy
```

## 使用方法

1. 确保已完成所有配置

2. 运行机器人：
```bash
python main.py
```

3. 在Telegram中与机器人交互：
   - 发送`/start`命令开始使用
   - 发送`/help`命令获取帮助
   - 直接发送YouTube链接开始下载
   - 根据机器人提示选择下载类型（音频或视频）
   - 等待处理完成，接收上传结果通知

## 架构说明

YTBot采用模块化异步架构设计：

- **消息接收层**：使用python-telegram-bot库的低级API监听Telegram消息，支持高并发处理
- **处理核心层**：使用yt-dlp下载视频并通过FFmpeg转换，支持音频和视频格式
- **存储层**：通过webdav3客户端将文件上传到Nextcloud，支持连接验证和权限检查
- **通知层**：向用户发送处理状态和结果通知
- **监控层**：包含资源监控、网络监控和用户状态管理模块
- **异常处理层**：全局异常捕获和管理员通知机制
- **信号处理层**：支持优雅关闭和资源释放

## 扩展建议

1. 添加用户配额限制功能
2. 支持更多音视频格式
3. 添加管理命令查看处理队列
4. 实现任务持久化，避免程序重启后丢失任务
5. 添加文件元数据编辑功能

## 注意事项

- 确保您有权利下载和转换所提供的YouTube视频
- 遵守相关法律法规，尊重版权
- 配置文件包含敏感信息，请妥善保管

## 常见问题

**Q: 机器人没有响应？**
A: 检查Telegram Bot Token是否正确，以及机器人是否已启动。同时检查防火墙设置，确保网络连接正常。

**Q: 下载失败？**
A: 检查网络连接和YouTube链接是否有效。如果遇到地区限制，可以尝试配置代理服务器。

**Q: 上传到Nextcloud失败？**
A: 检查Nextcloud服务器地址、用户名、密码是否正确，以及上传目录权限是否足够。查看日志获取详细错误信息。

**Q: 内存占用过高？**
A: YTBot内置了内存监控机制，会在内存使用超过阈值时自动清理。也可以通过降低MAX_CONCURRENT_DOWNLOADS值来减少内存使用。

**Q: 如何配置代理服务器？**
A: 在config.py中设置PROXY_URL参数，支持HTTP和SOCKS5格式。也可以通过环境变量PROXY_URL、ALL_PROXY或all_proxy设置。

**Q: 如何获取我的Telegram用户ID？**
A: 可以在Telegram中向@userinfobot发送消息获取您的用户ID。

**Q: 机器人崩溃后如何处理？**
A: YTBot具有全局异常处理机制，会记录错误并尝试通知管理员。可以检查ytbot.log文件获取详细的错误信息。

**Q: Nextcloud连接时遇到SSL证书错误？**
A: 确保Nextcloud服务器使用的SSL证书是受信任的，或者在配置中添加证书验证选项。

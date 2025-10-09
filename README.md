# YTBot

YTBot是一个Telegram机器人，可以帮助用户下载YouTube视频的音频并转换为MP3格式，然后上传到Nextcloud服务器。

## 功能特点

- 监听Telegram消息，自动识别YouTube链接
- 下载YouTube视频音频并转换为MP3格式
- 上传到Nextcloud服务器指定目录
- 提供处理进度通知
- 完善的错误处理机制
- 支持并发控制

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

1. 复制并重命名`config.py`文件，填入您的实际配置：
```python
# Telegram Bot配置
TELEGRAM_BOT_TOKEN = 'YOUR_TELEGRAM_BOT_TOKEN'  # 在@BotFather获取

# Nextcloud配置
NEXTCLOUD_URL = 'https://your-nextcloud-instance.com'  # Nextcloud服务器地址
NEXTCLOUD_USERNAME = 'your-username'  # Nextcloud用户名
NEXTCLOUD_PASSWORD = 'your-password'  # Nextcloud密码
NEXTCLOUD_UPLOAD_DIR = '/Music/YTBot'  # 上传目录

# 并发控制配置
MAX_CONCURRENT_DOWNLOADS = 5  # 最大并发下载数

# 日志级别配置
LOG_LEVEL = 'INFO'  # 日志级别
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
   - 直接发送YouTube链接开始下载和转换

## 架构说明

YTBot采用分层架构设计：

- **消息接收层**：使用python-telegram-bot库监听Telegram消息
- **处理核心层**：使用yt-dlp下载视频并通过FFmpeg转换为MP3
- **存储层**：通过WebDAV客户端将文件上传到Nextcloud
- **通知层**：向用户发送处理状态和结果通知

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
A: 检查Telegram Bot Token是否正确，以及机器人是否已启动

**Q: 下载失败？**
A: 检查网络连接和YouTube链接是否有效

**Q: 上传到Nextcloud失败？**
A: 检查Nextcloud配置和网络连接
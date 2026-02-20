# YTBot - Multi-Platform Content Download & Management Bot

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Code Style](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

YTBot is a professional Python tool for downloading and managing content from various platforms including YouTube, Twitter/X, and more. It features an extensible architecture that makes it easy to add support for new platforms.

## 🚀 Features

- **🎯 Multi-Platform Support**: YouTube, Twitter/X (extensible for more platforms)
- **💾 Flexible Storage**: Local storage with automatic cleanup + Nextcloud integration
- **🏗️ Professional Architecture**: Modular, extensible design following Python best practices
- **🛡️ Robust Error Handling**: Graceful degradation when services are unavailable
- **📊 Real-time Monitoring**: Health checks and connection monitoring
- **🔧 Admin Controls**: Comprehensive status reporting and management commands
- **🌐 International Support**: Multi-language support (Chinese/English)
- **⚡ Async Processing**: High-performance async/await architecture
- **🔒 Security First**: Secure configuration management and user permissions

## 🆕 What's New in v2.0

### Major Refactoring and New Features

Version 2.0 introduces a complete architectural refactoring with the following major improvements:

#### 🚀 Enhanced Startup Management
- **Phase-based Startup**: New `StartupManager` with 8 distinct startup phases
- **Automatic Dependency Checking**: FFmpeg availability check with installation guidance
- **yt-dlp Auto-Update**: Automatic version checking and updating of yt-dlp
- **Rollback Support**: Automatic cleanup and rollback on startup failures
- **Detailed Logging**: Comprehensive startup progress tracking and reporting

#### 👤 User State Management
- **State Tracking**: New `UserStateManager` for multi-step user interactions
- **Timeout Cleanup**: Automatic cleanup of expired user states (default 5 minutes)
- **State Persistence**: Optional persistence to disk for recovery after restart
- **Thread-Safe Operations**: Safe concurrent access from multiple threads

#### 💾 Advanced Cache Management
- **Persistent Queue**: New `CacheManager` for managing failed uploads
- **Automatic Retry**: Files are cached when Nextcloud is unavailable and retried later
- **Storage Statistics**: Detailed cache statistics and management
- **Cleanup Policies**: Automatic cleanup of missing files and old cache entries

#### 🎬 Enhanced YouTube Processing
- **Smart Format Selection**: Intelligent audio/video format selection
  - Audio: Prioritizes Opus 160kbps (251) > M4A 128kbps (140) > highest bitrate
  - Video: Prioritizes 1080p (137) or highest quality below 1080p
- **User Choice Flow**: Interactive selection between audio and video download
- **Improved Progress Feedback**: Real-time download progress updates
- **Better Error Handling**: Graceful handling of format unavailability

#### 🐦 Complete Twitter/X Integration
- **Playwright-based Scraping**: Bypass anti-bot protection with headless browser
- **Long Tweet Support**: Automatic expansion of "Show more" content
- **Content Filtering**: Removes analytics, ads, and recommendations
- **Markdown Formatting**: Preserves formatting (bold, links, code blocks, italic)
- **Media Download**: Support for downloading images from tweets
- **Storage Integration**: Automatic saving to Nextcloud or local storage

#### 🏗️ Architecture Improvements
- **Modular Design**: Clear separation of concerns with dedicated managers
- **Async-First**: Full async/await support throughout the codebase
- **Error Recovery**: Comprehensive error handling and recovery mechanisms
- **Test Coverage**: Extensive unit tests for all new components

## 📦 Installation

### Quick Start
```bash
# Clone the repository
git clone https://github.com/yourusername/ytbot.git
cd ytbot

# Install dependencies
pip install -r requirements.txt

# Install in development mode
pip install -e .
```

### Production Installation
```bash
# Install from PyPI (when published)
pip install ytbot

# Or install from source
pip install -e .
```

### Development Installation
```bash
# Install with development dependencies
pip install -e .[dev]

# Install pre-commit hooks
pip install pre-commit
pre-commit install
```

## ⚙️ Configuration

### Environment Variables

Create a `.env` file in the project root:

```env
# Telegram Bot Configuration (Required)
TELEGRAM_BOT_TOKEN=your_bot_token_here
ADMIN_CHAT_ID=your_admin_chat_id

# Nextcloud Configuration (Optional)
NEXTCLOUD_URL=http://your-nextcloud-server.com
NEXTCLOUD_USERNAME=your_username
NEXTCLOUD_PASSWORD=your_password
NEXTCLOUD_UPLOAD_DIR=/YTBot

# Local Storage Configuration
LOCAL_STORAGE_PATH=./downloads
LOCAL_STORAGE_ENABLED=true
LOCAL_STORAGE_MAX_SIZE_MB=10240
LOCAL_STORAGE_CLEANUP_AFTER_DAYS=7

# System Configuration
MAX_CONCURRENT_DOWNLOADS=5
LOG_LEVEL=INFO

# Download Configuration
DOWNLOAD_TIMEOUT=3600
MAX_RETRY_COUNT=3
INITIAL_RETRY_DELAY=1.0

# Monitoring Configuration
MONITOR_INTERVAL=3600
MIN_DISK_SPACE=1024
MAX_CPU_LOAD=0.8
MEMORY_THRESHOLD=512

# User State Configuration (New in v2.0)
USER_STATE_TIMEOUT=300
USER_STATE_PERSISTENCE_FILE=./data/user_states.json
USER_STATE_CLEANUP_INTERVAL=60

# Cache Configuration (New in v2.0)
CACHE_DIR=./downloads
CACHE_QUEUE_FILE=./downloads/cache_queue.json

# Download Configuration (Enhanced in v2.0)
CHECK_YT_DLP_VERSION=true
YT_DLP_VERSION_CHECK_TIMEOUT=10
VIDEO_FORMAT=bestvideo+bestaudio/best
AUDIO_FORMAT=bestaudio/best
AUDIO_CODEC=mp3
AUDIO_QUALITY=192
```

### New Configuration Options (v2.0)

#### User State Management
- `USER_STATE_TIMEOUT`: Timeout for user interaction states in seconds (default: 300)
- `USER_STATE_PERSISTENCE_FILE`: Optional file path for state persistence
- `USER_STATE_CLEANUP_INTERVAL`: Interval for cleanup thread in seconds (default: 60)

#### Cache Management
- `CACHE_DIR`: Directory for cached files (defaults to local storage path)
- `CACHE_QUEUE_FILE`: JSON file for cache queue persistence

#### Enhanced Download Options
- `CHECK_YT_DLP_VERSION`: Enable automatic yt-dlp version checking (default: true)
- `YT_DLP_VERSION_CHECK_TIMEOUT`: Timeout for version check requests in seconds
- `VIDEO_FORMAT`: Default video format selection strategy
- `AUDIO_FORMAT`: Default audio format selection strategy
- `AUDIO_CODEC`: Preferred audio codec for conversion (default: mp3)
- `AUDIO_QUALITY`: Audio quality for conversion (default: 192)

### Configuration Validation
```bash
# Validate configuration
ytbot --status

# Test with debug logging
ytbot --log-level DEBUG
```

## 🎯 Usage

### Basic Usage
```bash
# Start the bot
ytbot

# Run with custom config file
ytbot --config /path/to/.env

# Run with debug logging
ytbot --log-level DEBUG

# Check bot status
ytbot --status

# Show version
ytbot --version
```

### Telegram Commands

Send these commands to your bot:

| Command | Description | Permission |
|---------|-------------|------------|
| `/start` | Start using the bot | All users |
| `/help` | Show help information | All users |
| `/status` | Show system status | All users |
| `/storage` | Show storage status | Admin only |
| `/cancel` | Cancel active downloads | All users |

### Sending URLs

Simply send a supported URL to the bot:

- **YouTube**: `https://www.youtube.com/watch?v=VIDEO_ID`
- **YouTube (Short)**: `https://youtu.be/VIDEO_ID`
- **Twitter/X**: `https://twitter.com/username/status/TWEET_ID`
- **Twitter/X**: `https://x.com/username/status/TWEET_ID`

The bot will automatically detect the platform and offer download options.

### Enhanced User Interaction (v2.0)

#### YouTube Download Flow
1. Send a YouTube URL to the bot
2. Bot asks: "Download audio or video?"
3. Select your preference (audio/video)
4. Bot downloads using optimal format:
   - **Audio**: Opus 160kbps (251) or M4A 128kbps (140)
   - **Video**: 1080p MP4 (137) or best available quality
5. File is uploaded to Nextcloud or saved locally

#### Twitter/X Content Extraction
1. Send a Twitter/X URL to the bot
2. Bot automatically:
   - Expands long tweets ("Show more")
   - Filters out analytics and ads
   - Preserves formatting (bold, links, code)
   - Extracts images if present
3. Content is saved as Markdown file
4. File is uploaded to Nextcloud or saved locally

### Startup Process (v2.0)

The bot now goes through 8 startup phases:

1. **Configuration Validation** - Validates all config settings
2. **FFmpeg Check** - Verifies FFmpeg is installed
3. **yt-dlp Update** - Checks and updates yt-dlp if needed
4. **Telegram Connection** - Connects to Telegram Bot API
5. **Nextcloud Connection** - Connects to Nextcloud (if configured)
6. **Local Storage Init** - Initializes local storage directories
7. **Cache Check** - Checks for pending cached files
8. **Message Listener** - Prepares message polling

If any phase fails, the bot performs automatic rollback and cleanup.

## 🏗️ Architecture

YTBot follows a modular, extensible architecture:

```
ytbot/
├── ytbot/                    # Main package
│   ├── core/                 # Core functionality
│   │   ├── config.py        # Configuration management
│   │   ├── logger.py        # Logging utilities
│   │   ├── enhanced_logger.py # Enhanced logging with context
│   │   ├── startup_manager.py # Startup sequence manager
│   │   └── user_state.py    # User state management
│   ├── platforms/            # Platform handlers
│   │   ├── base.py          # Base platform handler
│   │   ├── youtube.py       # YouTube handler
│   │   └── twitter.py       # Twitter/X handler
│   ├── services/             # Business logic services
│   │   ├── telegram_service.py  # Telegram bot service
│   │   ├── storage_service.py   # Unified storage service
│   │   └── download_service.py  # Download coordination
│   ├── handlers/             # Command handlers
│   │   └── telegram_handler.py  # Telegram command handlers
│   ├── storage/              # Storage backends
│   │   ├── local_storage.py     # Local file storage
│   │   ├── nextcloud_storage.py # Nextcloud integration
│   │   └── cache_manager.py     # Cache file manager
│   ├── monitoring/           # System monitoring
│   │   ├── health_monitor.py    # Health checks
│   │   └── connection_monitor.py # Connection monitoring
│   ├── utils/                # Utility functions
│   │   └── common.py        # Common utilities
│   ├── x_content_extractor/ # X/Twitter content extraction
│   │   ├── x_content_scraper.js # Playwright scraper
│   │   └── x_to_notion_with_real_content.py
│   └── cli.py               # Command-line interface
├── tests/                    # Test suite
│   ├── unit/                # Unit tests
│   │   ├── test_cache_manager.py
│   │   ├── test_common.py
│   │   ├── test_config.py
│   │   ├── test_local_storage.py
│   │   ├── test_logger.py
│   │   ├── test_monitoring.py
│   │   ├── test_platform_base.py
│   │   ├── test_startup_manager.py
│   │   ├── test_storage_service.py
│   │   ├── test_telegram_service.py
│   │   ├── test_twitter_handler.py
│   │   ├── test_user_state_manager.py
│   │   ├── test_youtube.py
│   │   └── test_youtube_handler.py
│   └── integration/         # Integration tests
│       └── test_integration.py
├── setup.py                 # Package installation
├── requirements.txt         # Dependencies
├── README.md               # Documentation
└── CHANGELOG.md            # Change history
```

## 🔧 Adding New Platforms

YTBot's extensible architecture makes it easy to add support for new platforms:

### 1. Create Platform Handler

```python
# ytbot/platforms/new_platform.py
from ytbot.platforms.base import PlatformHandler, ContentInfo, ContentType, DownloadResult

class NewPlatformHandler(PlatformHandler):
    def __init__(self):
        super().__init__("NewPlatform")
        self.supported_content_types = [ContentType.VIDEO, ContentType.AUDIO]

    def can_handle(self, url: str) -> bool:
        """Check if URL is from this platform"""
        return "newplatform.com" in url

    async def get_content_info(self, url: str) -> Optional[ContentInfo]:
        """Get content information"""
        # Implementation here
        pass

    async def download_content(self, url: str, content_type: ContentType, progress_callback=None) -> DownloadResult:
        """Download content"""
        # Implementation here
        pass
```

### 2. Register the Handler

The handler is automatically registered when the download service initializes. No manual registration needed!

### 3. Test the Integration

```bash
# Test directly
python -c "
from ytbot.services.download_service import DownloadService
service = DownloadService()
print(service.get_supported_platforms())
"
```

## 📊 Storage Options

### Local Storage
- **Automatic Organization**: Files organized by date
- **Space Management**: Configurable quotas and cleanup
- **Retention Policies**: Automatic cleanup of old files
- **Health Monitoring**: Disk space alerts and management

### Nextcloud Storage
- **WebDAV Integration**: Direct upload to Nextcloud
- **Automatic Retry**: Failed uploads are retried automatically
- **Directory Management**: Automatic folder creation
- **Fallback Support**: Graceful fallback to local storage

### Storage Strategy
1. **Nextcloud First**: Try to upload to Nextcloud if available
2. **Local Fallback**: Use local storage if Nextcloud fails
3. **User Notification**: Clear messaging about where files are stored
4. **Admin Monitoring**: Storage status reports and cleanup notifications

## 🔍 Monitoring & Health

### Health Monitoring
- **System Resources**: CPU, memory, disk usage monitoring
- **Automatic Alerts**: Configurable thresholds and notifications
- **Performance Metrics**: Download success rates and timing
- **Resource Management**: Automatic cleanup and optimization

### Connection Monitoring
- **Real-time Checks**: Continuous service availability monitoring
- **Automatic Recovery**: Reconnection attempts and failover
- **Status Reporting**: Detailed connection status for all services
- **Admin Dashboard**: Comprehensive system overview

### Admin Commands
- `/storage` - Detailed storage status and usage
- `/status` - Overall system health and performance
- Automatic reports and notifications

## 🛡️ Error Handling

### Graceful Degradation
- **Service Failures**: Continued operation when services are unavailable
- **Network Issues**: Automatic retry with exponential backoff
- **Storage Failures**: Seamless fallback between storage backends
- **User Experience**: Clear error messages and guidance

### Retry Logic
- **Exponential Backoff**: Smart retry with increasing delays
- **Configurable Attempts**: Customizable retry limits
- **Failure Detection**: Intelligent failure pattern recognition
- **Circuit Breakers**: Prevent cascading failures

### Logging & Debugging
- **Structured Logging**: JSON-formatted logs with context
- **Debug Mode**: Detailed logging for troubleshooting
- **Log Rotation**: Automatic log file management
- **Performance Tracking**: Request timing and performance metrics

## 🧪 Development

### Setting Up Development Environment
```bash
# Clone repository
git clone https://github.com/yourusername/ytbot.git
cd ytbot

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows

# Install development dependencies
pip install -e .[dev]

# Install pre-commit hooks
pre-commit install
```

### Running Tests
```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=ytbot --cov-report=html

# Run specific test file
pytest tests/unit/test_local_storage.py

# Run with verbose output
pytest -v
```

### Code Quality
```bash
# Format code
black ytbot/

# Lint code
flake8 ytbot/

# Type checking
mypy ytbot/

# Security checks
bandit -r ytbot/
```

## 🤝 Contributing

### Quick Contribution Guide

1. **Fork the repository**
2. **Create a feature branch**: `git checkout -b feature/amazing-feature`
3. **Make your changes** and add tests
4. **Run tests and quality checks**:
   ```bash
   pytest
   black ytbot/
   flake8 ytbot/
   mypy ytbot/
   ```
5. **Commit your changes**: `git commit -m 'Add amazing feature'`
6. **Push to the branch**: `git push origin feature/amazing-feature`
7. **Open a Pull Request**

### Code Style
- Follow [PEP 8](https://pep8.org/) style guidelines
- Use [Black](https://black.readthedocs.io/) for code formatting
- Add type hints where possible
- Write comprehensive docstrings
- Include unit tests for new features

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.


## 📞 Support

### Getting Help
- 🐛 **Bug Reports**: [GitHub Issues](https://github.com/yourusername/ytbot/issues)
- 💬 **Discussions**: [GitHub Discussions](https://github.com/yourusername/ytbot/discussions)
- 📧 **Email**: ytbot@example.com
- 📚 **Documentation**: [Wiki](https://github.com/yourusername/ytbot/wiki)

### Common Issues

#### Bot won't start
1. Check your `.env` file configuration
2. Verify Telegram bot token is valid
3. Check log files for error messages
4. Run with `--log-level DEBUG` for detailed output

#### Downloads failing
1. Check internet connectivity
2. Verify the URL is accessible
3. Check storage space availability
4. Review download timeout settings

#### Nextcloud upload issues
1. Verify Nextcloud credentials
2. Check Nextcloud server availability
3. Review upload directory permissions
4. Check file size limits

## 🗺️ Roadmap

### Version 2.1 (Coming Soon)
- [ ] Web interface for administration
- [ ] Support for Instagram Reels
- [ ] Advanced download scheduling
- [ ] Plugin system for custom platforms

### Version 2.2 (Planned)
- [ ] Docker containerization
- [ ] Kubernetes deployment support
- [ ] Advanced analytics and reporting
- [ ] Multi-language support improvements

### Version 3.0 (Future)
- [ ] REST API for external integrations
- [ ] Advanced user management
- [ ] Enterprise features
- [ ] Cloud deployment options

---

**⭐ If you find this project useful, please give it a star on GitHub!**
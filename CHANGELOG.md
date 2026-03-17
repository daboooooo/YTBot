# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.5.0] - 2026-03-17

### Changed
- **Twitter/X download performance optimization**
  - Use shared browser instance to avoid browser startup overhead (~3-10s saved per download)
  - Parallel image download with concurrency limit (3 concurrent connections) - avoids rate limiting
  - Async yt-dlp execution using thread pool to avoid blocking event loop
  - Added cookie refresh mechanism: automatically retry with new browser on login failure
  - Graceful fallback to standalone browser if shared browser fails

### Added
- Login retry mechanism for Twitter/X - automatically retries with new browser instance on authentication failure

### Fixed
- Improved error handling for image download failures

## [2.0.0] - 2024-02-06

### Added
- Initial release with modular architecture
- YouTube platform support with video/audio download
- Twitter/X platform handler (example implementation)
- Local storage with automatic cleanup
- Nextcloud integration with WebDAV
- Telegram bot with multi-language support
- Comprehensive monitoring and health checks
- Professional CLI interface
- Extensive documentation

### Technical Details
- Python 3.8+ support
- Async/await architecture for high performance
- Type hints throughout the codebase
- Comprehensive error handling
- Configurable retry logic
- Graceful degradation when services are unavailable

## [1.x.x] - Previous Versions

### Note
Previous versions (1.x.x) used a flat file structure and are not compatible with the current modular architecture. Users upgrading from 1.x.x should follow the migration guide in the documentation.

---

## Release Notes Format

When adding new entries, use the following format:

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added
- New features

### Changed
- Changes in existing functionality

### Deprecated
- Soon-to-be removed features

### Removed
- Now removed features

### Fixed
- Bug fixes

### Security
- Security improvements
```

## Release Process

1. Update version in `setup.py`
2. Update CHANGELOG.md with new entries
3. Create release on GitHub
4. Tag the release with version number
5. Publish to PyPI (if applicable)
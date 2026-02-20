# Contributing to YTBot

Thank you for your interest in contributing to YTBot! This document provides guidelines and instructions for contributing to the project.

## 🚀 Getting Started

### Prerequisites

- Python 3.8 or higher
- Git
- Virtual environment (recommended)

### Setting Up Development Environment

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/yourusername/ytbot.git
   cd ytbot
   ```

3. **Create a virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/Mac
   # or
   venv\Scripts\activate  # Windows
   ```

4. **Install development dependencies**:
   ```bash
   pip install -e .[dev]
   ```

5. **Install pre-commit hooks** (optional but recommended):
   ```bash
   pip install pre-commit
   pre-commit install
   ```

## 📋 Development Workflow

### 1. Create a Feature Branch

```bash
git checkout -b feature/your-feature-name
# or for bug fixes
git checkout -b fix/issue-description
```

### 2. Make Your Changes

- Follow the coding standards (see below)
- Add tests for new functionality
- Update documentation as needed
- Ensure all tests pass

### 3. Test Your Changes

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=ytbot --cov-report=html

# Run specific test file
pytest tests/test_local_storage.py

# Run code quality checks
black ytbot/
flake8 ytbot/
mypy ytbot/
```

### 4. Commit Your Changes

```bash
git add .
git commit -m "feat: add your feature description"
```

Follow [Conventional Commits](https://www.conventionalcommits.org/) specification:
- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation changes
- `style:` - Code style changes
- `refactor:` - Code refactoring
- `test:` - Test additions or changes
- `chore:` - Maintenance tasks

### 5. Push and Create Pull Request

```bash
git push origin feature/your-feature-name
```

Then create a pull request on GitHub.

## 📝 Coding Standards

### Code Style

- Follow [PEP 8](https://pep8.org/) style guidelines
- Use [Black](https://black.readthedocs.io/) for code formatting
- Maximum line length: 100 characters
- Use type hints where possible

### Documentation

- Write comprehensive docstrings for all functions and classes
- Use Google-style docstrings:
  ```python
  def function_name(param1: str, param2: int) -> bool:
      """Brief description of the function.

      Args:
          param1: Description of param1.
          param2: Description of param2.

      Returns:
          Description of return value.

      Raises:
          ValueError: When something goes wrong.
      """
  ```

### Error Handling

- Use specific exception types
- Provide meaningful error messages
- Log errors appropriately
- Handle graceful degradation

### Testing

- Write unit tests for new functionality
- Aim for >80% code coverage
- Use pytest for testing
- Mock external dependencies
- Test both success and failure cases

## 🧪 Testing Guidelines

### Test Structure

```python
# tests/test_module.py
import pytest
from ytbot.module import function_to_test

class TestFunctionName:
    def test_success_case(self):
        """Test successful execution."""
        result = function_to_test(valid_input)
        assert result == expected_output

    def test_failure_case(self):
        """Test error handling."""
        with pytest.raises(ValueError):
            function_to_test(invalid_input)

    @pytest.mark.asyncio
    async def test_async_function(self):
        """Test async functions."""
        result = await async_function_to_test(input)
        assert result == expected_output
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=ytbot --cov-report=html

# Run specific test file
pytest tests/test_local_storage.py

# Run specific test
pytest tests/test_local_storage.py::TestLocalStorage::test_save_file

# Run with verbose output
pytest -v
```

## 🏗️ Architecture Guidelines

### Adding New Platforms

When adding support for a new platform:

1. **Create a new handler** in `ytbot/platforms/`:
   ```python
   # ytbot/platforms/new_platform.py
   from ytbot.platforms.base import PlatformHandler, ContentInfo, ContentType, DownloadResult

   class NewPlatformHandler(PlatformHandler):
       def __init__(self):
           super().__init__("NewPlatform")
           self.supported_content_types = [ContentType.VIDEO, ContentType.AUDIO]

       def can_handle(self, url: str) -> bool:
           """Check if URL is from this platform."""
           return "newplatform.com" in url

       async def get_content_info(self, url: str) -> Optional[ContentInfo]:
           """Get content information."""
           # Implementation
           pass

       async def download_content(self, url: str, content_type: ContentType, progress_callback=None) -> DownloadResult:
           """Download content."""
           # Implementation
           pass
   ```

2. **Add tests** in `tests/test_new_platform.py`
3. **Update documentation** in README.md
4. **Test integration** with the demo script

### Adding New Services

When adding new services:

1. Follow the service pattern in `ytbot/services/`
2. Implement proper dependency injection
3. Add comprehensive error handling
4. Write unit tests
5. Update service documentation

## 🐛 Bug Reports

When reporting bugs, please include:

- **Bug description**: Clear description of the issue
- **Steps to reproduce**: How to reproduce the bug
- **Expected behavior**: What should happen
- **Actual behavior**: What actually happens
- **Environment**: Python version, OS, dependencies
- **Logs**: Relevant log messages
- **Configuration**: Relevant config settings (sanitize sensitive data)

Use the bug report template when creating issues.

## 💡 Feature Requests

When suggesting new features:

- **Use case**: Describe the problem you're trying to solve
- **Proposed solution**: How you think it should work
- **Alternatives**: Other solutions you've considered
- **Impact**: How this affects existing functionality

Use the feature request template when creating issues.

## 📚 Documentation

### Code Documentation

- Update docstrings when modifying functions
- Add examples for complex functionality
- Keep README.md up to date
- Add configuration examples

### User Documentation

- Write clear, concise instructions
- Include screenshots for UI changes
- Provide troubleshooting guides
- Keep language simple and accessible

## 🔒 Security

### Security Guidelines

- Never commit sensitive data (tokens, passwords, API keys)
- Use environment variables for configuration
- Validate all user inputs
- Follow secure coding practices
- Report security issues privately

### Configuration Security

```python
# Good - Use environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Bad - Never hardcode sensitive data
TELEGRAM_BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
```

## 🚀 Release Process

### Version Numbering

We follow [Semantic Versioning](https://semver.org/):
- **MAJOR**: Incompatible API changes
- **MINOR**: Backwards-compatible functionality additions
- **PATCH**: Backwards-compatible bug fixes

### Release Checklist

1. Update version in `setup.py`
2. Update CHANGELOG.md
3. Run full test suite
4. Update documentation
5. Create release on GitHub
6. Publish to PyPI (if applicable)

## 🤝 Code of Conduct

- Be respectful and inclusive
- Welcome newcomers
- Focus on constructive feedback
- Help maintain a positive environment

## 📞 Getting Help

- **GitHub Issues**: For bug reports and feature requests
- **GitHub Discussions**: For questions and discussions
- **Email**: ytbot@example.com for security issues

Thank you for contributing to YTBot! 🎉
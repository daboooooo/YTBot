# 快速应用指南：PDF 存储支持

## 只需修改一个文件

**文件**：`ytbot/storage/local_storage.py`

**方法**：`_save_directory`（约 line 141）

## 应用步骤

### 1. 打开文件

```bash
code ytbot/storage/local_storage.py
```

### 2. 找到方法

在文件中搜索 `_save_directory`，会找到约 line 141 的方法。

### 3. 替换方法

用以下完整代码替换整个 `_save_directory` 方法：

```python
def _save_directory(self, source_dir: Path, filename: str) -> Optional[str]:
    """Save a directory (with HTML, PDF, images, videos) to local storage"""
    try:
        html_files = list(source_dir.glob("*.html"))
        if not html_files:
            logger.error(f"No HTML file found in directory: {source_dir}")
            return None

        html_file = html_files[0]

        pdf_files = list(source_dir.glob("*.pdf"))
        pdf_file = pdf_files[0] if pdf_files else None

        date_folder = datetime.now().strftime("%Y-%m")
        target_dir = self.storage_path / date_folder
        target_dir.mkdir(exist_ok=True)

        name, ext = os.path.splitext(filename)
        if not ext:
            ext = ".html"
            filename = name + ext

        target_path = target_dir / filename

        if target_path.exists():
            timestamp = datetime.now().strftime("%H%M%S")
            target_path = target_dir / f"{name}_{timestamp}{ext}"

        content_dir = target_path.parent / target_path.stem
        content_dir.mkdir(exist_ok=True)

        html_target = content_dir / target_path.name
        shutil.copy2(html_file, html_target)

        target_path = html_target

        has_pdf = False
        if pdf_file:
            pdf_target = content_dir / pdf_file.name

            if pdf_target.exists():
                timestamp = datetime.now().strftime("%H%M%S")
                name_without_ext = pdf_file.stem
                pdf_target = content_dir / f"{name_without_ext}_{timestamp}.pdf"

            try:
                shutil.copy2(pdf_file, pdf_target)
                has_pdf = True
                logger.info(f"PDF file copied: {pdf_target}")
            except Exception as e:
                logger.warning(f"Failed to copy PDF file: {e}")

        images_source = source_dir / "images"
        if images_source.exists():
            images_target = content_dir / "images"
            images_target.mkdir(exist_ok=True)

            for img_file in images_source.iterdir():
                if img_file.is_file():
                    shutil.copy2(img_file, images_target / img_file.name)

        videos_source = source_dir / "videos"
        if videos_source.exists():
            videos_target = content_dir / "videos"
            videos_target.mkdir(exist_ok=True)

            for video_file in videos_source.iterdir():
                if video_file.is_file():
                    shutil.copy2(video_file, videos_target / video_file.name)

        has_images = images_source.exists()
        has_videos = videos_source.exists()

        if has_images or has_videos or has_pdf:
            logger.info(
                f"Directory saved to local storage: {target_path} "
                f"(images: {has_images}, videos: {has_videos}, pdf: {has_pdf})"
            )
        else:
            logger.info(f"File saved to local storage: {target_path}")

        return str(target_path)

    except Exception as e:
        logger.error(f"Failed to save directory: {e}")
        return None
```

### 4. 保存文件

按 `Ctrl+S` 保存。

### 5. 验证修改

检查日志中是否包含 `pdf:` 字段：

```bash
grep "pdf:" ytbot.log
```

应该看到：
```
Directory saved to local storage: .../tweet_123.html (images: True, videos: True, pdf: True)
```

## 需要修改的其他文件

**无！**

Nextcloud 的 `upload_directory` 方法已经会遍历并上传目录中的所有文件，不需要修改。

## 验证清单

- [ ] PDF 文件在本地存储目录中
- [ ] PDF 文件名与 HTML 文件名一致
- [ ] Nextcloud 上传包含 PDF 文件
- [ ] 日志显示 `pdf: True`

## 测试命令

```bash
cd /Users/horsenli/Works/ytbot
python -c "
from ytbot.storage.local_storage import LocalStorageManager
import tempfile
import os

storage = LocalStorageManager()

# Create test directory
test_dir = tempfile.mkdtemp()
html_file = os.path.join(test_dir, 'test.html')
pdf_file = os.path.join(test_dir, 'test.pdf')

with open(html_file, 'w') as f:
    f.write('<html><body>Test</body></html>')

with open(pdf_file, 'w') as f:
    f.write('Fake PDF content')

result = storage.save_file_locally(test_dir, 'test.html')
print(f'Result: {result}')

# Check if PDF was copied
result_path = result.replace('.html', '')
if os.path.exists(result_path):
    files = os.listdir(result_path)
    print(f'Files in directory: {files}')
    print(f'PDF copied: {\"test.pdf\" in files}')
"
```

## 预期输出

```
Result: /path/to/storage/2026-05/test/test.html
Files in directory: ['test.html', 'test.pdf', 'images', 'videos']
PDF copied: True
```

## 完整流程

```
1. 下载推文内容
   ↓
2. TwitterHandler 生成 HTML + PDF
   ├── tweet_123.html
   ├── tweet_123.pdf  ← 新增
   ├── images/
   └── videos/
   ↓
3. 本地存储（local_storage.py）
   └── 复制所有文件到 storage/日期/目录/
       ├── tweet_123.html  ✓
       ├── tweet_123.pdf    ✓ 新增
       ├── images/          ✓
       └── videos/          ✓
   ↓
4. Nextcloud 上传（已自动支持）
   └── 上传目录中的所有文件
       ├── tweet_123.html  ✓
       ├── tweet_123.pdf    ✓
       ├── images/          ✓
       └── videos/          ✓
```

## 常见问题

### Q: 为什么 Nextcloud 不需要修改？

A: 因为 `upload_directory` 方法使用 `os.walk()` 遍历目录中的所有文件并上传，包括 PDF。

### Q: PDF 文件名会和 HTML 不一样吗？

A: 不会。代码使用 `pdf_file.name` 复制，保持原文件名。

### Q: 如果 PDF 复制失败会怎样？

A: 只会记录警告日志，不影响 HTML、images、videos 的复制。

### Q: 需要重启服务吗？

A: 是的，修改 Python 代码后需要重启服务。

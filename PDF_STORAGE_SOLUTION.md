# PDF 文件存储和上传解决方案

## 问题分析

### 当前流程

```
下载内容 → 生成 HTML + PDF（在临时目录）
                    ↓
              本地存储
                    ↓
              Nextcloud 上传
```

### 问题所在

**1. 本地存储（local_storage.py）**

当前代码只复制：
- ✅ HTML 文件
- ✅ images/ 目录
- ✅ videos/ 目录
- ❌ **PDF 文件丢失！**

**2. Nextcloud 上传**

当前代码会遍历并上传目录中的所有文件，但依赖源目录中有 PDF 文件。由于本地存储时 PDF 没有被复制，Nextcloud 也上传不了。

---

## 解决方案

### 修改 `local_storage.py`

在 `_save_directory` 方法中添加 PDF 文件处理：

```python
def _save_directory(self, source_dir: Path, filename: str) -> Optional[str]:
    """Save a directory (with HTML, PDF, images, videos) to local storage"""
    try:
        html_files = list(source_dir.glob("*.html"))
        if not html_files:
            logger.error(f"No HTML file found in directory: {source_dir}")
            return None

        html_file = html_files[0]

        # ↓ NEW: Look for corresponding PDF file
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

        # ↓ NEW: Copy PDF file (if exists)
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

        # Copy images
        images_source = source_dir / "images"
        if images_source.exists():
            images_target = content_dir / "images"
            images_target.mkdir(exist_ok=True)

            for img_file in images_source.iterdir():
                if img_file.is_file():
                    shutil.copy2(img_file, images_target / img_file.name)

        # Copy videos
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

---

## 完整文件结构

### 修改前

```
源目录（TwitterHandler 生成）:
/tmp/tweet_abc123/
├── tweet_abc123.html
├── tweet_abc123.pdf    ← PDF 在这里
├── images/
│   └── *.jpg
└── videos/
    └── *.mp4

本地存储（修改前）:
/path/to/storage/
└── 2026-05/
    └── tweet_abc123/
        ├── tweet_abc123.html   ← HTML ✓
        ├── images/             ← images ✓
        └── videos/             ← videos ✓
        ❌ PDF 文件丢失！

Nextcloud（修改前）:
/remote/path/Media/Tweet/tweet_abc123/
├── tweet_abc123.html   ← HTML ✓
├── images/             ← images ✓
└── videos/             ← videos ✓
❌ PDF 文件丢失！
```

### 修改后

```
源目录（TwitterHandler 生成）:
/tmp/tweet_abc123/
├── tweet_abc123.html
├── tweet_abc123.pdf    ← PDF 在这里
├── images/
│   └── *.jpg
└── videos/
    └── *.mp4

本地存储（修改后）:
/path/to/storage/
└── 2026-05/
    └── tweet_abc123/
        ├── tweet_abc123.html   ← HTML ✓
        ├── tweet_abc123.pdf    ← PDF ✓ 现在复制了！
        ├── images/             ← images ✓
        └── videos/             ← videos ✓

Nextcloud（修改后）:
/remote/path/Media/Tweet/tweet_abc123/
├── tweet_abc123.html   ← HTML ✓
├── tweet_abc123.pdf    ← PDF ✓ 现在上传了！
├── images/             ← images ✓
└── videos/              ← videos ✓
```

---

## 集成步骤

### 1. 修改 `local_storage.py`

打开 `ytbot/storage/local_storage.py`，找到 `_save_directory` 方法（约 line 141），用上面的完整实现替换。

### 2. 关键改动点

| 改动点 | 说明 |
|--------|------|
| Line: 查找 PDF 文件 | `pdf_files = list(source_dir.glob("*.pdf"))` |
| Line: 复制 PDF | `shutil.copy2(pdf_file, pdf_target)` |
| Line: 更新日志 | 添加 `pdf: {has_pdf}` 到日志输出 |

### 3. 测试验证

```python
# 下载包含 PDF 生成的内容
result = await download_content(url)

# 验证本地存储
local_path = result.file_path  # 本地存储路径
html_files = list(Path(local_path).glob("*.html"))
pdf_files = list(Path(local_path).glob("*.pdf"))

print(f"HTML 文件: {html_files}")
print(f"PDF 文件: {pdf_files}")

# 验证 Nextcloud 上传
# 登录 Nextcloud，检查相同目录是否有 HTML 和 PDF 两个文件
```

---

## Nextcloud 上传逻辑说明

Nextcloud 的 `upload_directory` 方法已经会遍历并上传目录中的**所有文件**，包括：

- `*.html`
- `*.pdf` ← 会被自动上传
- `images/*`
- `videos/*`
- 其他文件

**不需要修改 Nextcloud 相关代码！** 只需要确保 PDF 文件被复制到本地存储目录中。

---

## 日志输出

修改后，日志会显示：

```
Directory saved to local storage: /path/to/storage/2026-05/tweet_abc123/tweet_abc123.html 
(images: True, videos: True, pdf: True)
```

表示：
- ✅ images 目录已保存
- ✅ videos 目录已保存
- ✅ PDF 文件已保存

---

## 文件命名

PDF 文件名与 HTML 文件名保持一致：

| HTML 文件名 | PDF 文件名 |
|------------|-----------|
| `tweet_123.html` | `tweet_123.pdf` |
| `video_456.html` | `video_456.pdf` |

如果目标目录已存在同名文件，会添加时间戳：

```
tweet_123.pdf
tweet_123_143022.pdf  ← 时间戳后缀
```

---

## 错误处理

如果 PDF 复制失败：
```python
try:
    shutil.copy2(pdf_file, pdf_target)
    has_pdf = True
    logger.info(f"PDF file copied: {pdf_target}")
except Exception as e:
    logger.warning(f"Failed to copy PDF file: {e}")
    # 继续执行，不阻止其他文件复制
```

**不会阻止整个存储操作**，只会记录警告日志。

---

## 总结

### 需要修改的文件

| 文件 | 修改内容 |
|------|---------|
| `ytbot/storage/local_storage.py` | 在 `_save_directory` 方法中添加 PDF 文件复制 |

### 预期结果

1. ✅ 本地存储目录包含 HTML + PDF + images + videos
2. ✅ Nextcloud 上传包含所有文件（HTML + PDF + images + videos）
3. ✅ HTML 和 PDF 使用相同的命名规则
4. ✅ PDF 复制失败不影响其他功能

### 不需要修改的文件

| 文件 | 原因 |
|------|------|
| `nextcloud_storage.py` | 已经会遍历上传所有文件 |
| `storage_service.py` | 已经会调用 `_save_directory` |
| PDF 生成代码 | 已经正确生成 PDF |

---

## 快速修复

如果只想快速修复，可以直接在 `local_storage.py` 的 `_save_directory` 方法中：

1. 在 `# Copy HTML file` 之前添加：
   ```python
   # Look for PDF file
   pdf_files = list(source_dir.glob("*.pdf"))
   pdf_file = pdf_files[0] if pdf_files else None
   ```

2. 在 `shutil.copy2(html_file, html_target)` 之后添加：
   ```python
   # Copy PDF file
   has_pdf = False
   if pdf_file:
       pdf_target = content_dir / pdf_file.name
       try:
           shutil.copy2(pdf_file, pdf_target)
           has_pdf = True
           logger.info(f"PDF file copied: {pdf_target}")
       except Exception as e:
           logger.warning(f"Failed to copy PDF file: {e}")
   ```

3. 在日志输出中添加 `pdf: {has_pdf}`。

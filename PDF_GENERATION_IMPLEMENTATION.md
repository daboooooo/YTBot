# PDF Generation Implementation Guide

Complete implementation guide for adding PDF generation to YTBot.

## Overview

This implementation enables automatic PDF generation alongside HTML when downloading content from platforms like Twitter/X. The PDF will contain images but video content will be replaced with thumbnail placeholders.

## Architecture

```
Download Content
    ↓
Generate HTML (existing)
    ↓
┌─────────────────────────────────────┐
│  PDF Generation Pipeline:            │
│                                     │
│  1. Preprocess HTML                 │
│     - Convert relative paths        │
│     - Replace videos with          │
│       thumbnails                    │
│     - Handle iframe embeds         │
│     - Add print CSS                 │
│                                     │
│  2. Convert to PDF                  │
│     - Try Chrome headless (preferred)│
│     - Fallback to textutil          │
│                                     │
└─────────────────────────────────────┘
    ↓
Save Both HTML & PDF
```

## Files Created

### Core Implementation

1. **ytbot/services/pdf_preprocessor.py**
   - `PdfPreprocessor` class
   - Path conversion (relative → absolute)
   - Video placeholder generation
   - YouTube/Vimeo thumbnail extraction
   - Print-optimized CSS injection

2. **ytbot/services/pdf_converter.py**
   - `PdfConverter` class
   - Chrome headless conversion
   - textutil + cupsfilter fallback
   - Global `pdf_converter` instance
   - Convenience functions

3. **ytbot/platforms/twitter_pdf_extension.py**
   - `TwitterPdfMixin` class
   - Video thumbnail generation
   - PDF generation from content
   - Integration methods

### Integration Guides

4. **ytbot/platforms/twitter_pdf_integration.py**
   - Integration steps for TwitterHandler
   - Code examples

5. **ytbot/platforms/twitter_pdf_patch.py**
   - Detailed code patches
   - Complete method examples

6. **ytbot/storage/storage_pdf_extension.py**
   - Storage service modifications
   - PDF file handling

7. **ytbot/core/pdf_config_guide.py**
   - Configuration options
   - Environment variables
   - Command-line arguments

### Updated Files

8. **ytbot/services/__init__.py**
   - Updated to export PDF services
   - Added docstring examples

## Installation

### Prerequisites

1. **Chrome or Chromium** (recommended):
   ```bash
   # macOS
   brew install --cask google-chrome
   # or
   brew install --cask chromium
   ```

2. **Python dependencies** (already in requirements.txt):
   ```bash
   pip install -r requirements.txt
   ```

### Verification

Check if PDF conversion is available:

```python
from ytbot.services import is_pdf_conversion_available

if is_pdf_conversion_available():
    print("PDF conversion is ready!")
else:
    print("PDF conversion not available")
```

## Integration Steps

### Step 1: Update TwitterHandler

Modify `ytbot/platforms/twitter.py`:

#### 1.1 Add imports (around line 22):

```python
from ytbot.services.pdf_converter import pdf_converter, convert_html_to_pdf
```

#### 1.2 Add helper method (before `_generate_html`):

```python
def _generate_video_thumbnails_mapping(self, local_videos: List[str]) -> Dict[str, str]:
    """Generate thumbnail paths for videos"""
    thumbnails = {}
    from pathlib import Path

    for video_path in local_videos:
        if not os.path.exists(video_path):
            continue

        video_path_obj = Path(video_path)
        possible_thumbnails = [
            video_path_obj.with_suffix('.jpg'),
            video_path_obj.with_suffix('.png'),
            video_path_obj.with_suffix('.thumb.jpg'),
            video_path_obj.parent / 'thumbnails' / f'{video_path_obj.stem}.jpg',
        ]

        for thumb in possible_thumbnails:
            if thumb.exists():
                thumbnails[video_path] = str(thumb)
                break

    return thumbnails
```

#### 1.3 Add PDF generation (after line 2590):

```python
# Generate PDF alongside HTML
pdf_filename = filename.replace('.html', '.pdf')
pdf_file = os.path.join(temp_dir, pdf_filename)

if pdf_converter.is_available():
    try:
        video_thumbnails = self._generate_video_thumbnails_mapping(local_videos)

        pdf_result = await asyncio.to_thread(
            asyncio.run,
            convert_html_to_pdf(
                temp_file,
                pdf_file,
                preprocess=True,
                video_thumbnails=video_thumbnails
            )
        )

        if pdf_result and os.path.exists(pdf_result):
            file_size = os.path.getsize(pdf_result)
            logger.info(f"PDF generated: {pdf_result} ({file_size} bytes)")
    except Exception as e:
        logger.error(f"PDF generation failed: {e}")
else:
    logger.warning("PDF converter not available")
```

#### 1.4 Update metadata (around line 2642):

```python
metadata = {
    # ... existing fields ...
    'pdf_path': pdf_file if 'pdf_file' in locals() and os.path.exists(pdf_file) else None,
}
```

### Step 2: Update Storage Service

Modify `ytbot/storage/local_storage.py`:

In `_save_directory` method, add PDF file handling:

```python
# Check for PDF files
pdf_files = list(source_dir.glob("*.pdf"))
if pdf_files:
    pdf_file = pdf_files[0]
    pdf_target = content_dir / pdf_file.name

    if pdf_target.exists():
        timestamp = datetime.now().strftime("%H%M%S")
        name, ext = os.path.splitext(pdf_file.name)
        pdf_target = content_dir / f"{name}_{timestamp}{ext}"

    try:
        shutil.copy2(pdf_file, pdf_target)
        has_pdf = True
    except Exception as e:
        logger.warning(f"Failed to copy PDF: {e}")
```

## Usage Examples

### Basic Usage

```python
from ytbot.services import convert_html_to_pdf

# Convert HTML file to PDF
pdf_path = await convert_html_to_pdf(
    "input.html",
    "output.pdf",
    preprocess=True
)

if pdf_path:
    print(f"PDF created: {pdf_path}")
```

### With Video Thumbnails

```python
from ytbot.services import convert_html_to_pdf

video_thumbnails = {
    "/path/to/video.mp4": "/path/to/video.thumb.jpg"
}

pdf_path = await convert_html_to_pdf(
    "content.html",
    "content.pdf",
    preprocess=True,
    video_thumbnails=video_thumbnails
)
```

### Direct HTML Content Conversion

```python
from ytbot.services import convert_html_content_to_pdf

html_content = """
<html>
<head><title>Test</title></head>
<body>
    <h1>Hello World</h1>
    <img src="image.jpg">
</body>
</html>
"""

pdf_path = await convert_html_content_to_pdf(
    html_content,
    "output.pdf",
    base_dir="/path/to/images"
)
```

### Twitter Download with PDF

```python
from ytbot.platforms.twitter import TwitterHandler

handler = TwitterHandler()

result = await handler.download_twitter_content(
    "https://twitter.com/user/status/1234567890"
)

if result.success:
    # HTML file
    html_dir = result.file_path
    print(f"HTML directory: {html_dir}")

    # PDF file
    pdf_path = result.content_info.metadata.get('pdf_path')
    if pdf_path:
        print(f"PDF file: {pdf_path}")
```

## Configuration

### Programmatic Configuration

```python
from ytbot.core.config import CONFIG

CONFIG['pdf'] = {
    'enabled': True,
    'auto_generate': True,
    'quality': 'high',
    'page_size': 'A4',
}
```

### Environment Variables

```bash
export YTBOT_PDF_ENABLED=true
export YTBOT_PDF_QUALITY=high
export YTBOT_PDF_PAGE_SIZE=A4
```

## Media Handling

### Images

- ✅ All images are included in PDF
- ✅ Relative paths converted to absolute
- ✅ Images scaled to fit page width
- ✅ Page breaks avoided for images

### Local Videos

- ❌ Videos are NOT playable in PDF
- ✅ Replaced with thumbnail images
- ✅ Placeholder shows video filename
- ✅ Note indicates "view in HTML version"

### Embedded Videos (YouTube/Vimeo)

- ❌ iframes are NOT rendered in PDF
- ✅ YouTube videos: Show thumbnail + link
- ✅ Vimeo videos: Show placeholder + link
- ✅ Clickable links to original video

## Troubleshooting

### Chrome Not Found

**Error**: `Chrome/Chromium not found`

**Solution**:
1. Install Chrome:
   ```bash
   brew install --cask google-chrome
   ```

2. Or use Chromium:
   ```bash
   brew install --cask chromium
   ```

### PDF Generation Failed

**Error**: `All PDF conversion methods failed`

**Solutions**:
1. Check Chrome installation:
   ```bash
   ls "/Applications/Google Chrome.app/Contents/MacOS/"
   ```

2. Try textutil fallback (macOS only):
   ```bash
   which textutil
   which cupsfilter
   ```

3. Check file permissions:
   ```bash
   ls -la your-html-file.html
   ```

### Images Not Showing in PDF

**Problem**: Images appear as broken links

**Solution**:
1. Ensure images use absolute paths
2. Check image files exist
3. Enable preprocessing:
   ```python
   await convert_html_to_pdf(html_path, pdf_path, preprocess=True)
   ```

### Videos Still Showing

**Problem**: Videos appear in PDF instead of thumbnails

**Solution**:
1. Ensure video thumbnails exist:
   ```bash
   ls path/to/video.thumb.jpg
   ```

2. Provide thumbnail mapping:
   ```python
   thumbnails = {"/path/to/video.mp4": "/path/to/video.thumb.jpg"}
   await convert_html_to_pdf(html_path, pdf_path, video_thumbnails=thumbnails)
   ```

## Performance

### Conversion Time

- Simple HTML: ~1-2 seconds
- HTML with images: ~3-5 seconds
- HTML with many images: ~5-10 seconds

### File Size

- Simple PDF: ~50-100 KB
- PDF with images: ~500KB-5MB
- (Depends on image compression)

### Optimization Tips

1. Use smaller images for PDF:
   ```python
   # Resize large images before PDF generation
   from PIL import Image
   img.thumbnail((800, 600))
   ```

2. Disable image embedding for large files:
   ```python
   await convert_html_to_pdf(html_path, pdf_path, preprocess=False)
   ```

## Future Enhancements

Potential improvements for future versions:

1. **Video thumbnail extraction**: Use FFmpeg to extract video frames
2. **PDF compression**: Compress generated PDFs
3. **Watermarking**: Add custom watermarks
4. **Table of contents**: Generate TOC for long content
5. **Custom templates**: Support different PDF templates
6. **Batch conversion**: Convert multiple HTML files to PDF

## Support

For issues or questions:

1. Check the [Troubleshooting](#troubleshooting) section
2. Review logs in `ytbot.log`
3. Verify Chrome/Chromium installation
4. Check file permissions

## License

This PDF generation feature is part of YTBot and follows the same license terms.

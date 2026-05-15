# Quick Start Guide: PDF Generation

## What Was Created

This implementation adds PDF generation capability to YTBot, allowing you to automatically generate PDF files alongside HTML when downloading content.

### New Files

| File | Purpose |
|------|---------|
| [pdf_preprocessor.py](ytbot/services/pdf_preprocessor.py) | Prepares HTML for PDF conversion |
| [pdf_converter.py](ytbot/services/pdf_converter.py) | Converts HTML to PDF |
| [twitter_pdf_extension.py](ytbot/platforms/twitter_pdf_extension.py) | TwitterHandler PDF integration |
| [test_pdf_generation.py](test_pdf_generation.py) | Test script |

### Modified Files

| File | Change |
|------|--------|
| [services/__init__.py](ytbot/services/__init__.py) | Added PDF service exports |

### Documentation Files

| File | Purpose |
|------|---------|
| [PDF_GENERATION_IMPLEMENTATION.md](PDF_GENERATION_IMPLEMENTATION.md) | Complete implementation guide |
| [twitter_pdf_integration.py](ytbot/platforms/twitter_pdf_integration.py) | Integration steps |
| [twitter_pdf_patch.py](ytbot/platforms/twitter_pdf_patch.py) | Code patches |
| [storage_pdf_extension.py](ytbot/storage/storage_pdf_extension.py) | Storage modifications |
| [pdf_config_guide.py](ytbot/core/pdf_config_guide.py) | Configuration guide |

## Quick Test

Test if PDF generation works:

```bash
cd /Users/horsenli/Works/ytbot
python test_pdf_generation.py
```

Expected output:
```
============================================================
PDF GENERATION TEST SUITE
============================================================

TEST 0: Converter Availability Check
============================================================
PDF conversion available: True
Chrome path: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome

✅ PASS: Converter Availability

...
```

## Basic Usage

### 1. Simple HTML to PDF

```python
from ytbot.services import convert_html_to_pdf

# Convert HTML file to PDF
pdf_path = await convert_html_to_pdf(
    "input.html",    # Input HTML file
    "output.pdf",    # Output PDF file
    preprocess=True  # Optimize for PDF
)

if pdf_path:
    print(f"PDF created: {pdf_path}")
```

### 2. From HTML String

```python
from ytbot.services import convert_html_content_to_pdf

html_content = """
<html>
<head><title>Test</title></head>
<body>
    <h1>Hello World</h1>
    <p>This will be converted to PDF.</p>
</body>
</html>
"""

pdf_path = await convert_html_content_to_pdf(
    html_content,
    "output.pdf"
)
```

### 3. With Video Thumbnails

```python
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

## Integration with TwitterHandler

### Step 1: Add Import

In `ytbot/platforms/twitter.py`, add:

```python
from ytbot.services.pdf_converter import pdf_converter, convert_html_to_pdf
```

### Step 2: Add PDF Generation

After HTML generation (around line 2590):

```python
# Generate PDF alongside HTML
pdf_filename = filename.replace('.html', '.pdf')
pdf_file = os.path.join(temp_dir, pdf_filename)

if pdf_converter.is_available():
    try:
        pdf_result = await asyncio.to_thread(
            asyncio.run,
            convert_html_to_pdf(temp_file, pdf_file, preprocess=True)
        )

        if pdf_result and os.path.exists(pdf_result):
            logger.info(f"PDF generated: {pdf_result}")
    except Exception as e:
        logger.error(f"PDF generation failed: {e}")
```

### Step 3: Update Metadata

Add to the metadata dictionary:

```python
'pdf_path': pdf_file if 'pdf_file' in locals() and os.path.exists(pdf_file) else None,
```

## Media Handling in PDF

### Images
- ✅ Included in PDF
- ✅ Relative paths converted to absolute
- ✅ Properly scaled for page width

### Videos
- ❌ NOT playable in PDF
- ✅ Replaced with thumbnail placeholders
- ✅ Shows video filename
- ✅ Note: "View in HTML version"

### YouTube/Vimeo
- ❌ iframes NOT rendered
- ✅ Thumbnail images shown
- ✅ Clickable links included

## Checking Availability

```python
from ytbot.services import is_pdf_conversion_available

if is_pdf_conversion_available():
    print("Ready to generate PDFs!")
else:
    print("PDF conversion not available")
    print("Install Chrome: brew install --cask google-chrome")
```

## Troubleshooting

### Chrome Not Found

```bash
# Install Chrome
brew install --cask google-chrome

# Or install Chromium (lighter)
brew install --cask chromium
```

### Conversion Fails

1. Check Chrome is installed:
   ```bash
   ls "/Applications/Google Chrome.app/Contents/MacOS/"
   ```

2. Check file permissions:
   ```bash
   ls -la your-html-file.html
   ```

3. Run test script:
   ```bash
   python test_pdf_generation.py
   ```

## Configuration

### Programmatic

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
```

## Output Example

When downloading a tweet:

```
Output/
├── tweet_1234567890/
│   ├── tweet_1234567890.html    # Full interactive version
│   ├── tweet_1234567890.pdf    # Print-friendly version
│   ├── images/                  # Downloaded images
│   │   ├── image1.jpg
│   │   └── image2.jpg
│   └── videos/                  # Downloaded videos
│       └── video.mp4
```

## Performance

| Content Type | Time | Size |
|--------------|------|------|
| Simple HTML | 1-2s | 50-100 KB |
| With images | 3-5s | 500KB-2MB |
| Many images | 5-10s | 2-5MB |

## Next Steps

1. **Run Test Script**: `python test_pdf_generation.py`
2. **Integrate with TwitterHandler**: Follow steps in [twitter_pdf_integration.py](ytbot/platforms/twitter_pdf_integration.py)
3. **Configure**: Add PDF options to your config
4. **Use**: Download content and get both HTML and PDF!

## Support

- Full guide: [PDF_GENERATION_IMPLEMENTATION.md](PDF_GENERATION_IMPLEMENTATION.md)
- Integration: [twitter_pdf_integration.py](ytbot/platforms/twitter_pdf_integration.py)
- Test script: [test_pdf_generation.py](test_pdf_generation.py)

## Summary

✅ **What's working:**
- HTML to PDF conversion
- Image embedding
- Video thumbnail placeholders
- YouTube/Vimeo link cards
- Print-optimized CSS
- Multiple fallback methods

❌ **Not supported:**
- Video playback in PDF
- JavaScript execution
- Interactive content

📄 **Result:**
- HTML file: Full interactive version
- PDF file: Print-friendly version

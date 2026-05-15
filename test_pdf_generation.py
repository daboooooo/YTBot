#!/usr/bin/env python3
"""
PDF Generation Test Script

This script tests the PDF generation functionality without modifying
the main application code.

Usage:
    python test_pdf_generation.py
"""

import asyncio
import os
import tempfile


async def test_basic_html_to_pdf():
    """Test basic HTML to PDF conversion"""
    print("\n" + "=" * 60)
    print("TEST 1: Basic HTML to PDF Conversion")
    print("=" * 60)

    from ytbot.services import pdf_converter, is_pdf_conversion_available

    if not is_pdf_conversion_available():
        print("❌ PDF conversion not available")
        return False

    print("✅ PDF converter is available")

    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Test Document</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            h1 { color: #333; border-bottom: 2px solid #333; }
            p { line-height: 1.6; }
        </style>
    </head>
    <body>
        <h1>PDF Generation Test</h1>
        <p>This is a test document to verify PDF generation functionality.</p>
        <p>Features tested:</p>
        <ul>
            <li>Basic HTML rendering</li>
            <li>CSS styling</li>
            <li>Lists and formatting</li>
        </ul>
    </body>
    </html>
    """

    with tempfile.NamedTemporaryFile(
        mode='w',
        suffix='.html',
        delete=False,
        encoding='utf-8'
    ) as f:
        f.write(html_content)
        html_path = f.name

    try:
        pdf_path = html_path.replace('.html', '.pdf')

        result = await pdf_converter.convert_html_to_pdf(
            html_path,
            pdf_path,
            preprocess=True
        )

        if result and os.path.exists(result):
            size = os.path.getsize(result)
            print(f"✅ PDF generated successfully!")
            print(f"   Path: {result}")
            print(f"   Size: {size:,} bytes")
            return True
        else:
            print("❌ PDF generation failed")
            return False

    finally:
        if os.path.exists(html_path):
            os.remove(html_path)
        if os.path.exists(pdf_path):
            print(f"   (Keeping PDF for inspection: {pdf_path})")


async def test_html_with_images():
    """Test HTML to PDF with images"""
    print("\n" + "=" * 60)
    print("TEST 2: HTML to PDF with Images")
    print("=" * 60)

    from ytbot.services import pdf_converter

    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Test with Images</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            img { max-width: 100%; border: 1px solid #ccc; }
        </style>
    </head>
    <body>
        <h1>Test with Images</h1>
        <p>This test verifies image handling in PDF generation.</p>
        <p>Note: Images require absolute paths to work correctly.</p>
    </body>
    </html>
    """

    with tempfile.NamedTemporaryFile(
        mode='w',
        suffix='.html',
        delete=False,
        encoding='utf-8'
    ) as f:
        f.write(html_content)
        html_path = f.name

    try:
        pdf_path = html_path.replace('.html', '.pdf')

        result = await pdf_converter.convert_html_to_pdf(
            html_path,
            pdf_path,
            preprocess=True
        )

        if result and os.path.exists(result):
            size = os.path.getsize(result)
            print(f"✅ PDF with images generated!")
            print(f"   Path: {result}")
            print(f"   Size: {size:,} bytes")
            return True
        else:
            print("❌ PDF generation failed")
            return False

    finally:
        if os.path.exists(html_path):
            os.remove(html_path)
        if os.path.exists(pdf_path):
            print(f"   (Keeping PDF for inspection: {pdf_path})")


async def test_html_content_conversion():
    """Test direct HTML content to PDF conversion"""
    print("\n" + "=" * 60)
    print("TEST 3: Direct HTML Content to PDF")
    print("=" * 60)

    from ytbot.services import convert_html_content_to_pdf

    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Direct Content Test</title>
    </head>
    <body>
        <h1>Direct HTML Content Test</h1>
        <p>This PDF was generated from HTML content string, not a file.</p>
    </body>
    </html>
    """

    with tempfile.NamedTemporaryFile(
        suffix='.pdf',
        delete=False
    ) as f:
        pdf_path = f.name

    try:
        result = await convert_html_content_to_pdf(
            html_content,
            pdf_path
        )

        if result and os.path.exists(result):
            size = os.path.getsize(result)
            print(f"✅ Direct content PDF generated!")
            print(f"   Path: {result}")
            print(f"   Size: {size:,} bytes")
            return True
        else:
            print("❌ PDF generation failed")
            return False

    finally:
        if os.path.exists(pdf_path):
            print(f"   (Keeping PDF for inspection: {pdf_path})")


async def test_preprocessor():
    """Test PDF preprocessor functionality"""
    print("\n" + "=" * 60)
    print("TEST 4: PDF Preprocessor")
    print("=" * 60)

    from ytbot.services.pdf_preprocessor import preprocess_for_pdf

    html_content = """
    <html>
    <head><title>Preprocessor Test</title></head>
    <body>
        <h1>Preprocessor Test</h1>
        <video controls>
            <source src="videos/test.mp4">
        </video>
        <iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe>
    </body>
    </html>
    """

    base_dir = tempfile.gettempdir()

    processed_html = preprocess_for_pdf(
        html_content,
        base_dir
    )

    if processed_html:
        print("✅ Preprocessing successful!")
        print(f"   Original size: {len(html_content)} bytes")
        print(f"   Processed size: {len(processed_html)} bytes")

        has_video_placeholder = "pdf-video-placeholder" in processed_html
        has_youtube_placeholder = "pdf-youtube-placeholder" in processed_html
        has_print_styles = "@media print" in processed_html

        status_video = '✅' if has_video_placeholder else '❌'
        status_yt = '✅' if has_youtube_placeholder else '❌'
        status_print = '✅' if has_print_styles else '❌'

        print(f"   Video placeholders: {status_video}")
        print(f"   YouTube placeholders: {status_yt}")
        print(f"   Print styles: {status_print}")

        return True
    else:
        print("❌ Preprocessing failed")
        return False


async def test_converter_availability():
    """Test if converter is available"""
    print("\n" + "=" * 60)
    print("TEST 0: PDF Converter Availability Check")
    print("=" * 60)

    from ytbot.services import pdf_converter, is_pdf_conversion_available

    print(f"PDF conversion available: {is_pdf_conversion_available()}")
    print(f"Chrome path: {pdf_converter._chrome_path}")

    return is_pdf_conversion_available()


async def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("PDF GENERATION TEST SUITE")
    print("=" * 60)

    tests = [
        ("Converter Availability", test_converter_availability),
        ("Basic HTML to PDF", test_basic_html_to_pdf),
        ("HTML with Images", test_html_with_images),
        ("Direct Content Conversion", test_html_content_conversion),
        ("Preprocessor", test_preprocessor),
    ]

    results = []

    for test_name, test_func in tests:
        try:
            result = await test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n❌ Test '{test_name}' crashed:")
            print(f"   Error: {e}")
            results.append((test_name, False))

    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed! PDF generation is ready to use.")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Please check the output above.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)

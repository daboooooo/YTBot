"""
PDF conversion service for YTBot

Converts HTML to PDF using multiple fallback strategies:
1. Chrome/Chromium headless (preferred, cross-platform)
2. wkhtmltopdf (Linux fallback)
3. macOS textutil + cupsfilter (macOS fallback)
"""

import asyncio
import os
import shutil
import sys
import tempfile
from typing import Dict, Optional

from ..core.enhanced_logger import get_logger
from .pdf_preprocessor import PdfPreprocessor

logger = get_logger(__name__)


class PdfConverter:
    """PDF converter with multiple fallback strategies"""

    def __init__(self):
        self._chrome_path = self._find_chrome_path()
        self._wkhtmltopdf_path = self._find_wkhtmltopdf_path()
        self._preprocessor = PdfPreprocessor()

    def _find_chrome_path(self) -> Optional[str]:
        """Find Chrome/Chromium executable path"""

        possible_paths = [
            # macOS
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            (
                "/Applications/Google Chrome for Testing.app/"
                "Contents/MacOS/Google Chrome for Testing"
            ),
            # Linux (common locations)
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/local/bin/chromium",
            "/snap/bin/chromium",
            # Also check via which
        ]

        for path in possible_paths:
            if os.path.exists(path):
                logger.info(f"Found Chrome/Chromium at: {path}")
                return path

        # Try finding via PATH
        for name in ["google-chrome", "google-chrome-stable",
                      "chromium", "chromium-browser"]:
            found = shutil.which(name)
            if found:
                logger.info(f"Found Chrome/Chromium via PATH: {found}")
                return found

        logger.warning("Chrome/Chromium not found in standard locations")
        return None

    def _find_wkhtmltopdf_path(self) -> Optional[str]:
        """Find wkhtmltopdf executable path"""
        found = shutil.which("wkhtmltopdf")
        if found:
            logger.info(f"Found wkhtmltopdf at: {found}")
        return found

    def is_available(self) -> bool:
        """Check if PDF conversion is available"""
        return (
            self._chrome_path is not None
            or self._wkhtmltopdf_path is not None
            or shutil.which("textutil") is not None
        )

    async def convert_html_to_pdf(
        self,
        html_path: str,
        output_pdf_path: str,
        preprocess: bool = True,
        video_thumbnails: Optional[Dict[str, str]] = None
    ) -> Optional[str]:
        """
        Convert HTML file to PDF

        Args:
            html_path: HTML file path
            output_pdf_path: Output PDF file path
            preprocess: Whether to preprocess HTML for better PDF output
            video_thumbnails: Mapping of video files to thumbnails

        Returns:
            PDF file path, or None if conversion failed
        """
        html_path = os.path.abspath(html_path)

        if not os.path.exists(html_path):
            logger.error(f"HTML file not found: {html_path}")
            return None

        output_dir = os.path.dirname(output_pdf_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        html_to_convert = html_path

        if preprocess:
            try:
                with open(html_path, 'r', encoding='utf-8') as f:
                    html_content = f.read()

                processed_html = self._preprocessor.preprocess(
                    html_content,
                    os.path.dirname(html_path),
                    video_thumbnails
                )

                temp_html_fd, temp_html_path = tempfile.mkstemp(
                    suffix='.html',
                    prefix='ytbot_pdf_'
                )

                try:
                    with os.fdopen(temp_html_fd, 'w', encoding='utf-8') as f:
                        f.write(processed_html)

                    html_to_convert = temp_html_path
                    logger.info("HTML preprocessed for PDF conversion")
                except Exception as e:
                    logger.error(f"Failed to create temp HTML file: {e}")
                    html_to_convert = html_path
            except Exception as e:
                logger.error(f"HTML preprocessing failed: {e}")
                html_to_convert = html_path

        conversion_methods = [
            ("Chrome Headless", self._convert_with_chrome),
            ("wkhtmltopdf", self._convert_with_wkhtmltopdf),
            ("textutil + cupsfilter", self._convert_with_textutil),
        ]

        for method_name, method_func in conversion_methods:
            try:
                result = await method_func(html_to_convert, output_pdf_path)
                if result:
                    logger.info(
                        f"PDF generated successfully using {method_name}"
                    )
                    return result
            except Exception as e:
                logger.warning(f"{method_name} conversion failed: {e}")

        logger.error("All PDF conversion methods failed")
        return None

    async def convert_html_content_to_pdf(
        self,
        html_content: str,
        output_pdf_path: str,
        base_dir: Optional[str] = None,
        video_thumbnails: Optional[Dict[str, str]] = None
    ) -> Optional[str]:
        """
        Convert HTML content (string) directly to PDF without saving HTML file

        Args:
            html_content: HTML content as string
            output_pdf_path: Output PDF file path
            base_dir: Base directory for resolving relative paths
            video_thumbnails: Mapping of video files to thumbnails

        Returns:
            PDF file path, or None if conversion failed
        """
        if base_dir is None:
            base_dir = tempfile.gettempdir()

        temp_html_fd, temp_html_path = tempfile.mkstemp(
            suffix='.html',
            prefix='ytbot_pdf_',
            dir=base_dir
        )

        try:
            with os.fdopen(temp_html_fd, 'w', encoding='utf-8') as f:
                f.write(html_content)

            return await self.convert_html_to_pdf(
                temp_html_path,
                output_pdf_path,
                preprocess=True,
                video_thumbnails=video_thumbnails
            )
        finally:
            if os.path.exists(temp_html_path):
                try:
                    os.remove(temp_html_path)
                except Exception:
                    pass

    async def _convert_with_chrome(
        self,
        html_path: str,
        output_path: str
    ) -> Optional[str]:
        """Convert HTML to PDF using Chrome headless mode"""

        if not self._chrome_path:
            raise RuntimeError("Chrome/Chromium not found")

        html_path_abs = os.path.abspath(html_path)
        output_path_abs = os.path.abspath(output_path)

        file_url = f"file://{html_path_abs}"

        cmd = [
            self._chrome_path,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            f"--print-to-pdf={output_path_abs}",
            "--print-to-pdf-no-header",
            file_url
        ]

        logger.info("Running Chrome PDF conversion...")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=60
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError("Chrome PDF conversion timed out")

        if process.returncode != 0:
            error_msg = stderr.decode().strip()
            if error_msg:
                logger.error(f"Chrome error: {error_msg}")
            raise RuntimeError(
                f"Chrome conversion failed with code {process.returncode}"
            )

        if os.path.exists(output_path_abs):
            file_size = os.path.getsize(output_path_abs)
            logger.info(
                f"PDF generated with Chrome: {output_path_abs} "
                f"({file_size} bytes)"
            )
            return output_path_abs

        raise RuntimeError("PDF file was not created")

    async def _convert_with_wkhtmltopdf(
        self,
        html_path: str,
        output_path: str
    ) -> Optional[str]:
        """Convert HTML to PDF using wkhtmltopdf (Linux-friendly)"""

        if not self._wkhtmltopdf_path:
            raise RuntimeError("wkhtmltopdf not found")

        html_path_abs = os.path.abspath(html_path)
        output_path_abs = os.path.abspath(output_path)

        cmd = [
            self._wkhtmltopdf_path,
            "--enable-local-file-access",
            "--no-stop-slow-scripts",
            "--encoding", "utf-8",
            "--page-size", "A4",
            "--margin-top", "10mm",
            "--margin-bottom", "10mm",
            "--margin-left", "10mm",
            "--margin-right", "10mm",
            html_path_abs,
            output_path_abs,
        ]

        logger.info("Running wkhtmltopdf PDF conversion...")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=60
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError("wkhtmltopdf conversion timed out")

        # wkhtmltopdf returns 0 for success, 1 for warnings (still produces PDF)
        if process.returncode not in (0, 1):
            error_msg = stderr.decode().strip()
            if error_msg:
                logger.error(f"wkhtmltopdf error: {error_msg}")
            raise RuntimeError(
                f"wkhtmltopdf failed with code {process.returncode}"
            )

        if os.path.exists(output_path_abs):
            file_size = os.path.getsize(output_path_abs)
            logger.info(
                f"PDF generated with wkhtmltopdf: {output_path_abs} "
                f"({file_size} bytes)"
            )
            return output_path_abs

        raise RuntimeError("PDF file was not created")

    async def _convert_with_textutil(
        self,
        html_path: str,
        output_path: str
    ) -> Optional[str]:
        """Convert HTML to PDF using macOS built-in tools"""

        if shutil.which("textutil") is None:
            raise RuntimeError("textutil not available")

        html_path_abs = os.path.abspath(html_path)
        output_path_abs = os.path.abspath(output_path)
        rtf_path = os.path.splitext(output_path_abs)[0] + ".rtf"

        cmd_rtf = [
            "textutil",
            "-convert", "rtf",
            "-output", rtf_path,
            html_path_abs
        ]

        logger.info("Converting HTML to RTF using textutil...")

        process_rtf = await asyncio.create_subprocess_exec(
            *cmd_rtf,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout_rtf, stderr_rtf = await process_rtf.communicate()

        if process_rtf.returncode != 0:
            error_msg = stderr_rtf.decode().strip()
            raise RuntimeError(f"textutil RTF conversion failed: {error_msg}")

        if not os.path.exists(rtf_path):
            raise RuntimeError("RTF file was not created")

        if shutil.which("cupsfilter") is None:
            logger.warning("cupsfilter not available, trying alternative")
            return await self._convert_rtf_to_pdf_alternative(
                rtf_path,
                output_path_abs
            )

        cmd_pdf = [
            "cupsfilter",
            "-o", output_path_abs,
            rtf_path
        ]

        logger.info("Converting RTF to PDF using cupsfilter...")

        process_pdf = await asyncio.create_subprocess_exec(
            *cmd_pdf,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout_pdf, stderr_pdf = await process_pdf.communicate()

        if os.path.exists(rtf_path):
            try:
                os.remove(rtf_path)
            except Exception:
                pass

        if process_pdf.returncode != 0:
            error_msg = stderr_pdf.decode().strip()
            raise RuntimeError(f"cupsfilter PDF conversion failed: {error_msg}")

        if os.path.exists(output_path_abs):
            file_size = os.path.getsize(output_path_abs)
            logger.info(
                f"PDF generated with textutil+cupsfilter: "
                f"{output_path_abs} ({file_size} bytes)"
            )
            return output_path_abs

        raise RuntimeError("PDF file was not created")

    async def _convert_rtf_to_pdf_alternative(
        self,
        rtf_path: str,
        output_path: str
    ) -> Optional[str]:
        """Alternative method to convert RTF to PDF"""

        # Try libreoffice if available (common on Linux)
        libreoffice = (
            shutil.which("libreoffice")
            or shutil.which("soffice")
        )
        if libreoffice:
            logger.info("Trying LibreOffice for RTF to PDF conversion...")
            output_dir = os.path.dirname(output_path)
            cmd = [
                libreoffice,
                "--headless",
                "--convert-to", "pdf",
                "--outdir", output_dir,
                rtf_path
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=60
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise RuntimeError("LibreOffice conversion timed out")

            # Clean up RTF
            if os.path.exists(rtf_path):
                try:
                    os.remove(rtf_path)
                except Exception:
                    pass

            if process.returncode == 0 and os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                logger.info(
                    f"PDF generated with LibreOffice: {output_path} "
                    f"({file_size} bytes)"
                )
                return output_path

        raise RuntimeError("Alternative PDF conversion not available")


pdf_converter = PdfConverter()


async def convert_html_to_pdf(
    html_path: str,
    output_pdf_path: str,
    preprocess: bool = True,
    video_thumbnails: Optional[Dict[str, str]] = None
) -> Optional[str]:
    """
    Convenience function to convert HTML to PDF

    Args:
        html_path: HTML file path
        output_pdf_path: Output PDF file path
        preprocess: Whether to preprocess HTML
        video_thumbnails: Mapping of video files to thumbnails

    Returns:
        PDF file path, or None if conversion failed
    """
    return await pdf_converter.convert_html_to_pdf(
        html_path,
        output_pdf_path,
        preprocess,
        video_thumbnails
    )


async def convert_html_content_to_pdf(
    html_content: str,
    output_pdf_path: str,
    base_dir: Optional[str] = None,
    video_thumbnails: Optional[Dict[str, str]] = None
) -> Optional[str]:
    """
    Convenience function to convert HTML content to PDF

    Args:
        html_content: HTML content as string
        output_pdf_path: Output PDF file path
        base_dir: Base directory for resolving relative paths
        video_thumbnails: Mapping of video files to thumbnails

    Returns:
        PDF file path, or None if conversion failed
    """
    return await pdf_converter.convert_html_content_to_pdf(
        html_content,
        output_pdf_path,
        base_dir,
        video_thumbnails
    )


def is_pdf_conversion_available() -> bool:
    """Check if PDF conversion is available"""
    return pdf_converter.is_available()

"""
Service layer for YTBot.

This module provides high-level services that coordinate the application's
core functionality. Each service encapsulates a specific domain of operations
and provides a clean API for the bot to interact with.

The service layer abstracts away the complexity of individual components and
provides a unified interface for common operations like Telegram communication,
file storage, content downloading, and PDF generation.

Exported Components:
    TelegramService: class
        Service for Telegram bot communication. Handles connection management,
        message sending/editing, handler registration, and user permission
        checking. Provides detailed logging for all operations.

    StorageService: class
        Unified storage service managing both local and cloud storage backends.
        Provides automatic failover from Nextcloud to local storage, cache
        queue support for retry when Nextcloud recovers, and comprehensive
        storage health monitoring.

    DownloadService: class
        Service that coordinates platform handlers and manages download operations.
        Supports URL validation, content information extraction, and downloading
        with progress callbacks and format selection.

    PdfConverter: class
        Service for converting HTML content to PDF. Supports multiple conversion
        methods (Chrome headless, textutil) with automatic fallback.

    PdfPreprocessor: class
        Preprocesses HTML content for better PDF conversion, handling media
        paths and video placeholders.

Example:
    >>> from ytbot.services import TelegramService, StorageService, DownloadService
    >>>
    >>> # Initialize services
    >>> telegram = TelegramService()
    >>> storage = StorageService()
    >>> download = DownloadService()
    >>>
    >>> # Connect to Telegram
    >>> await telegram.connect()
    >>>
    >>> # Download content
    >>> result = await download.download_content(
    ...     "https://www.youtube.com/watch?v=example",
    ...     content_type="video"
    ... )
    >>>
    >>> # Store the downloaded file
    >>> if result.success:
    ...     storage_result = await storage.store_file(
    ...         result.file_path,
    ...         "video.mp4",
    ...         content_type="video"
    ...     )
    >>>
    >>> # Send notification
    >>> await telegram.send_message(
    ...     chat_id=123456,
    ...     text="Download completed!"
    ... )

PDF Generation Example:
    >>> from ytbot.services import PdfConverter, convert_html_to_pdf
    >>>
    >>> # Convert HTML file to PDF
    >>> pdf_path = await convert_html_to_pdf(
    ...     "content.html",
    ...     "output.pdf"
    ... )
    >>>
    >>> if pdf_path:
    ...     print(f"PDF generated: {pdf_path}")
"""

from .telegram_service import TelegramService
from .storage_service import StorageService
from .download_service import DownloadService
from .pdf_converter import (
    PdfConverter,
    pdf_converter,
    convert_html_to_pdf,
    convert_html_content_to_pdf,
    is_pdf_conversion_available
)
from .pdf_preprocessor import PdfPreprocessor, pdf_preprocessor, preprocess_for_pdf

__all__ = [
    "TelegramService",
    "StorageService",
    "DownloadService",
    "PdfConverter",
    "pdf_converter",
    "convert_html_to_pdf",
    "convert_html_content_to_pdf",
    "is_pdf_conversion_available",
    "PdfPreprocessor",
    "pdf_preprocessor",
    "preprocess_for_pdf",
]

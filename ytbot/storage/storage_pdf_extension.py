"""
Storage service PDF support extension

This module provides guidance on how to modify storage_service.py to support
saving PDF files alongside HTML files.

================================================================================
WHY MODIFY STORAGE SERVICE?
================================================================================

The storage service is responsible for:
1. Saving downloaded content to local storage
2. Managing file organization
3. Handling cleanup

When PDF generation is added, we need to:
1. Detect PDF files in the source directory
2. Copy PDF files to local storage alongside HTML
3. Update file size calculations to include PDF

================================================================================
CHANGE: Update _save_directory method in local_storage.py
================================================================================

In the _save_directory method (around line 141), add PDF file handling:

Current code (simplified):

    def _save_directory(self, source_dir: Path, filename: str) -> Optional[str]:
        try:
            html_files = list(source_dir.glob("*.html"))
            if not html_files:
                logger.error(f"No HTML file found in directory: {source_dir}")
                return None

            html_file = html_files[0]

            # ... create target directory ...

            # Copy HTML file
            html_target = content_dir / target_path.name
            shutil.copy2(html_file, html_target)

            # Copy images
            images_source = source_dir / "images"
            if images_source.exists():
                # ... copy images ...

            # Copy videos
            videos_source = source_dir / "videos"
            if videos_source.exists():
                # ... copy videos ...

            return str(target_path)

With PDF support:

    def _save_directory(self, source_dir: Path, filename: str) -> Optional[str]:
        try:
            html_files = list(source_dir.glob("*.html"))
            pdf_files = list(source_dir.glob("*.pdf"))  # ADD THIS LINE

            if not html_files:
                logger.error(f"No HTML file found in directory: {source_dir}")
                return None

            html_file = html_files[0]

            # ... create target directory ...

            # Copy HTML file
            html_target = content_dir / target_path.name
            shutil.copy2(html_file, html_target)

            # Copy PDF file (if exists)
            has_pdf = False
            if pdf_files:
                pdf_file = pdf_files[0]
                pdf_target = content_dir / pdf_file.name

                # Handle duplicate filename
                if pdf_target.exists():
                    timestamp = datetime.now().strftime("%H%M%S")
                    name, ext = os.path.splitext(pdf_file.name)
                    pdf_target = content_dir / f"{name}_{timestamp}{ext}"

                try:
                    shutil.copy2(pdf_file, pdf_target)
                    has_pdf = True
                    logger.info(f"PDF file copied: {pdf_target}")
                except Exception as e:
                    logger.warning(f"Failed to copy PDF file: {e}")

            # Copy images
            images_source = source_dir / "images"
            if images_source.exists():
                # ... copy images ...

            # Copy videos
            videos_source = source_dir / "videos"
            if videos_source.exists():
                # ... copy videos ...

            # Update logging
            has_images = images_source.exists()
            has_videos = videos_source.exists()

            if has_images or has_videos or has_pdf:
                logger.info(
                    f"Directory saved to local storage: {target_path} "
                    f"(images: {has_images}, videos: {has_videos}, pdf: {has_pdf})"
                )

            return str(target_path)

================================================================================
ENHANCEMENT: Add PDF-specific save method
================================================================================

Optionally, add a dedicated method for saving PDF content:

    def save_pdf_content(
        self,
        source_path: str,
        filename: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        '''
        Save PDF content to local storage

        Args:
            source_path: Source PDF file path
            filename: Target filename
            metadata: Optional metadata about the PDF

        Returns:
            Local file path if successful, None otherwise
        '''
        if not self.enabled:
            logger.warning("Local storage is disabled")
            return None

        try:
            source = Path(source_path)

            if not source.exists():
                logger.error(f"PDF file not found: {source_path}")
                return None

            if not source.suffix.lower() == '.pdf':
                logger.error(f"File is not a PDF: {source_path}")
                return None

            date_folder = datetime.now().strftime("%Y-%m")
            target_dir = self.storage_path / date_folder
            target_dir.mkdir(exist_ok=True)

            target_path = target_dir / filename

            if target_path.exists():
                timestamp = datetime.now().strftime("%H%M%S")
                name, ext = os.path.splitext(filename)
                target_path = target_dir / f"{name}_{timestamp}{ext}"

            shutil.copy2(source_path, target_path)

            logger.info(f"PDF saved to local storage: {target_path}")
            return str(target_path)

        except Exception as e:
            logger.error(f"Failed to save PDF to local storage: {e}")
            return None

================================================================================
ENHANCEMENT: Update file info to include PDF
================================================================================

Update the get_file_info method to detect and report PDF files:

    def get_file_info(self, file_path: str) -> Optional[Dict[str, Any]]:
        try:
            path = Path(file_path)
            if not path.exists():
                return None

            stat = path.stat()
            is_pdf = path.suffix.lower() == '.pdf'

            info = {
                "path": str(path),
                "size": stat.st_size,
                "size_mb": stat.st_size / (1024 * 1024),
                "created": datetime.fromtimestamp(stat.st_ctime),
                "modified": datetime.fromtimestamp(stat.st_mtime),
                "filename": path.name,
                "is_pdf": is_pdf,
            }

            # For directories, check if there's an associated PDF
            if path.is_dir():
                html_files = list(path.glob("*.html"))
                pdf_files = list(path.glob("*.pdf"))
                info["has_html"] = len(html_files) > 0
                info["has_pdf"] = len(pdf_files) > 0
                info["pdf_files"] = [str(p) for p in pdf_files]

            return info

        except Exception as e:
            logger.error(f"Failed to get file info: {e}")
            return None

================================================================================
USAGE EXAMPLE
================================================================================

After applying these changes:

    from ytbot.storage.local_storage import local_storage_manager

    # Save content with HTML and PDF
    source_dir = "/tmp/tweet_content_123"
    filename = "tweet.html"

    saved_path = local_storage_manager.save_file_locally(source_dir, filename)

    # Check file info
    if saved_path:
        file_info = local_storage_manager.get_file_info(saved_path)
        print(f"HTML: {saved_path}")
        if file_info.get("has_pdf"):
            print(f"PDF: {file_info['pdf_files'][0]}")

    # Save PDF directly
    pdf_path = local_storage_manager.save_pdf_content(
        "/path/to/video.pdf",
        "video.pdf"
    )

"""

# This file contains documentation and code patches
# Apply the changes manually to local_storage.py

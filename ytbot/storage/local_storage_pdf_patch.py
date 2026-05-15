"""
Patch for local_storage.py to add PDF file support

This file contains the code changes needed to make local_storage.py
handle PDF files alongside HTML files.

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
            html_target = tweet_dir / target_path.name
            shutil.copy2(html_file, html_target)

            # Update target_path to point to the HTML inside tweet directory
            target_path = html_target

            # Copy images
            images_source = source_dir / "images"
            if images_source.exists():
                # ... copy images ...

            # Copy videos
            videos_source = source_dir / "videos"
            if videos_source.exists():
                # ... copy videos ...

            # ... logging ...

            return str(target_path)

With PDF support:

    def _save_directory(self, source_dir: Path, filename: str) -> Optional[str]:
        try:
            html_files = list(source_dir.glob("*.html"))
            if not html_files:
                logger.error(f"No HTML file found in directory: {source_dir}")
                return None

            html_file = html_files[0]

            # Look for corresponding PDF file
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

            # Create a directory for this content's files
            content_dir = target_path.parent / target_path.stem
            content_dir.mkdir(exist_ok=True)

            # Copy HTML file into the content directory
            html_target = content_dir / target_path.name
            shutil.copy2(html_file, html_target)

            # Update target_path to point to the HTML inside content directory
            target_path = html_target

            # Copy PDF file (if exists)
            has_pdf = False
            if pdf_file:
                pdf_target = content_dir / pdf_file.name

                # Handle duplicate filename
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

================================================================================
COMPLETE METHOD: Full _save_directory implementation
================================================================================

Here's the complete _save_directory method with PDF support:

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

================================================================================
RESULT: Directory structure after saving
================================================================================

Source directory (before saving):
    /tmp/tweet_abc123/
    ├── tweet_abc123.html
    ├── tweet_abc123.pdf    ← PDF generated here
    ├── images/
    │   └── *.jpg
    └── videos/
        └── *.mp4

Local storage (after saving):
    /path/to/storage/
    └── 2026-05/
        └── tweet_abc123/
            ├── tweet_abc123.html   ← HTML
            ├── tweet_abc123.pdf    ← PDF ✓ Now copied!
            ├── images/
            │   └── *.jpg
            └── videos/
                └── *.mp4

Nextcloud (after uploading):
    /remote/path/
    └── Media/
        └── Tweet/
            └── tweet_abc123/
                ├── tweet_abc123.html   ← HTML
                ├── tweet_abc123.pdf    ← PDF ✓ Now uploaded!
                ├── images/
                │   └── *.jpg
                └── videos/
                    └── *.mp4

================================================================================
INTEGRATION STEPS
================================================================================

1. Open ytbot/storage/local_storage.py

2. Find the _save_directory method (around line 141)

3. Replace the entire method with the implementation above

4. Test by downloading a tweet with PDF generation enabled

5. Verify:
   - PDF file exists in local storage directory
   - PDF file exists in Nextcloud after upload
   - Both HTML and PDF have the same base filename

================================================================================
"""

# This file contains documentation and code patches
# Apply the changes manually to local_storage.py

"""
Patch for TwitterHandler to add PDF generation

This file contains the specific code changes needed to integrate PDF generation
into the TwitterHandler class. Apply these changes to twitter.py.

================================================================================
CHANGE 1: Import PDF converter at the top of twitter.py
================================================================================

Add these imports after the existing imports (around line 22):

    from ytbot.services.pdf_converter import pdf_converter, convert_html_to_pdf

================================================================================
CHANGE 2: Add PDF generation after HTML generation (around line 2584-2590)
================================================================================

Replace this code:

    tweet_id = self.extract_tweet_id(url) or 'unknown'
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"tweet_{tweet_id}_{timestamp}.html"
    temp_file = os.path.join(temp_dir, filename)

    html_content = self._generate_html(result, local_images, local_videos)
    with open(temp_file, 'w', encoding='utf-8') as f:
        f.write(html_content)

    logger.info(f"Saved tweet content to temporary file: {temp_file}")

With this code:

    tweet_id = self.extract_tweet_id(url) or 'unknown'
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"tweet_{tweet_id}_{timestamp}.html"
    temp_file = os.path.join(temp_dir, filename)

    html_content = self._generate_html(result, local_images, local_videos)
    with open(temp_file, 'w', encoding='utf-8') as f:
        f.write(html_content)

    logger.info(f"Saved tweet content to temporary file: {temp_file}")

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
                logger.info(f"PDF generated successfully: {pdf_result} ({file_size} bytes)")
            else:
                logger.warning("PDF generation returned empty result")
        except Exception as e:
            logger.error(f"PDF generation failed: {e}")
    else:
        logger.warning("PDF converter not available, skipping PDF generation")

================================================================================
CHANGE 3: Add helper method to TwitterHandler class
================================================================================

Add this method to the TwitterHandler class (before the _generate_html method):

    def _generate_video_thumbnails_mapping(self, local_videos: List[str]) -> Dict[str, str]:
        '''
        Generate thumbnail paths for videos

        Args:
            local_videos: List of local video paths

        Returns:
            Mapping of video paths to thumbnail paths
        '''
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
                video_path_obj.with_suffix('.thumb.png'),
                video_path_obj.parent / 'thumbnails' / f'{video_path_obj.stem}.jpg',
                video_path_obj.parent / 'thumbnails' / f'{video_path_obj.stem}.png',
            ]

            for thumb in possible_thumbnails:
                if thumb.exists():
                    thumbnails[video_path] = str(thumb)
                    break

        return thumbnails

================================================================================
CHANGE 4: Update metadata in DownloadResult (around line 2626-2643)
================================================================================

Add 'pdf_path' to the metadata dictionary:

    metadata = {
        'tweet_id': tweet_id,
        'images': list(local_images.values()),
        'videos': local_videos,
        'formats': result.get('formats', []),
        'full_content': content_text,
        'html': result.get('html', ''),
        'post_type': post_type,
        'article_title': article_title,
        'has_video': has_video,
        'video_urls': video_urls,
        'embedded_videos': result.get('embedded_videos', []),
        'external_links': external_links,
        'author': author,
        'timestamp': result.get('timestamp', ''),
        'local_images': local_images,
        'local_videos': local_videos,
        'pdf_path': pdf_file if 'pdf_file' in locals() and os.path.exists(pdf_file) else None,
    }

================================================================================
COMPLETE EXAMPLE: Full modified download_twitter_content method
================================================================================

Here's a complete example of the modified method (simplified for clarity):

    async def download_twitter_content(
        self,
        url: str,
        progress_callback: Optional[Any] = None
    ) -> DownloadResult:
        try:
            # ... existing code for content extraction ...

            tweet_id = self.extract_tweet_id(url) or 'unknown'
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"tweet_{tweet_id}_{timestamp}.html"
            temp_file = os.path.join(temp_dir, filename)

            html_content = self._generate_html(result, local_images, local_videos)
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(html_content)

            logger.info(f"Saved tweet content to temporary file: {temp_file}")

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
                    else:
                        logger.warning("PDF generation returned empty result")
                except Exception as e:
                    logger.error(f"PDF generation failed: {e}")

            # ... rest of the method ...

            metadata = {
                # ... existing metadata fields ...
                'pdf_path': pdf_file if 'pdf_file' in locals() and os.path.exists(pdf_file) else None,
            }

            content_info = ContentInfo(
                url=url,
                title=generated_title,
                description=content_text[:200],
                content_type=actual_content_type,
                uploader=author,
                upload_date=datetime.now().strftime('%Y-%m-%d'),
                metadata=metadata
            )

            return DownloadResult(
                success=True,
                file_path=temp_dir,
                content_info=content_info,
                error_message=None
            )

        except Exception as e:
            logger.error(f"Failed to download Twitter content: {e}")
            return DownloadResult(
                success=False,
                error_message=str(e)
            )

================================================================================
USAGE EXAMPLE
================================================================================

After applying these changes, you can use the PDF generation like this:

    from ytbot.platforms.twitter import TwitterHandler

    handler = TwitterHandler()

    result = await handler.download_twitter_content(
        "https://twitter.com/user/status/1234567890"
    )

    if result.success:
        # Access HTML file
        html_dir = result.file_path
        html_files = [f for f in os.listdir(html_dir) if f.endswith('.html')]
        if html_files:
            html_path = os.path.join(html_dir, html_files[0])
            print(f"HTML: {html_path}")

        # Access PDF file
        pdf_path = result.content_info.metadata.get('pdf_path')
        if pdf_path:
            print(f"PDF: {pdf_path}")
        else:
            print("PDF was not generated")

"""

# This file contains documentation and code patches
# Apply the changes manually to twitter.py

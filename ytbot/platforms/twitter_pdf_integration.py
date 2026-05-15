"""
Integration guide for PDF generation in TwitterHandler

To enable PDF generation in TwitterHandler, follow these steps:

1. Import the TwitterPdfMixin class:
   from .twitter_pdf_extension import TwitterPdfMixin

2. Modify the TwitterHandler class definition:
   class TwitterHandler(TwitterPdfMixin, PlatformHandler):
       ...

3. In the download_twitter_content method (around line 2584),
   add PDF generation after HTML generation:

   # Current code:
   temp_file = os.path.join(temp_dir, filename)
   html_content = self._generate_html(result, local_images, local_videos)
   with open(temp_file, 'w', encoding='utf-8') as f:
       f.write(html_content)

   # Add this after:
   pdf_filename = filename.replace('.html', '.pdf')
   pdf_file = os.path.join(temp_dir, pdf_filename)

   video_thumbnails = self._generate_video_thumbnails(local_videos)

   if pdf_converter.is_available():
       try:
           pdf_result = await pdf_converter.convert_html_to_pdf(
               temp_file,
               pdf_file,
               preprocess=True,
               video_thumbnails=video_thumbnails
           )
           if pdf_result:
               logger.info(f"PDF generated: {pdf_result}")
           else:
               logger.warning("PDF generation failed, continuing with HTML only")
       except Exception as e:
           logger.error(f"PDF generation error: {e}")

4. Update the metadata in DownloadResult to include PDF path:
   metadata = {
       ...
       'pdf_path': pdf_file if os.path.exists(pdf_file) else None,
       ...
   }

Example usage:
-------------

from ytbot.platforms.twitter import TwitterHandler

handler = TwitterHandler()

# Download and get both HTML and PDF
result = await handler.download_twitter_content(url)

if result.success:
    html_path = result.file_path  # Path to HTML file
    pdf_path = result.content_info.metadata.get('pdf_path')  # Path to PDF file
    print(f"HTML: {html_path}")
    print(f"PDF: {pdf_path}")

"""

# This file serves as documentation and contains the integration steps
# No code execution is needed

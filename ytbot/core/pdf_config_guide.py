"""
PDF generation configuration guide

This file explains how to add PDF generation configuration options
to the project's configuration system.

================================================================================
STEP 1: Add PDF configuration to config.py
================================================================================

In ytbot/core/config.py, add a new configuration class:

    class PdfConfig:
        '''PDF generation configuration'''

        def __init__(self):
            self.enabled = True
            self.auto_generate = True
            self.quality = "high"  # "low", "medium", "high"
            self.page_size = "A4"  # "A4", "Letter", "Legal"
            self.include_images = True
            self.include_videos = False  # Videos will be replaced with thumbnails
            self.output_format = "both"  # "html", "pdf", "both"

================================================================================
STEP 2: Update CONFIG dictionary
================================================================================

Add PDF configuration to the CONFIG dictionary:

    CONFIG = {
        # ... existing config sections ...

        'pdf': {
            'enabled': True,
            'auto_generate': True,
            'quality': 'high',
            'page_size': 'A4',
            'include_images': True,
            'include_videos': False,
            'output_format': 'both',
        },

        # ... rest of config ...
    }

================================================================================
STEP 3: Add command-line arguments
================================================================================

In ytbot/cli.py, add command-line arguments for PDF options:

    import argparse

    def add_pdf_arguments(parser):
        '''Add PDF-related command-line arguments'''
        pdf_group = parser.add_argument_group('PDF Options')
        pdf_group.add_argument(
            '--pdf',
            action='store_true',
            default=False,
            help='Generate PDF alongside HTML'
        )
        pdf_group.add_argument(
            '--pdf-only',
            action='store_true',
            default=False,
            help='Generate only PDF, skip HTML'
        )
        pdf_group.add_argument(
            '--no-pdf',
            action='store_true',
            default=False,
            help='Disable PDF generation'
        )

================================================================================
STEP 4: Update download command
================================================================================

Modify the download command to use PDF options:

    async def download_command(url: str, args):
        '''Download content with optional PDF generation'''

        download_service = DownloadService()
        result = await download_service.download_content(url)

        if result.success:
            # Check PDF generation settings
            if not args.no_pdf and (args.pdf or CONFIG['pdf']['auto_generate']):
                pdf_path = await generate_pdf_from_content(result)
                if pdf_path:
                    print(f"PDF generated: {pdf_path}")
                else:
                    print("PDF generation failed")

================================================================================
STEP 5: Environment variable support
================================================================================

Add environment variable support for PDF settings:

    import os

    def load_pdf_config():
        '''Load PDF configuration from environment variables'''

        return {
            'enabled': os.getenv('YTBOT_PDF_ENABLED', 'true').lower() == 'true',
            'auto_generate': os.getenv('YTBOT_PDF_AUTO', 'true').lower() == 'true',
            'quality': os.getenv('YTBOT_PDF_QUALITY', 'high'),
            'page_size': os.getenv('YTBOT_PDF_PAGE_SIZE', 'A4'),
            'include_images': os.getenv('YTBOT_PDF_IMAGES', 'true').lower() == 'true',
            'include_videos': os.getenv('YTBOT_PDF_VIDEOS', 'false').lower() == 'true',
            'output_format': os.getenv('YTBOT_PDF_FORMAT', 'both'),
        }

================================================================================
COMPLETE CONFIGURATION EXAMPLE
================================================================================

Example .env file with PDF settings:

    # PDF Generation Settings
    YTBOT_PDF_ENABLED=true
    YTBOT_PDF_AUTO=true
    YTBOT_PDF_QUALITY=high
    YTBOT_PDF_PAGE_SIZE=A4
    YTBOT_PDF_IMAGES=true
    YTBOT_PDF_VIDEOS=false
    YTBOT_PDF_FORMAT=both

Example YAML configuration (cli-config.yaml):

    pdf:
      enabled: true
      auto_generate: true
      quality: high
      page_size: A4
      include_images: true
      include_videos: false
      output_format: both

================================================================================
USAGE EXAMPLES
================================================================================

1. Enable PDF generation for all downloads:
   $ ytbot download https://twitter.com/user/status/123 --pdf

2. Generate only PDF (no HTML):
   $ ytbot download https://twitter.com/user/status/123 --pdf-only

3. Disable PDF generation:
   $ ytbot download https://twitter.com/user/status/123 --no-pdf

4. Using environment variable:
   $ YTBOT_PDF_ENABLED=true ytbot download https://twitter.com/user/status/123

5. Programmatically:
    from ytbot.core.config import CONFIG

    # Enable PDF generation
    CONFIG['pdf']['enabled'] = True
    CONFIG['pdf']['auto_generate'] = True

    # Generate PDF
    result = await download_service.download_content(url)
    if result.success:
        pdf_path = await generate_pdf(result)

================================================================================
CONFIGURATION PRIORITY
================================================================================

Configuration values are loaded in this order (later overrides earlier):

1. Default values (hardcoded in config.py)
2. YAML configuration file (cli-config.yaml)
3. Environment variables
4. Command-line arguments

================================================================================
AVAILABLE CONFIGURATION OPTIONS
================================================================================

Option              | Type    | Default | Description
--------------------|---------|---------|------------------------------------------
enabled             | bool    | true    | Enable/disable PDF generation
auto_generate       | bool    | true    | Auto-generate PDF after download
quality             | string  | "high"  | PDF quality: low, medium, high
page_size           | string  | "A4"    | Page size: A4, Letter, Legal
include_images      | bool    | true    | Include images in PDF
include_videos      | bool    | false   | Include video thumbnails (always)
output_format       | string  | "both"  | Output format: html, pdf, both

"""

# This file contains documentation for PDF configuration
# Apply these changes to enable configurable PDF generation

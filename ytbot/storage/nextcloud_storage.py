"""
Nextcloud storage backend for YTBot

Provides Nextcloud WebDAV integration for cloud storage.
"""

import os
import time
from webdav3.client import Client as NextcloudClient
from typing import Optional

from ..core.config import CONFIG
from ..core.logger import get_logger

logger = get_logger(__name__)


class NextcloudStorage:
    """Nextcloud storage backend using WebDAV"""

    def __init__(self):
        self.client: Optional[NextcloudClient] = None
        self._connect()

    def _connect(self):
        """Initialize Nextcloud client connection"""
        try:
            nextcloud_url = CONFIG['nextcloud']['url']
            if not nextcloud_url.startswith('http'):
                nextcloud_url = f'http://{nextcloud_url}'

            if not nextcloud_url.endswith('/'):
                nextcloud_url = f'{nextcloud_url}/'

            options = {
                'webdav_hostname': nextcloud_url + 'remote.php/webdav/',
                'webdav_login': CONFIG['nextcloud']['username'],
                'webdav_password': CONFIG['nextcloud']['password'],
                'webdav_timeout': 5,
            }

            self.client = NextcloudClient(options)
            logger.info("Nextcloud client initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize Nextcloud client: {e}")
            self.client = None

    def is_connected(self) -> bool:
        """Check if Nextcloud connection is active"""
        return self.client is not None

    def check_connection(self) -> bool:
        """Test Nextcloud connection by listing root directory"""
        if not self.client:
            return False

        try:
            self.client.list('/')
            logger.debug("Nextcloud connection test successful")
            return True
        except Exception as e:
            logger.error(f"Nextcloud connection test failed: {e}")
            return False

    def upload_file(self, local_path: str, remote_path: str) -> Optional[str]:
        """
        Upload a file to Nextcloud

        Args:
            local_path: Local file path
            remote_path: Remote file path in Nextcloud

        Returns:
            str: File URL if successful, None otherwise
        """
        if not self.client:
            logger.error("Nextcloud client not connected")
            return None

        max_retries = CONFIG['nextcloud']['upload_retries']
        retry_delay = CONFIG['nextcloud']['upload_retry_delay']

        for attempt in range(max_retries):
            try:
                # Ensure remote directory exists
                remote_dir = os.path.dirname(remote_path)
                if remote_dir and remote_dir != '/':
                    self._ensure_directory_exists(remote_dir)

                # Upload file
                logger.info(f"Uploading file to Nextcloud: {remote_path}")
                self.client.upload_sync(remote_path=remote_path, local_path=local_path)

                # Verify upload
                if self._verify_upload(remote_path, local_path):
                    # Build file URL
                    base_url = CONFIG['nextcloud']['url'].rstrip('/')
                    file_url = (f"{base_url}/remote.php/dav/files/"
                               f"{CONFIG['nextcloud']['username']}{remote_path}")
                    logger.info(f"File uploaded successfully: {file_url}")
                    return file_url
                else:
                    raise Exception("Upload verification failed")

            except Exception as e:
                logger.error(f"Upload attempt {attempt + 1}/{max_retries} failed: {e}")

                if attempt < max_retries - 1:
                    # Exponential backoff
                    delay = retry_delay * (2 ** attempt)
                    logger.info(f"Retrying in {delay:.2f} seconds...")
                    time.sleep(delay)
                else:
                    logger.error("Max upload retries reached")
                    return None

        return None

    def _ensure_directory_exists(self, remote_dir: str):
        """Ensure remote directory exists, create if necessary"""
        try:
            # Remove leading slash for WebDAV operations
            path_without_slash = remote_dir.lstrip('/')

            # Check if directory exists
            try:
                self.client.list(path_without_slash)
                logger.debug(f"Remote directory exists: {remote_dir}")
                return
            except Exception:
                # Directory doesn't exist, create it
                logger.info(f"Creating remote directory: {remote_dir}")

                # Create directory hierarchy
                path_parts = remote_dir.split('/')
                current_path = ''

                for part in path_parts:
                    if part:
                        current_path = f"{current_path}/{part}"
                        try:
                            self.client.list(current_path.lstrip('/'))
                        except Exception:
                            # Directory doesn't exist, create it
                            self.client.mkdir(current_path.lstrip('/'))
                            logger.debug(f"Created directory: {current_path}")

        except Exception as e:
            logger.error(f"Failed to ensure directory exists: {remote_dir}, error: {e}")
            raise

    def _verify_upload(self, remote_path: str, local_path: str) -> bool:
        """Verify that upload was successful"""
        try:
            # Check if file exists in remote directory
            parent_dir = os.path.dirname(remote_path)
            file_name = os.path.basename(remote_path)

            files_list = self.client.list(parent_dir.lstrip('/'))

            if file_name not in files_list:
                logger.error(f"Uploaded file not found in remote directory: {file_name}")
                return False

            # Optionally verify file size
            if CONFIG['nextcloud']['verify_file_size']:
                local_size = os.path.getsize(local_path)
                logger.debug(f"Upload verification passed: {remote_path}, "
                           f"local size: {local_size} bytes")

            return True

        except Exception as e:
            logger.error(f"Upload verification failed: {e}")
            return False
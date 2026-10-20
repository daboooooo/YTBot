"""
Nextcloud storage backend for YTBot

Provides Nextcloud WebDAV integration for cloud storage.
"""

import os
import time
from webdav3.client import Client as NextcloudClient
from typing import Optional, Dict, Any

from ..core.config import get_config
from ..core.enhanced_logger import get_logger
from ..utils.common import sanitize_filename

logger = get_logger(__name__)


class NextcloudStorage:
    """Nextcloud storage backend using WebDAV"""

    def __init__(self):
        self.client: Optional[NextcloudClient] = None
        self._connect()

    def _connect(self):
        """Initialize Nextcloud client connection"""
        try:
            nextcloud_url = get_config().nextcloud.url
            if not nextcloud_url.startswith('http'):
                nextcloud_url = f'http://{nextcloud_url}'

            if not nextcloud_url.endswith('/'):
                nextcloud_url = f'{nextcloud_url}/'

            options = {
                'webdav_hostname': nextcloud_url + 'remote.php/webdav/',
                'webdav_login': get_config().nextcloud.username,
                'webdav_password': get_config().nextcloud.password,
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

    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        progress_callback: Optional[Any] = None,
        byte_offset: int = 0,
        total_bytes: Optional[int] = None,
    ) -> Optional[str]:
        """
        Upload a file to Nextcloud

        Args:
            local_path: Local file path
            remote_path: Remote file path in Nextcloud
            progress_callback: Optional sync callback invoked per uploaded
                chunk as ``callback(current, total)`` with global bytes
                (byte_offset added to per-file current; total_bytes is the
                grand total when aggregating a directory upload)
            byte_offset: Bytes already uploaded before this file (for
                aggregating directory uploads into one overall progress)
            total_bytes: Grand total bytes reported to the callback;
                defaults to the size of this single file

        Returns:
            str: File URL if successful, None otherwise
        """
        if not self.client:
            logger.error("Nextcloud client not connected")
            return None

        # Sanitize filename in remote_path to avoid Nextcloud
        # "filename too long" (InvalidPath) errors
        remote_dir = os.path.dirname(remote_path)
        remote_name = os.path.basename(remote_path)
        sanitized_name = sanitize_filename(remote_name)
        if sanitized_name != remote_name:
            logger.warning(
                f"Filename truncated for Nextcloud: "
                f"'{remote_name}' -> '{sanitized_name}'"
            )
        remote_path = f"{remote_dir}/{sanitized_name}"

        max_retries = get_config().nextcloud.upload_retries
        retry_delay = get_config().nextcloud.upload_retry_delay

        for attempt in range(max_retries):
            try:
                # Ensure remote directory exists
                remote_dir = os.path.dirname(remote_path)
                if remote_dir and remote_dir != '/':
                    self._ensure_directory_exists(remote_dir)

                # Upload file
                logger.info(f"Uploading file to Nextcloud: {remote_path}")
                if progress_callback is not None:
                    # Call the low-level API directly: upload_sync's file
                    # branch does not forward `progress` to the client
                    file_size = os.path.getsize(local_path)
                    grand_total = (
                        total_bytes if total_bytes is not None else file_size
                    )

                    def _lib_progress(current: int, total: int) -> None:
                        progress_callback(
                            byte_offset + current, grand_total
                        )

                    self.client.upload_file(
                        remote_path=remote_path,
                        local_path=local_path,
                        progress=_lib_progress,
                    )
                else:
                    self.client.upload_sync(
                        remote_path=remote_path, local_path=local_path
                    )

                # Verify upload
                if self._verify_upload(remote_path, local_path):
                    # Build file URL
                    base_url = get_config().nextcloud.url.rstrip('/')
                    file_url = (
                        f"{base_url}/remote.php/dav/files/"
                        f"{get_config().nextcloud.username}{remote_path}"
                    )
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

    def upload_directory(
        self,
        local_dir: str,
        remote_dir: str,
        progress_callback: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Upload a directory and all its contents to Nextcloud

        Args:
            local_dir: Local directory path
            remote_dir: Remote directory path
            progress_callback: Optional sync callback invoked as
                ``callback(current, total)`` with aggregated bytes across
                all files in the directory

        Returns:
            dict: Upload result with 'success', 'file_url',
                  'uploaded_files', 'errors'
        """
        result = {
            "success": False,
            "file_url": None,
            "uploaded_files": [],
            "errors": []
        }

        if not self.client:
            logger.error("Nextcloud client not connected")
            result["errors"].append("Nextcloud client not connected")
            return result

        if not os.path.isdir(local_dir):
            error_msg = f"Local directory does not exist: {local_dir}"
            logger.error(error_msg)
            result["errors"].append(error_msg)
            return result

        try:
            # Ensure remote directory exists
            self._ensure_directory_exists(remote_dir)

            # Find HTML file for building file_url (optional)
            html_files = [f for f in os.listdir(local_dir) if f.endswith('.html')]
            main_html = html_files[0] if html_files else None

            uploaded_files = []
            errors = []
            total_files = 0

            # Pre-compute total bytes for aggregated progress reporting
            total_bytes = 0
            if progress_callback is not None:
                for root_pre, _, files_pre in os.walk(local_dir):
                    for f in files_pre:
                        try:
                            total_bytes += os.path.getsize(
                                os.path.join(root_pre, f)
                            )
                        except OSError:
                            pass
                progress_callback(0, total_bytes)

            # Bytes of files already processed (for progress aggregation)
            completed_bytes = 0

            # Walk through directory and upload all files
            for root, dirs, files in os.walk(local_dir):
                # Calculate relative path from local_dir
                rel_root = os.path.relpath(root, local_dir)
                if rel_root == '.':
                    rel_root = ''

                # Create corresponding remote directory
                if rel_root:
                    current_remote_dir = f"{remote_dir}/{rel_root}"
                    self._ensure_directory_exists(current_remote_dir)

                # Upload files in current directory
                for file in files:
                    total_files += 1
                    local_file_path = os.path.join(root, file)
                    if rel_root:
                        remote_file_path = f"{remote_dir}/{rel_root}/{file}"
                    else:
                        remote_file_path = f"{remote_dir}/{file}"

                    try:
                        logger.info(
                            f"Uploading: {local_file_path} -> "
                            f"{remote_file_path}"
                        )
                        file_size = 0
                        try:
                            file_size = os.path.getsize(local_file_path)
                        except OSError:
                            pass
                        if progress_callback is not None:
                            self.upload_file(
                                local_path=local_file_path,
                                remote_path=remote_file_path,
                                progress_callback=progress_callback,
                                byte_offset=completed_bytes,
                                total_bytes=total_bytes,
                            )
                        else:
                            self.client.upload_sync(
                                remote_path=remote_file_path,
                                local_path=local_file_path
                            )
                        uploaded_files.append(remote_file_path)
                        logger.info(f"Successfully uploaded: {file}")
                    except Exception as e:
                        error_msg = f"Failed to upload {file}: {e}"
                        logger.error(error_msg)
                        errors.append(error_msg)
                    finally:
                        # Advance the global offset even on failure so the
                        # overall percentage keeps moving
                        completed_bytes += file_size

            # Build file URL - use Nextcloud web UI browse URL
            base_url = get_config().nextcloud.url.rstrip('/')
            file_url = (
                f"{base_url}/apps/files/?dir="
                f"{remote_dir}"
            )

            # Success: all files uploaded, or at least the majority
            # (allow some non-critical files to fail)
            upload_ratio = (
                len(uploaded_files) / total_files if total_files > 0 else 0
            )
            is_success = upload_ratio >= 0.8 or (
                main_html and main_html in [
                    os.path.basename(f) for f in uploaded_files
                ]
            )

            result.update({
                "success": is_success,
                "file_url": file_url,
                "uploaded_files": uploaded_files,
                "errors": errors
            })

            if errors:
                logger.warning(
                    f"Directory upload completed with {len(errors)} "
                    f"errors ({len(uploaded_files)}/{total_files} files)"
                )
            else:
                logger.info(
                    f"Directory upload completed successfully: {file_url}"
                )

            return result

        except Exception as e:
            error_msg = f"Directory upload failed: {e}"
            logger.error(error_msg)
            result["errors"].append(error_msg)
            return result

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
            if get_config().nextcloud.verify_file_size:
                local_size = os.path.getsize(local_path)
                logger.debug(
                    f"Upload verification passed: {remote_path}, "
                    f"local size: {local_size} bytes"
                )

            return True

        except Exception as e:
            logger.error(f"Upload verification failed: {e}")
            return False

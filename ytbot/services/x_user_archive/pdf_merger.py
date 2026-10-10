"""Merged archive book PDF (spec 11.5).

Front matter (cover + table of contents) is rendered from a
self-contained inline-CSS HTML through the existing
``pdf_converter.convert_html_content_to_pdf`` pipeline; per-post PDFs
are appended in chronological order with pypdf, and each post gets a
flat PDF outline bookmark at its start page.

The merged book is a DERIVED artifact: it is rebuilt from whatever
per-post PDFs currently exist, atomically replaces the previous book,
and any failure here never touches per-post archives. pypdf is imported
lazily so single-post archiving works without the dependency.
"""

import html
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

from ...core.enhanced_logger import get_logger
from .manifest import sort_key
from .models import (
    DownloadStatus,
    EnumStatus,
)

logger = get_logger(__name__)

# Records present on disk and eligible to contribute a per-post PDF
_ARCHIVED_STATUSES = (DownloadStatus.DOWNLOADED, DownloadStatus.SKIPPED)
_FAILED_STATUSES = (
    DownloadStatus.PERMANENT_ERROR,
    DownloadStatus.RETRYABLE_ERROR,
)

FrontRenderer = Callable[[str, str], Awaitable[Optional[str]]]


@dataclass
class MergeResult:
    status: str
    output_path: Optional[str] = None
    included_posts: int = 0
    downloadable_posts: int = 0
    missing_pdf: List[str] = field(default_factory=list)
    skipped_invalid: List[str] = field(default_factory=list)
    pages: int = 0
    message: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "path": self.output_path,
            "included_posts": self.included_posts,
            "downloadable_posts": self.downloadable_posts,
            "missing_pdf": list(self.missing_pdf),
            "skipped_invalid": list(self.skipped_invalid),
            "pages": self.pages,
            "message": self.message,
        }


def default_output_name(
    handle: str, window_start: datetime, window_end: datetime
) -> str:
    return (
        f"{handle}_{window_start.strftime('%Y%m%d')}"
        f"-{window_end.strftime('%Y%m%d')}.pdf"
    )


def _display_title(record) -> str:
    title = (record.title or "").strip()
    if title:
        return re.sub(r"\s+", " ", title)
    prefix = "article" if record.content_type == "article" else "post"
    return f"{prefix}_{record.tweet_id}"


def _bookmark_title(record) -> str:
    return (
        f"{record.published_at.strftime('%Y-%m-%d')} "
        f"{_display_title(record)}"
    )


def build_front_matter_html(
    *,
    handle: str,
    window_start: datetime,
    window_end: datetime,
    generated_at: datetime,
    archive_root: str,
    enum_status: str,
    discovered: int,
    downloadable: int,
    included: int,
    failed: int,
    missing_pdf: int,
    toc_rows: List[str],
) -> str:
    """Cover + TOC page(s), fully inline, no external resources."""
    start_d = window_start.strftime("%Y-%m-%d")
    end_d = window_end.strftime("%Y-%m-%d")
    cover_rows = [
        ("归档账号", f"@{html.escape(handle)}"),
        ("时间窗口", f"{start_d} ~ {end_d}（半开区间）"),
        ("生成时间", generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")),
        ("发现帖子", str(discovered)),
        ("已收录", f"{included} / 可下载 {downloadable}"),
        ("下载失败", str(failed)),
        ("无单篇 PDF", str(missing_pdf)),
        ("枚举完整性", enum_status),
        ("归档根目录", html.escape(str(archive_root))),
    ]
    rows_html = "\n".join(
        f'<tr><th>{html.escape(k)}</th><td>{v}</td></tr>'
        for k, v in cover_rows
    )
    if toc_rows:
        toc_html = "\n".join(f"<li>{row}</li>" for row in toc_rows)
    else:
        toc_html = "<li>（本次没有可收录的单篇 PDF）</li>"
    gap_note = (
        f'<p class="note">注意：{missing_pdf} 篇已归档帖子无可用单篇 PDF，'
        f'见各自目录下的 HTML；{failed} 篇下载失败未收录。可补下载后用 '
        f'--resume 重建本合订本。</p>'
        if (missing_pdf or failed) else ""
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>@{html.escape(handle)} X 帖子合订</title>
<style>
  @page {{ size: A4; margin: 18mm 16mm; }}
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei",
         sans-serif; color: #1a1a1a; font-size: 11pt; line-height: 1.55; }}
  h1 {{ font-size: 22pt; margin: 0 0 4pt; }}
  h2 {{ font-size: 15pt; margin: 24pt 0 10pt;
        border-bottom: 2px solid #444; padding-bottom: 4pt; }}
  .sub {{ color: #666; margin-bottom: 20pt; }}
  table.cover {{ border-collapse: collapse; width: 100%; }}
  table.cover th {{ text-align: left; width: 110px; padding: 4pt 8pt;
                    color: #555; font-weight: 600; vertical-align: top; }}
  table.cover td {{ padding: 4pt 8pt; }}
  .note {{ background: #fff7e6; border: 1px solid #f0d8a8; padding: 8pt;
           border-radius: 4pt; font-size: 10pt; }}
  ol.toc {{ padding-left: 22pt; }}
  ol.toc li {{ margin: 3pt 0; }}
  .toc-no-pdf {{ color: #b06a00; }}
  .pagebreak {{ page-break-before: always; }}
</style>
</head>
<body>
  <h1>@{html.escape(handle)} 的 X 帖子合订</h1>
  <div class="sub">按发布时间升序 · ytbot x-user-archive</div>
  <table class="cover">
    {rows_html}
  </table>
  {gap_note}
  <div class="pagebreak"></div>
  <h2>目录</h2>
  <ol class="toc">
    {toc_html}
  </ol>
</body>
</html>"""


def _toc_row(index: int, record, state: str) -> str:
    date = record.published_at.strftime("%Y-%m-%d")
    title = html.escape(_display_title(record))
    if state == "ok":
        return f"{index}. {date} {title}"
    return (
        f'{index}. {date} {title} '
        f'<span class="toc-no-pdf">（无 PDF，见同目录 HTML）</span>'
    )


async def generate_merged_pdf(
    *,
    handle: str,
    records: list,
    output_path,
    window_start: datetime,
    window_end: datetime,
    enum_status: str = EnumStatus.PARTIAL,
    archive_root: Optional[str] = None,
    now: Optional[datetime] = None,
    front_renderer: Optional[FrontRenderer] = None,
) -> MergeResult:
    """Rebuild the merged book from the current on-disk per-post PDFs.

    Records outside archived/failed states (e.g. pending) are ignored.
    """
    try:
        from pypdf import PdfReader, PdfWriter
        from pypdf.errors import PdfReadError
    except ImportError:
        logger.warning("pypdf not installed; merged PDF skipped")
        return MergeResult(
            status="skipped",
            message=(
                "合订大 PDF 需要 pypdf，请运行 `pip install pypdf` 后用 "
                "--resume 补生成（单篇归档已完成，无需重新下载）"
            ),
        )

    ordered = sorted(records, key=sort_key)
    archived = [
        r for r in ordered if r.status in _ARCHIVED_STATUSES
    ]
    failed = [r for r in ordered if r.status in _FAILED_STATUSES]

    output_path = Path(output_path)
    work_dir = output_path.parent
    work_dir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.utcnow()

    # -- validate each per-post PDF --------------------------------------
    valid_pairs: List[tuple] = []      # (record, Path)
    missing_pdf: List[str] = []
    skipped_invalid: List[str] = []
    for rec in archived:
        if not rec.archive_path:
            missing_pdf.append(rec.tweet_id)
            continue
        post_dir = Path(rec.archive_path)
        pdfs = sorted(post_dir.glob("*.pdf")) if post_dir.is_dir() else []
        pdfs = [p for p in pdfs if p.is_file() and p.stat().st_size > 0]
        if not pdfs:
            missing_pdf.append(rec.tweet_id)
            continue
        candidate = pdfs[0]
        try:
            probe = PdfReader(str(candidate))
            if len(probe.pages) <= 0:
                raise PdfReadError("zero pages")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "skip unreadable per-post PDF %s: %s", candidate, exc
            )
            skipped_invalid.append(rec.tweet_id)
            continue
        valid_pairs.append((rec, candidate))

    # -- render front matter ---------------------------------------------
    toc_rows = []
    valid_ids = {r.tweet_id for r, _ in valid_pairs}
    index = 0
    for rec in archived:
        index += 1
        state = "ok" if rec.tweet_id in valid_ids else "gap"
        toc_rows.append(_toc_row(index, rec, state))

    cover_html = build_front_matter_html(
        handle=handle,
        window_start=window_start,
        window_end=window_end,
        generated_at=now,
        archive_root=archive_root or str(work_dir),
        enum_status=enum_status,
        discovered=len(ordered),
        downloadable=len(archived),
        included=len(valid_pairs),
        failed=len(failed),
        missing_pdf=len(missing_pdf) + len(skipped_invalid),
        toc_rows=toc_rows,
    )

    fd, front_path = tempfile.mkstemp(
        prefix="xmerge_front_", suffix=".pdf", dir=str(work_dir)
    )
    os.close(fd)
    os.unlink(front_path)  # renderer writes the path itself

    if front_renderer is None:
        from ..pdf_converter import convert_html_content_to_pdf
        front_renderer = convert_html_content_to_pdf

    rendered = None
    try:
        rendered = await front_renderer(cover_html, front_path)
        if not rendered or not Path(front_path).is_file() or \
                Path(front_path).stat().st_size == 0:
            return MergeResult(
                status="failed",
                downloadable_posts=len(archived),
                missing_pdf=missing_pdf,
                skipped_invalid=skipped_invalid,
                message="封面/目录 PDF 渲染失败",
            )

        writer = PdfWriter()
        writer.append(front_path)
        writer.add_metadata(
            {
                "/Title": (
                    f"@{handle} X 帖子合订 "
                    f"{window_start.strftime('%Y%m%d')}-"
                    f"{window_end.strftime('%Y%m%d')}"
                ),
                "/Author": handle,
            }
        )

        for rec, pdf in valid_pairs:
            offset = len(writer.pages)
            writer.append(str(pdf))
            writer.add_outline_item(_bookmark_title(rec), offset)

        fd2, tmp_out = tempfile.mkstemp(
            prefix=".xmerge-", suffix=".pdf", dir=str(work_dir)
        )
        os.close(fd2)
        os.unlink(tmp_out)
        try:
            with open(tmp_out, "wb") as fh:
                writer.write(fh)

            # -- verify before commit ------------------------------------
            verify = PdfReader(tmp_out)
            pages = len(verify.pages)
            outline_count = sum(
                1 for item in _iter_outline(verify.outline)
            )
            size = os.path.getsize(tmp_out)
            if pages <= 0 or size == 0 or \
                    outline_count != len(valid_pairs):
                raise ValueError(
                    f"merged PDF verification failed: pages={pages}, "
                    f"size={size}, outlines={outline_count}, "
                    f"expected={len(valid_pairs)}"
                )
            os.replace(tmp_out, output_path)
        finally:
            if os.path.exists(tmp_out):
                os.unlink(tmp_out)
    finally:
        if os.path.exists(front_path):
            try:
                os.unlink(front_path)
            except OSError:
                pass

    logger.info(
        "merged book: %s posts, %s pages -> %s",
        len(valid_pairs), pages, output_path,
    )
    return MergeResult(
        status="success",
        output_path=str(output_path),
        included_posts=len(valid_pairs),
        downloadable_posts=len(archived),
        missing_pdf=missing_pdf,
        skipped_invalid=skipped_invalid,
        pages=pages,
    )


def _iter_outline(items):
    """Yield leaf outline items (pypdf nests sub-lists)."""
    for item in items:
        if isinstance(item, list):
            yield from _iter_outline(item)
        else:
            yield item

"""Unit tests for the merged archive book (spec 17.14).

Real micro-PDFs are generated locally with pypdf (no network, no
Chrome); the front-matter renderer is injected.
"""

import inspect
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from ytbot.services.x_user_archive import pdf_merger
from ytbot.services.x_user_archive.models import (
    ContentKind,
    ContentRecord,
    DownloadStatus,
    EnumStatus,
)
from ytbot.services.x_user_archive.pdf_merger import (
    default_output_name,
    generate_merged_pdf,
)

UTC = timezone.utc
SID_A = "1893214234567890123"
SID_B = "1894000000000000456"
SID_C = "1895000000000000789"
SID_D = "1896000000000000999"

WINDOW_START = datetime(2021, 10, 10, tzinfo=UTC)
WINDOW_END = datetime(2026, 10, 10, tzinfo=UTC)
GEN_AT = datetime(2026, 10, 10, 12, 0, 0)


def write_pdf(path: Path, pages: int = 1) -> Path:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=297, height=420)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def make_record(tweet_id, status, *, title=None, post_dir=None,
                with_pdf=True, corrupt=False):
    from ytbot.services.x_user_archive.models import snowflake_to_datetime
    published = snowflake_to_datetime(tweet_id)
    archive_path = None
    if post_dir is not None:
        post_dir.mkdir(parents=True, exist_ok=True)
        (post_dir / f"tweet_{tweet_id}.html").write_text("<html/>")
        if with_pdf:
            pdf = post_dir / f"tweet_{tweet_id}.pdf"
            if corrupt:
                pdf.write_bytes(b"not a pdf body")
            else:
                write_pdf(pdf, pages=1)
        archive_path = str(post_dir)
    return ContentRecord(
        tweet_id=tweet_id,
        author_id="elonmusk",
        status_author="elonmusk",
        post_url=f"https://x.com/elonmusk/status/{tweet_id}",
        published_at=published,
        discovered_at=published,
        content_type=ContentKind.POST,
        title=title,
        status=status,
        archive_path=archive_path,
    )


def make_front_renderer(pages=2):
    captured = {}

    async def render(html_content, output_path):
        captured["html"] = html_content
        captured["path"] = output_path
        write_pdf(Path(output_path), pages=pages)
        return output_path

    render.captured = captured
    return render


def run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "x_archive" / "elonmusk"
    content = root / "content"
    out = root / default_output_name("elonmusk", WINDOW_START, WINDOW_END)
    return root, content, out


def _three_records(content):
    return [
        make_record(SID_A, DownloadStatus.DOWNLOADED, title="Alpha post",
                    post_dir=content / "2025" / "02" / "a"),
        make_record(SID_B, DownloadStatus.DOWNLOADED, title="Beta post",
                    post_dir=content / "2025" / "03" / "b"),
        make_record(SID_C, DownloadStatus.DOWNLOADED, title="Gamma post",
                    post_dir=content / "2025" / "03" / "c"),
    ]


class TestMergeHappyPath:
    def test_merge_pages_outlines_and_metadata(self, workspace):
        root, content, out = workspace
        records = _three_records(content)
        renderer = make_front_renderer(pages=2)

        result = run(generate_merged_pdf(
            handle="elonmusk", records=records, output_path=out,
            window_start=WINDOW_START, window_end=WINDOW_END,
            enum_status=EnumStatus.COMPLETE,
            archive_root=str(root), now=GEN_AT,
            front_renderer=renderer,
        ))

        assert result.status == "success"
        assert result.included_posts == 3
        assert result.downloadable_posts == 3
        assert result.pages == 5
        assert Path(result.output_path) == out
        assert out.exists()
        assert not list(out.parent.glob(".xmerge-*"))
        assert not list(out.parent.glob("xmerge_front_*"))

        reader = PdfReader(str(out))
        titles = [item.title for item in reader.outline]
        # chronological order, front matter occupies pages 0-1
        assert titles == [
            f"{r.published_at:%Y-%m-%d} {r.title}" for r in
            sorted(records, key=lambda r: (r.published_at, int(r.tweet_id)))
        ]
        # each outline lands on its post's first page
        page_indices = [
            reader.get_destination_page_number(item)
            for item in reader.outline
        ]
        assert page_indices == [2, 3, 4]
        meta = reader.metadata
        assert meta.author == "elonmusk"
        assert meta.title == "@elonmusk X 帖子合订 20211010-20261010"

    def test_cover_and_toc_content_matches_manifest(self, workspace):
        root, content, out = workspace
        records = _three_records(content)
        renderer = make_front_renderer()
        run(generate_merged_pdf(
            handle="elonmusk", records=records, output_path=out,
            window_start=WINDOW_START, window_end=WINDOW_END,
            enum_status=EnumStatus.COMPLETE, now=GEN_AT,
            front_renderer=renderer,
        ))
        doc = renderer.captured["html"]
        assert "@elonmusk" in doc
        assert "2021-10-10 ~ 2026-10-10" in doc
        assert "complete" in doc
        assert "Alpha post" in doc and "Gamma post" in doc
        # no gap note when everything is included
        assert "无可用单篇 PDF" not in doc

    def test_idempotent_rebuild_replaces_and_no_temp_left(self, workspace):
        root, content, out = workspace
        records = _three_records(content)
        renderer = make_front_renderer()

        first = run(generate_merged_pdf(
            handle="elonmusk", records=records, output_path=out,
            window_start=WINDOW_START, window_end=WINDOW_END,
            now=GEN_AT, front_renderer=renderer,
        ))
        size_1 = out.stat().st_size
        second = run(generate_merged_pdf(
            handle="elonmusk", records=records, output_path=out,
            window_start=WINDOW_START, window_end=WINDOW_END,
            now=GEN_AT, front_renderer=renderer,
        ))

        assert first.status == second.status == "success"
        assert first.pages == second.pages == 5
        assert out.exists() and out.stat().st_size == size_1
        assert not list(out.parent.glob(".xmerge-*"))

    def test_resume_added_post_makes_book_complete(self, workspace):
        root, content, out = workspace
        records = _three_records(content)
        renderer = make_front_renderer()
        run(generate_merged_pdf(
            handle="elonmusk", records=records, output_path=out,
            window_start=WINDOW_START, window_end=WINDOW_END,
            front_renderer=renderer,
        ))

        records.append(make_record(
            SID_D, DownloadStatus.DOWNLOADED, title="Delta post",
            post_dir=content / "2025" / "05" / "d",
        ))
        result = run(generate_merged_pdf(
            handle="elonmusk", records=records, output_path=out,
            window_start=WINDOW_START, window_end=WINDOW_END,
            front_renderer=renderer,
        ))

        assert result.included_posts == 4
        assert result.pages == 6
        assert len(PdfReader(str(out)).outline) == 4


class TestMissingAndCorrupt:
    def test_missing_pdf_listed_in_toc_and_counts(self, workspace):
        root, content, out = workspace
        r_ok = make_record(
            SID_A, DownloadStatus.DOWNLOADED, title="Has PDF",
            post_dir=content / "a", with_pdf=True,
        )
        r_no = make_record(
            SID_B, DownloadStatus.DOWNLOADED, title="HTML only",
            post_dir=content / "b", with_pdf=False,
        )
        renderer = make_front_renderer()

        result = run(generate_merged_pdf(
            handle="elonmusk", records=[r_ok, r_no], output_path=out,
            window_start=WINDOW_START, window_end=WINDOW_END,
            now=GEN_AT, front_renderer=renderer,
        ))

        assert result.status == "success"
        assert result.included_posts == 1
        assert result.missing_pdf == [SID_B]
        doc = renderer.captured["html"]
        assert "HTML only" in doc
        assert "无 PDF，见同目录 HTML" in doc
        assert "已收录" in doc

    def test_corrupt_pdf_skipped_counted_and_not_merged(self, workspace):
        root, content, out = workspace
        r_ok = make_record(
            SID_A, DownloadStatus.DOWNLOADED, title="Good",
            post_dir=content / "a",
        )
        r_bad = make_record(
            SID_B, DownloadStatus.DOWNLOADED, title="Broken",
            post_dir=content / "b", corrupt=True,
        )
        r_zero = make_record(
            SID_C, DownloadStatus.DOWNLOADED, title="Empty PDF",
            post_dir=content / "c",
        )
        (Path(r_zero.archive_path) / f"tweet_{SID_C}.pdf").write_bytes(
            b""
        )
        renderer = make_front_renderer()

        result = run(generate_merged_pdf(
            handle="elonmusk", records=[r_ok, r_bad, r_zero],
            output_path=out, window_start=WINDOW_START,
            window_end=WINDOW_END, front_renderer=renderer,
        ))

        assert result.status == "success"
        assert result.included_posts == 1
        assert result.skipped_invalid == [SID_B]
        assert result.missing_pdf == [SID_C]
        reader = PdfReader(str(out))
        assert len(reader.outline) == 1
        assert reader.outline[0].title.endswith("Good")

    def test_failed_records_counted_on_cover_not_merged(self, workspace):
        root, content, out = workspace
        r_ok = make_record(
            SID_A, DownloadStatus.DOWNLOADED, title="Good",
            post_dir=content / "a",
        )
        r_fail = make_record(SID_B, DownloadStatus.PERMANENT_ERROR)
        renderer = make_front_renderer()

        result = run(generate_merged_pdf(
            handle="elonmusk", records=[r_ok, r_fail], output_path=out,
            window_start=WINDOW_START, window_end=WINDOW_END,
            now=GEN_AT, front_renderer=renderer,
        ))

        assert result.status == "success"
        assert result.downloadable_posts == 1
        doc = renderer.captured["html"]
        # TOC lists archived posts only (failed one has no archive dir)
        assert "Good" in doc
        assert ">1<" in doc  # failed count row


class TestFailureModes:
    def test_front_render_failure_returns_failed(self, workspace):
        root, content, out = workspace
        records = _three_records(content)

        async def broken_renderer(html_content, output_path):
            return None

        result = run(generate_merged_pdf(
            handle="elonmusk", records=records, output_path=out,
            window_start=WINDOW_START, window_end=WINDOW_END,
            front_renderer=broken_renderer,
        ))

        assert result.status == "failed"
        assert not out.exists()

    def test_missing_pypdf_returns_skipped_with_hint(
        self, workspace, monkeypatch
    ):
        root, content, out = workspace
        records = _three_records(content)
        monkeypatch.setitem(sys.modules, "pypdf", None)

        result = run(generate_merged_pdf(
            handle="elonmusk", records=records, output_path=out,
            window_start=WINDOW_START, window_end=WINDOW_END,
        ))

        assert result.status == "skipped"
        assert "pip install pypdf" in result.message
        assert not out.exists()
        # single-post archives remain untouched
        for r in records:
            assert Path(r.archive_path).is_dir()

    def test_pypdf_import_is_lazy_not_module_level(self):
        src = inspect.getsource(pdf_merger)
        # pypdf must only be imported inside the function body
        for line in src.splitlines():
            if "import pypdf" in line or "from pypdf" in line:
                assert line.startswith((" ", "\t"))

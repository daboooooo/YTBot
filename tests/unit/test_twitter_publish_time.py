"""Publish-time tests for Twitter/X rendering and handler metadata.

The rendered "发布时间" / ContentInfo.upload_date must be the author's post
time scraped from the tweet's ``<time datetime>`` (ISO UTC), never the
download time. Previously the renderers read a ``publish_time`` key that
nobody sets and silently fell back to ``datetime.now()``.
"""

import datetime

from ytbot.utils.common import format_iso_timestamp
from ytbot.platforms.twitter.handler import TwitterHandler, _iso_to_date
from ytbot.platforms.twitter.rendering.html_renderer import render_html
from ytbot.platforms.twitter.rendering.markdown import generate_markdown

POST_ISO = "2024-06-15T12:34:56.000Z"
URL = "https://x.com/elonmusk/status/1801234567890123456"


def _base_result(**overrides):
    result = {
        "post_type": "regular",
        "url": URL,
        "content": "hello world",
        "timestamp": POST_ISO,
        "author": "@elonmusk",
    }
    result.update(overrides)
    return result


# --------------------------------------------------------------------------
# format_iso_timestamp
# --------------------------------------------------------------------------

def test_format_iso_timestamp_zulu():
    assert format_iso_timestamp(POST_ISO) == "2024-06-15 12:34:56"


def test_format_iso_timestamp_date_only():
    assert format_iso_timestamp(POST_ISO, fmt="%Y-%m-%d") == "2024-06-15"


def test_format_iso_timestamp_empty_uses_default():
    assert format_iso_timestamp("", default="未知") == "未知"
    assert format_iso_timestamp(None) == ""


def test_format_iso_timestamp_passes_through_relative_text():
    # DOM textContent fallback ("2h", "Jun 15") is real data, keep it
    assert format_iso_timestamp("2h") == "2h"


# --------------------------------------------------------------------------
# HTML / Markdown renderers
# --------------------------------------------------------------------------

def test_render_html_uses_post_time_not_download_time():
    html = render_html(_base_result())
    assert "发布时间: 2024-06-15 12:34:56" in html


def test_render_html_unknown_time_is_not_today():
    html = render_html(_base_result(timestamp=""))
    assert "发布时间: 未知" in html
    today_prefix = datetime.datetime.now().strftime("%Y-%m-%d")
    assert f"发布时间: {today_prefix}" not in html


def test_render_html_publish_time_overrides_timestamp():
    html = render_html(_base_result(publish_time="2023-01-02T03:04:05+00:00"))
    assert "发布时间: 2023-01-02 03:04:05" in html


def test_render_html_thread_post_time_is_formatted():
    result = _base_result(is_thread=True, thread_content={
        "thread_posts": [
            {"content": "first", "author": "@elonmusk", "timestamp": POST_ISO},
        ]
    })
    html = render_html(result)
    assert "thread-post-time" in html
    assert "2024-06-15T12:34:56.000Z" not in html
    assert "2024-06-15 12:34:56" in html


def test_generate_markdown_uses_post_time():
    md = generate_markdown(_base_result())
    assert "> 时间: 2024-06-15 12:34:56" in md


def test_generate_markdown_unknown_time_is_not_today():
    md = generate_markdown(_base_result(timestamp=""))
    assert "> 时间: 未知" in md


# --------------------------------------------------------------------------
# handler metadata
# --------------------------------------------------------------------------

def test_iso_to_date():
    assert _iso_to_date(POST_ISO) == "2024-06-15"
    assert _iso_to_date("") is None
    assert _iso_to_date(None) is None
    assert _iso_to_date("2h") is None


def _bare_handler():
    """Build without running __init__ (avoids storage side effects)."""
    return TwitterHandler.__new__(TwitterHandler)


def test_generate_title_empty_content_uses_post_date():
    h = _bare_handler()
    title = h.generate_title("regular", "", "@elonmusk", [], False,
                             timestamp=POST_ISO)
    assert title == "@elonmusk - 2024-06-15"


def test_generate_title_empty_content_without_time_has_no_date():
    h = _bare_handler()
    title = h.generate_title("regular", "", "@elonmusk", [], False,
                             timestamp="")
    assert title == "@elonmusk"
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    assert today not in title


class _FakeExtractor:
    def __init__(self, result):
        self._result = result

    async def scrape_tweet(self, url):
        return self._result


async def test_get_content_info_upload_date_is_post_date():
    h = _bare_handler()
    h.extractor = _FakeExtractor(_base_result(success=True, formats=[]))
    info = await h.get_content_info(URL)
    assert info is not None
    assert info.upload_date == "2024-06-15"


async def test_get_content_info_upload_date_none_without_timestamp():
    h = _bare_handler()
    h.extractor = _FakeExtractor(
        _base_result(success=True, formats=[], timestamp="")
    )
    info = await h.get_content_info(URL)
    assert info is not None
    assert info.upload_date is None

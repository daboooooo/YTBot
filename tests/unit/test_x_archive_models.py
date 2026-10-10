"""Unit tests for x_user_archive models: snowflake, windows, records."""

from datetime import datetime, timezone

import pytest

from ytbot.services.x_user_archive.models import (
    TWITTER_SNOWFLAKE_EPOCH_MS,
    ContentKind,
    ContentRecord,
    DownloadStatus,
    build_record,
    classify_target,
    in_window,
    is_in_scope,
    normalize_handle,
    parse_iso_utc,
    resolve_time_window,
    snowflake_to_datetime,
)
from ytbot.services.x_user_archive.manifest import dedupe_merge, sort_key

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Snowflake
# ---------------------------------------------------------------------------

class TestSnowflake:
    def test_known_early_id(self):
        # id 20 -> raw ms 0 -> exactly the Twitter epoch
        dt = snowflake_to_datetime(20)
        assert dt == datetime(2010, 11, 4, 1, 42, 54, 657000, tzinfo=UTC)

    def test_round_trip_with_known_timestamp(self):
        # Pick a ms value and build the id carrying it
        ms = 1700000000000
        tweet_id = (ms - TWITTER_SNOWFLAKE_EPOCH_MS) << 22
        dt = snowflake_to_datetime(tweet_id)
        assert dt == datetime.fromtimestamp(ms / 1000, tz=UTC)

    def test_millisecond_precision_within_one_ms_bucket(self):
        # ids differing only by sequence share the same millisecond
        base = (1700000000000 - TWITTER_SNOWFLAKE_EPOCH_MS) << 22
        assert snowflake_to_datetime(base) == snowflake_to_datetime(base + 4095)

    @pytest.mark.parametrize("bad", ["", "abc", "-1", "0", "12a34", None, True])
    def test_invalid_ids_raise(self, bad):
        with pytest.raises(ValueError):
            snowflake_to_datetime(bad)

    def test_future_id_raises(self):
        # year ~3020
        huge = ((32851248600000 - TWITTER_SNOWFLAKE_EPOCH_MS) << 22) + 1
        with pytest.raises(ValueError):
            snowflake_to_datetime(huge)


# ---------------------------------------------------------------------------
# ISO / window
# ---------------------------------------------------------------------------

class TestTimeParsing:
    def test_z_suffix(self):
        assert parse_iso_utc("2025-01-01T00:00:00Z") == datetime(
            2025, 1, 1, tzinfo=UTC
        )

    def test_naive_assumed_utc(self):
        assert parse_iso_utc("2025-01-01T08:00:00") == datetime(
            2025, 1, 1, 8, tzinfo=UTC
        )

    def test_offset_converted(self):
        assert parse_iso_utc("2025-01-01T08:00:00+08:00") == datetime(
            2025, 1, 1, 0, tzinfo=UTC
        )


class TestTimeWindow:
    def test_default_five_calendar_years(self):
        now = datetime(2026, 10, 10, 15, 30, tzinfo=UTC)
        start, end = resolve_time_window(now, history_years=5)
        assert start == datetime(2021, 10, 10, 15, 30, tzinfo=UTC)
        assert end == now

    def test_leap_day_falls_back_to_feb_28(self):
        now = datetime(2024, 2, 29, 12, 0, tzinfo=UTC)
        start, _ = resolve_time_window(now, history_years=5)
        assert start == datetime(2019, 2, 28, 12, 0, tzinfo=UTC)

    def test_explicit_start_overrides_history_years(self):
        now = datetime(2026, 10, 10, tzinfo=UTC)
        start, end = resolve_time_window(
            now, history_years=5, start_at="2025-06-01T00:00:00Z"
        )
        assert start == datetime(2025, 6, 1, tzinfo=UTC)
        assert end == now

    def test_explicit_end_anchors_default_start(self):
        now = datetime(2026, 10, 10, tzinfo=UTC)
        start, end = resolve_time_window(
            now, history_years=2, end_at="2024-01-01T00:00:00Z"
        )
        assert end == datetime(2024, 1, 1, tzinfo=UTC)
        assert start == datetime(2022, 1, 1, tzinfo=UTC)

    def test_empty_window_raises(self):
        now = datetime(2026, 10, 10, tzinfo=UTC)
        with pytest.raises(ValueError):
            resolve_time_window(
                now, start_at="2026-11-01T00:00:00Z",
                end_at="2026-01-01T00:00:00Z",
            )

    def test_start_equals_end_raises(self):
        now = datetime(2026, 10, 10, tzinfo=UTC)
        with pytest.raises(ValueError):
            resolve_time_window(
                now, start_at="2025-01-01T00:00:00Z",
                end_at="2025-01-01T00:00:00Z",
            )

    def test_negative_history_raises(self):
        with pytest.raises(ValueError):
            resolve_time_window(datetime.now(UTC), history_years=-1)

    def test_half_open_boundaries(self):
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 2, 1, tzinfo=UTC)
        assert in_window(start, start, end)
        assert not in_window(end, start, end)
        assert in_window(
            datetime(2025, 1, 15, tzinfo=UTC), start, end
        )


# ---------------------------------------------------------------------------
# Target parsing
# ---------------------------------------------------------------------------

class TestTargetParsing:
    @pytest.mark.parametrize("raw,expected", [
        ("@elonmusk", "elonmusk"),
        ("ElonMusk", "elonmusk"),
        ("  @NASA  ", "nasa"),
    ])
    def test_normalize_handle(self, raw, expected):
        assert normalize_handle(raw) == expected

    @pytest.mark.parametrize("bad", ["", "has space", "a" * 16, "@"])
    def test_bad_handle(self, bad):
        with pytest.raises(ValueError):
            normalize_handle(bad)

    @pytest.mark.parametrize("raw,kind,handle", [
        ("@elonmusk", "handle", "elonmusk"),
        ("elonmusk", "handle", "elonmusk"),
        ("https://x.com/elonmusk", "profile_url", "elonmusk"),
        ("https://twitter.com/elonmusk", "profile_url", "elonmusk"),
        ("http://mobile.twitter.com/elonmusk", "profile_url", "elonmusk"),
        ("https://x.com/elonmusk/with_replies", "profile_url", "elonmusk"),
        (
            "https://x.com/elonmusk/status/1234567890",
            "status_url", "elonmusk",
        ),
        (
            "https://x.com/elonmusk/status/1234567890/photo/1",
            "status_url", "elonmusk",
        ),
    ])
    def test_classify(self, raw, kind, handle):
        assert classify_target(raw) == (kind, handle)

    def test_classify_rejects_non_x_url(self):
        with pytest.raises(ValueError):
            classify_target("https://example.com/foo")

    def test_classify_rejects_empty(self):
        with pytest.raises(ValueError):
            classify_target("   ")


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

def _id_for(ms: int) -> str:
    return str((ms - TWITTER_SNOWFLAKE_EPOCH_MS) << 22)


class TestContentRecord:
    def test_build_and_roundtrip(self):
        rec = build_record(
            _id_for(1700000000000), "elonmusk",
            is_pinned=True, content_type=ContentKind.POST,
        )
        assert rec.post_url == (
            f"https://x.com/elonmusk/status/{_id_for(1700000000000)}"
        )
        assert rec.status == DownloadStatus.PENDING
        data = rec.to_dict()
        again = ContentRecord.from_dict(data)
        assert again.key == rec.key
        assert again.published_at == rec.published_at
        assert again.is_pinned is True
        assert again.published_at.tzinfo == UTC

    def test_repost_uses_original_author_in_url(self):
        # Probe-verified: repost permalinks point at the original author
        rec = build_record(
            _id_for(1700000000000), "elonmusk",
            status_author="spacex", is_repost=True,
        )
        assert rec.author_id == "elonmusk"
        assert rec.status_author == "spacex"
        assert rec.post_url.endswith(f"/spacex/status/{rec.tweet_id}")

    def test_validate_rejects_bad_post_url(self):
        rec = build_record(_id_for(1700000000000), "elonmusk")
        rec.post_url = "https://x.com/nasa/status/999"
        with pytest.raises(ValueError):
            rec.validate()

    def test_validate_rejects_non_normalized_author(self):
        # build_record normalizes inputs; validation must still catch a
        # record constructed with a non-normalized owner
        rec = build_record(_id_for(1700000000000), "elonmusk")
        rec.author_id = "ElonMusk"
        with pytest.raises(ValueError):
            rec.validate()

    def test_scope_rules(self):
        assert is_in_scope("elonmusk", "elonmusk", False)
        # repost of another user's tweet is in scope
        assert is_in_scope("spacex", "elonmusk", True)
        # conversation-context tweet without repost marker is not
        assert not is_in_scope("teslayoda", "elonmusk", False)


# ---------------------------------------------------------------------------
# Dedup / sort
# ---------------------------------------------------------------------------

class TestDedupeSort:
    def _rec(self, idx_ms, tweet_seq=0, **kw):
        tweet_id = str((idx_ms - TWITTER_SNOWFLAKE_EPOCH_MS) << 22 | tweet_seq)
        return build_record(tweet_id, "elonmusk", **kw)

    def test_merge_flags_or_and_unknown_upgraded(self):
        a = build_record(_id_for(1700000000000), "elonmusk")
        b = build_record(
            _id_for(1700000000000), "elonmusk",
            is_pinned=True, content_type=ContentKind.ARTICLE,
            dom_datetime="2023-11-14T22:13:20.000Z",
        )
        merged, diags = dedupe_merge([a, b])
        assert len(merged) == 1
        r = merged[0]
        assert r.is_pinned
        assert r.content_type == ContentKind.ARTICLE
        assert r.dom_datetime.startswith("2023-11-14")
        assert diags == []

    def test_author_conflict_diagnostic(self):
        a = build_record(
            _id_for(1700000000000), "elonmusk", status_author="spacex",
            is_repost=True,
        )
        b = build_record(_id_for(1700000000000), "elonmusk")
        merged, diags = dedupe_merge([a, b])
        assert len(merged) == 1
        assert any("status_author conflict" in d for d in diags)

    def test_sort_ascending_and_same_ms_by_id(self):
        r1 = self._rec(1700000000000, tweet_seq=5)
        r2 = self._rec(1700000000000, tweet_seq=1)
        r3 = self._rec(1600000000000)
        merged, _ = dedupe_merge([r1, r2, r3])
        assert [r.tweet_id for r in merged] == [r3.tweet_id, r2.tweet_id,
                                                r1.tweet_id]

    def test_pinned_sorted_by_time_not_position(self):
        pinned = build_record(
            _id_for(1500000000000), "elonmusk", is_pinned=True
        )
        fresh = build_record(_id_for(1700000000000), "elonmusk")
        merged, _ = dedupe_merge([pinned, fresh])
        assert sort_key(merged[0]) < sort_key(merged[1])
        assert merged[0].is_pinned

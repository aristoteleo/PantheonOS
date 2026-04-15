"""Tests for freshness tracking and staleness warnings."""

import time

import pytest

from pantheon.internal.memory_system.freshness import (
    annotate_with_freshness,
    memory_age_days,
    memory_age_text,
    staleness_warning,
)


class TestMemoryAgeDays:
    def test_today(self):
        assert memory_age_days(time.time()) == 0

    def test_yesterday(self):
        assert memory_age_days(time.time() - 86_400) == 1

    def test_week_ago(self):
        assert memory_age_days(time.time() - 7 * 86_400) == 7

    def test_future_clamps_to_zero(self):
        assert memory_age_days(time.time() + 86_400) == 0


class TestMemoryAgeText:
    def test_today(self):
        assert memory_age_text(time.time()) == "today"

    def test_yesterday(self):
        assert memory_age_text(time.time() - 86_400) == "yesterday"

    def test_days_ago(self):
        text = memory_age_text(time.time() - 5 * 86_400)
        assert text == "5 days ago"


class TestStalenessWarning:
    def test_today_no_warning(self):
        assert staleness_warning(time.time()) is None

    def test_yesterday_no_warning(self):
        assert staleness_warning(time.time() - 86_400) is None

    def test_two_days_has_warning(self):
        warning = staleness_warning(time.time() - 2 * 86_400)
        assert warning is not None
        assert "2 days old" in warning
        assert "outdated" in warning

    def test_warning_content(self):
        warning = staleness_warning(time.time() - 30 * 86_400)
        assert "30 days old" in warning
        assert "Verify" in warning


class TestAnnotateWithFreshness:
    def test_fresh_no_annotation(self):
        content = "Some fresh content"
        result = annotate_with_freshness(content, time.time())
        assert result == content
        assert "---" not in result

    def test_stale_adds_annotation(self):
        content = "Some old content"
        mtime = time.time() - 10 * 86_400
        result = annotate_with_freshness(content, mtime)
        assert content in result
        assert "10 days old" in result
        assert "---" in result

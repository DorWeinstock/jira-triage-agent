"""Tests for composite history scoring functions.

Updated for Task 2 refactoring:
- STATUS_SCORE_MAP no longer has "investigating" (removed as nonexistent Jira status)
- Recency uses tiered cutoffs instead of linear formula
- Default weights from config: 0.55/0.10/0.20/0.15 (llm/component/status/recency)
"""

import pytest
from datetime import datetime, timezone, timedelta


class TestStatusScore:
    """Tests for status_score computation."""

    @pytest.fixture(autouse=True)
    def setup(self):
        import sys
        sys.path.insert(0, 'langgraph-agent')
        from src.agents.history_agent import compute_status_score, STATUS_SCORE_MAP
        self.compute = compute_status_score
        self.score_map = STATUS_SCORE_MAP

    def test_done_status(self):
        assert self.compute("Done") == 100

    def test_resolved_status(self):
        assert self.compute("Resolved") == 100

    def test_closed_status(self):
        assert self.compute("Closed") == 100

    def test_in_review_status(self):
        assert self.compute("In Review") == 75

    def test_in_progress_status(self):
        assert self.compute("In Progress") == 50

    def test_todo_status(self):
        assert self.compute("To Do") == 25

    def test_open_status(self):
        assert self.compute("Open") == 0

    def test_unknown_status(self):
        assert self.compute("Unknown") == 0

    def test_case_insensitive(self):
        assert self.compute("done") == 100
        assert self.compute("IN PROGRESS") == 50

    def test_investigating_status_removed(self):
        """'investigating' was a speculative nonexistent Jira status -- now removed."""
        assert "investigating" not in self.score_map
        # Falls through to default 0
        assert self.compute("investigating") == 0


class TestComponentMatch:
    """Tests for component_match computation."""

    @pytest.fixture(autouse=True)
    def setup(self):
        import sys
        sys.path.insert(0, 'langgraph-agent')
        from src.agents.history_agent import compute_component_match
        self.compute = compute_component_match

    def test_exact_match(self):
        assert self.compute(["order-service"], ["order-service"]) == 100

    def test_partial_overlap(self):
        """Any overlap = 100 (binary match)."""
        assert self.compute(
            ["order-service", "payments"],
            ["payments", "auth"]
        ) == 100

    def test_no_match(self):
        assert self.compute(["order-service"], ["auth-service"]) == 0

    def test_empty_current(self):
        """No current components = 0 (can't match)."""
        assert self.compute([], ["order-service"]) == 0

    def test_empty_historical(self):
        assert self.compute(["order-service"], []) == 0

    def test_both_empty(self):
        assert self.compute([], []) == 0

    def test_case_insensitive(self):
        assert self.compute(["Order-Service"], ["order-service"]) == 100


class TestRecencyBonus:
    """Tests for tiered recency_bonus computation.

    Tiers (with default max_days=365):
    - <30 days  -> 100
    - <90 days  -> 75
    - <180 days -> 50
    - <365 days -> 25
    - >=365 days -> 0
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        import sys
        sys.path.insert(0, 'langgraph-agent')
        from src.agents.history_agent import compute_recency_bonus
        self.compute = compute_recency_bonus

    def test_today(self):
        """Today = <30 days -> 100."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        assert self.compute(today, max_days=365) == 100

    def test_10_days_ago(self):
        """10 days = <30 days -> 100."""
        dt = datetime.now(timezone.utc) - timedelta(days=10)
        date_str = dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        assert self.compute(date_str, max_days=365) == 100

    def test_29_days_ago(self):
        """29 days = <30 days -> 100."""
        dt = datetime.now(timezone.utc) - timedelta(days=29)
        date_str = dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        assert self.compute(date_str, max_days=365) == 100

    def test_30_days_ago(self):
        """30 days = >=30 and <90 -> 75."""
        dt = datetime.now(timezone.utc) - timedelta(days=30)
        date_str = dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        assert self.compute(date_str, max_days=365) == 75

    def test_60_days_ago(self):
        """60 days = <90 days -> 75."""
        dt = datetime.now(timezone.utc) - timedelta(days=60)
        date_str = dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        assert self.compute(date_str, max_days=365) == 75

    def test_90_days_ago(self):
        """90 days = >=90 and <180 -> 50."""
        dt = datetime.now(timezone.utc) - timedelta(days=90)
        date_str = dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        assert self.compute(date_str, max_days=365) == 50

    def test_half_year_ago(self):
        """182 days = >=180 and <365 -> 25."""
        half_year = datetime.now(timezone.utc) - timedelta(days=182)
        date_str = half_year.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        score = self.compute(date_str, max_days=365)
        assert score == 25

    def test_one_year_ago(self):
        """365 days = >=max_days -> 0."""
        one_year = datetime.now(timezone.utc) - timedelta(days=365)
        date_str = one_year.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        score = self.compute(date_str, max_days=365)
        assert score == 0

    def test_older_than_max(self):
        old = datetime.now(timezone.utc) - timedelta(days=500)
        date_str = old.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        assert self.compute(date_str, max_days=365) == 0

    def test_empty_date(self):
        assert self.compute("", max_days=365) == 0

    def test_invalid_date(self):
        assert self.compute("not-a-date", max_days=365) == 0

    def test_date_only_format(self):
        """Test YYYY-MM-DD format (without time)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert self.compute(today, max_days=365) == 100

    def test_custom_max_days_controls_final_cutoff(self):
        """max_days param controls the >=max_days -> 0 cutoff."""
        dt = datetime.now(timezone.utc) - timedelta(days=100)
        date_str = dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        # With max_days=100, 100 days ago >= max_days -> 0
        assert self.compute(date_str, max_days=100) == 0
        # With max_days=365, 100 days ago < 180 -> 50
        assert self.compute(date_str, max_days=365) == 50

    def test_tier_boundaries_exact(self):
        """Verify exact tier boundaries: <30, <90, <180, <max_days."""
        # Boundary at 179 days: <180 -> 50
        dt = datetime.now(timezone.utc) - timedelta(days=179)
        date_str = dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        assert self.compute(date_str, max_days=365) == 50

        # Boundary at 180 days: >=180 and <365 -> 25
        dt = datetime.now(timezone.utc) - timedelta(days=180)
        date_str = dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        assert self.compute(date_str, max_days=365) == 25

        # Boundary at 364 days: <365 -> 25
        dt = datetime.now(timezone.utc) - timedelta(days=364)
        date_str = dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        assert self.compute(date_str, max_days=365) == 25


class TestCompositeScore:
    """Tests for final composite score computation.

    Default weights from config: llm=0.55, component=0.10, status=0.20, recency=0.15
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        import sys
        sys.path.insert(0, 'langgraph-agent')
        from src.agents.history_agent import compute_composite_score
        self.compute = compute_composite_score

    def test_all_perfect(self):
        score = self.compute(
            llm_similarity=100,
            component_match=100,
            status_score=100,
            recency_bonus=100,
        )
        assert score == 100.0

    def test_all_zero(self):
        score = self.compute(
            llm_similarity=0,
            component_match=0,
            status_score=0,
            recency_bonus=0,
        )
        assert score == 0.0

    def test_default_weights_from_config(self):
        """Default weights should come from config: 0.55 + 0.10 + 0.20 + 0.15 = 1.0"""
        # If only LLM similarity is 100, score should be 55 (0.55 weight)
        score = self.compute(
            llm_similarity=100,
            component_match=0,
            status_score=0,
            recency_bonus=0,
        )
        assert score == 55.0

    def test_component_weight(self):
        """Component weight is 0.10 from config defaults."""
        score = self.compute(
            llm_similarity=0,
            component_match=100,
            status_score=0,
            recency_bonus=0,
        )
        assert score == 10.0

    def test_status_weight(self):
        """Status weight is 0.20 from config defaults."""
        score = self.compute(
            llm_similarity=0,
            component_match=0,
            status_score=100,
            recency_bonus=0,
        )
        assert score == 20.0

    def test_recency_weight(self):
        """Recency weight is 0.15 from config defaults."""
        score = self.compute(
            llm_similarity=0,
            component_match=0,
            status_score=0,
            recency_bonus=100,
        )
        assert score == 15.0

    def test_realistic_scenario(self):
        """Resolved ticket with matching component, high similarity, recent."""
        score = self.compute(
            llm_similarity=85,
            component_match=100,
            status_score=100,  # Done
            recency_bonus=80,  # recent
        )
        # 85*0.55 + 100*0.10 + 100*0.20 + 80*0.15 = 46.75 + 10 + 20 + 12 = 88.75
        # Rounded to 1 decimal: 88.8
        assert score == 88.8

    def test_explicit_weight_override(self):
        """Explicit weights should override config defaults."""
        score = self.compute(
            llm_similarity=100,
            component_match=0,
            status_score=0,
            recency_bonus=0,
            w_llm=0.30,
            w_component=0.30,
            w_status=0.20,
            w_recency=0.20,
        )
        assert score == 30.0

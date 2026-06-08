"""Unit tests for composite ticket scoring sort logic.

Tests verify that similar tickets are correctly prioritized using
the composite score formula (weights from config):
  final_score = llm_similarity*0.55 + component_match*0.10 + status_score*0.20 + recency_bonus*0.15

Sort order: highest final_score first.
Within same final_score: is_resolved=True first, then newer updated.
"""

import pytest
import sys

sys.path.insert(0, "langgraph-agent")

from src.agents.history_agent import (
    ScoredTicket,
    compute_composite_score,
    compute_status_score,
    compute_component_match,
)


def sort_scored_tickets(tickets: list[ScoredTicket]) -> list[ScoredTicket]:
    """Sort ScoredTickets by final_score desc, then is_resolved desc, then updated desc."""
    return sorted(
        tickets,
        key=lambda t: (t.final_score, t.is_resolved, t.updated),
        reverse=True,
    )


class TestScoredTicketSorting:
    """Tests for ScoredTicket composite sort order."""

    def test_final_score_is_primary_sort(self):
        """Higher final_score should always come first."""
        tickets = [
            ScoredTicket(key="SP-1", final_score=50.0, is_resolved=True, updated="2026-01-01"),
            ScoredTicket(key="SP-2", final_score=90.0, is_resolved=False, updated="2025-01-01"),
            ScoredTicket(key="SP-3", final_score=70.0, is_resolved=True, updated="2026-06-01"),
        ]

        result = sort_scored_tickets(tickets)

        assert result[0].key == "SP-2"  # 90.0
        assert result[1].key == "SP-3"  # 70.0
        assert result[2].key == "SP-1"  # 50.0

    def test_resolved_breaks_tie_same_score(self):
        """Resolved tickets should come before open at same final_score."""
        tickets = [
            ScoredTicket(key="SP-1", final_score=80.0, is_resolved=False, updated="2026-06-01"),
            ScoredTicket(key="SP-2", final_score=80.0, is_resolved=True, updated="2026-01-01"),
        ]

        result = sort_scored_tickets(tickets)

        assert result[0].key == "SP-2"  # Resolved
        assert result[1].key == "SP-1"  # Open

    def test_newer_before_older_same_score_and_resolution(self):
        """Newer tickets should come first when score and resolution are equal."""
        tickets = [
            ScoredTicket(key="SP-1", final_score=75.0, is_resolved=True, updated="2026-01-15"),
            ScoredTicket(key="SP-2", final_score=75.0, is_resolved=True, updated="2026-06-20"),
            ScoredTicket(key="SP-3", final_score=75.0, is_resolved=True, updated="2026-03-10"),
        ]

        result = sort_scored_tickets(tickets)

        assert result[0].key == "SP-2"  # Newest
        assert result[1].key == "SP-3"
        assert result[2].key == "SP-1"  # Oldest

    def test_full_priority_order(self):
        """Test complete priority: final_score > is_resolved > updated."""
        tickets = [
            ScoredTicket(key="SP-1", final_score=90.0, is_resolved=False, updated="2026-01-01"),
            ScoredTicket(key="SP-2", final_score=80.0, is_resolved=True, updated="2026-06-01"),
            ScoredTicket(key="SP-3", final_score=80.0, is_resolved=True, updated="2026-01-01"),
            ScoredTicket(key="SP-4", final_score=80.0, is_resolved=False, updated="2026-12-01"),
            ScoredTicket(key="SP-5", final_score=70.0, is_resolved=True, updated="2026-12-01"),
        ]

        result = sort_scored_tickets(tickets)

        expected = ["SP-1", "SP-2", "SP-3", "SP-4", "SP-5"]
        actual = [t.key for t in result]
        assert actual == expected, (
            f"Expected {expected}, got {actual}\n"
            "SP-1: score=90 (highest)\n"
            "SP-2: score=80, resolved, newer\n"
            "SP-3: score=80, resolved, older\n"
            "SP-4: score=80, open\n"
            "SP-5: score=70 (lowest)"
        )

    def test_single_ticket(self):
        """Single ticket should remain in list."""
        tickets = [ScoredTicket(key="SP-1", final_score=90.0)]
        result = sort_scored_tickets(tickets)
        assert len(result) == 1
        assert result[0].key == "SP-1"

    def test_empty_list(self):
        """Empty list should remain empty."""
        assert sort_scored_tickets([]) == []

    def test_realistic_scenario_composite_scores(self):
        """Realistic: resolved+status should beat open+high similarity.

        With new weights (0.55/0.10/0.20/0.15):
        A: 95*0.55 + 0*0.10 + 0*0.20 + 80*0.15 = 52.25 + 0 + 0 + 12 = 64.25 -> 64.2
        B: 70*0.55 + 100*0.10 + 100*0.20 + 50*0.15 = 38.5 + 10 + 20 + 7.5 = 76.0
        B wins.
        """
        score_a = compute_composite_score(
            llm_similarity=95, component_match=0, status_score=0, recency_bonus=80
        )
        score_b = compute_composite_score(
            llm_similarity=70, component_match=100, status_score=100, recency_bonus=50
        )

        tickets = [
            ScoredTicket(key="SP-A", final_score=score_a, is_resolved=False, updated="2026-06-01"),
            ScoredTicket(key="SP-B", final_score=score_b, is_resolved=True, updated="2026-01-01"),
        ]

        result = sort_scored_tickets(tickets)

        assert result[0].key == "SP-B"
        assert result[1].key == "SP-A"
        assert score_b > score_a

    def test_scored_ticket_model_defaults(self):
        """Verify ScoredTicket field defaults."""
        t = ScoredTicket(key="SP-1")
        assert t.summary == ""
        assert t.components == []
        assert t.llm_similarity == 0.0
        assert t.component_match == 0.0
        assert t.status_score == 0.0
        assert t.recency_bonus == 0.0
        assert t.final_score == 0.0
        assert t.is_resolved is False

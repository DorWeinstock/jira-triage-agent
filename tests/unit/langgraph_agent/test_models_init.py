"""Smoke test: all public symbols are importable from src.models.

This test ensures that __all__ in src/models/__init__.py matches the actual
exported symbols, preventing regressions when the package is refactored.
"""

import importlib
import pytest


EXPECTED_SYMBOLS = [
    # Enums
    "ConfidenceLevel",
    "ActionType",
    "IssueCategory",
    # JiraAgent models
    "TicketExtraction",
    # Diagnostician models
    "Diagnosis",
    "RemediationPlan",
    "RemediationStep",
    # Schema helpers
    "get_llm_schema_for_remediation_plan",
]


def test_all_symbols_importable():
    """Verify all expected symbols are importable from src.models."""
    module = importlib.import_module("src.models")
    for name in EXPECTED_SYMBOLS:
        assert hasattr(module, name), f"src.models missing expected symbol: {name}"


def test_all_symbols_declared_in_dunder_all():
    """Verify all expected symbols are declared in __all__."""
    from src import models

    for name in EXPECTED_SYMBOLS:
        assert name in models.__all__, f"{name} not in src.models.__all__"


def test_no_private_symbols_in_dunder_all():
    """Verify no private symbols (starting with _) are in __all__."""
    from src import models

    private = [n for n in models.__all__ if n.startswith("_")]
    assert private == [], f"Private symbols found in __all__: {private}"


def test_dunder_all_has_expected_length():
    """Verify __all__ has expected number of exports (detect accidental removals)."""
    from src import models

    expected_count = len(EXPECTED_SYMBOLS)
    assert len(models.__all__) == expected_count, (
        f"src.models.__all__ has {len(models.__all__)} items, "
        f"expected {expected_count}. Check for accidental additions/removals."
    )

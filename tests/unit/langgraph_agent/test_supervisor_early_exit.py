"""Test early exit routing in supervisor workflow."""

import pytest


def test_early_exit_when_no_ticket_id():
    """Should route to post_comment when ticket_id is missing."""
    from src.supervisor import should_continue_after_ticket_read

    state = {"affected_deployments": ["app"]}  # No ticket_id
    result = should_continue_after_ticket_read(state)
    assert result == "post_comment"


def test_early_exit_when_no_affected_resources():
    """Should route to post_comment when no resources identified."""
    from src.supervisor import should_continue_after_ticket_read

    state = {"ticket_id": "TEST-123", "namespace": "default"}  # No affected_resources
    result = should_continue_after_ticket_read(state)
    assert result == "post_comment"


def test_continue_when_valid():
    """Should continue to investigate when ticket and resources present."""
    from src.supervisor import should_continue_after_ticket_read

    state = {
        "ticket_id": "TEST-123",
        "affected_resources": {"deployments": ["app"]},
        "namespace": "default"
    }
    result = should_continue_after_ticket_read(state)
    assert result == "investigate_cluster"


def test_continue_with_affected_services():
    """Should continue to investigate when ticket and affected_services present."""
    from src.supervisor import should_continue_after_ticket_read

    state = {
        "ticket_id": "TEST-123",
        "affected_resources": {"services": ["api-service"]},
        "namespace": "default"
    }
    result = should_continue_after_ticket_read(state)
    assert result == "investigate_cluster"


def test_continue_with_both_resources():
    """Should continue when both deployments and services are affected."""
    from src.supervisor import should_continue_after_ticket_read

    state = {
        "ticket_id": "TEST-123",
        "affected_resources": {
            "deployments": ["app"],
            "services": ["api-service"],
        },
        "namespace": "default",
    }
    result = should_continue_after_ticket_read(state)
    assert result == "investigate_cluster"


def test_early_exit_with_empty_lists():
    """Should route to post_comment when lists are empty."""
    from src.supervisor import should_continue_after_ticket_read

    state = {
        "ticket_id": "TEST-123",
        "affected_resources": {
            "deployments": [],
            "services": [],
        },
        "namespace": "default",
    }
    result = should_continue_after_ticket_read(state)
    assert result == "post_comment"


def test_early_exit_when_no_namespace():
    """Should skip investigation when namespace is missing."""
    from src.supervisor import should_continue_after_ticket_read

    state = {
        "ticket_id": "TEST-123",
        "affected_deployments": ["app"],
    }
    result = should_continue_after_ticket_read(state)
    assert result == "skip_investigation_no_namespace"

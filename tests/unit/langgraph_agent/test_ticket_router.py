"""Tests for TicketRouter."""

from unittest.mock import MagicMock, AsyncMock

import pytest

from src.agents.ticket_router import TicketRouter

PROCESSED_LABEL = "triage-agent-done"
IN_PROGRESS_LABEL = "triage-in-progress"
VALID_LABEL = "triage-verdict-valid"
INVALID_LABEL = "triage-verdict-invalid"


@pytest.fixture
def jira_tools():
    tools = MagicMock()
    tools.update_assignee = AsyncMock()
    tools.add_label = AsyncMock()
    tools.remove_label = AsyncMock()
    tools.update_issue = AsyncMock()
    return tools


@pytest.fixture
def router(jira_tools):
    return TicketRouter(
        jira_tools=jira_tools,
        team_members=["alice", "bob"],
        processed_label=PROCESSED_LABEL,
        in_progress_label=IN_PROGRESS_LABEL,
        verdict_valid_label=VALID_LABEL,
        verdict_invalid_label=INVALID_LABEL,
    )


def valid_state(**overrides):
    state = {
        "ticket_id": "TEST-1",
        "ticket_description": "Original body.",
        "is_spam": False,
        "issue_scope": "k8s",
        "jenkins_link_found": True,
        "server_name_found": True,
    }
    state.update(overrides)
    return state


def spam_state(**overrides):
    state = {
        "ticket_id": "TEST-1",
        "ticket_description": "Original body.",
        "is_spam": True,
        "ticket_reporter": "reporter1",
        "spam_reason": "Missing Jenkins link",
        "issue_scope": "other",
        "jenkins_link_found": False,
        "server_name_found": False,
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# Valid path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_valid_assigns_and_stamps_labels(router, jira_tools):
    result = await router.run(valid_state(), rr_index=0)

    jira_tools.update_assignee.assert_awaited_once_with("TEST-1", "alice")
    jira_tools.add_label.assert_any_await("TEST-1", PROCESSED_LABEL)
    jira_tools.add_label.assert_any_await("TEST-1", VALID_LABEL)
    jira_tools.remove_label.assert_awaited_once_with("TEST-1", IN_PROGRESS_LABEL)
    assert result == {"assigned_to": "alice", "triage_complete": True}


@pytest.mark.asyncio
async def test_handle_valid_updates_description_with_judgment_block(router, jira_tools):
    await router.run(valid_state(), rr_index=0)

    _, kwargs = jira_tools.update_issue.await_args
    description = kwargs["description"]
    assert "Original body." in description
    assert "Verdict: VALID" in description
    assert "Why:" in description


@pytest.mark.asyncio
async def test_handle_valid_idempotent_replaces_previous_judgment(router, jira_tools):
    stale = (
        "Original body.\n\n"
        "---\n**Triage Judgment**\n- Verdict: VALID\n- Why: stale reason."
    )
    await router.run(valid_state(ticket_description=stale), rr_index=0)

    _, kwargs = jira_tools.update_issue.await_args
    description = kwargs.get("description")
    assert description.count("**Triage Judgment**") == 1
    assert "stale reason" not in description
    assert "Original body." in description


@pytest.mark.asyncio
async def test_handle_valid_never_raises_when_jira_calls_fail(router, jira_tools):
    jira_tools.update_assignee.side_effect = Exception("boom")
    jira_tools.add_label.side_effect = Exception("boom")
    jira_tools.remove_label.side_effect = Exception("boom")
    jira_tools.update_issue.side_effect = Exception("boom")

    result = await router.run(valid_state(), rr_index=1)

    assert result == {"assigned_to": "bob", "triage_complete": True}


# ---------------------------------------------------------------------------
# Spam (invalid) path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_spam_reassigns_and_stamps_labels(router, jira_tools):
    result = await router.run(spam_state(), rr_index=0)

    jira_tools.update_assignee.assert_awaited_once_with("TEST-1", "reporter1")
    jira_tools.add_label.assert_any_await("TEST-1", PROCESSED_LABEL)
    jira_tools.add_label.assert_any_await("TEST-1", INVALID_LABEL)
    jira_tools.remove_label.assert_awaited_once_with("TEST-1", IN_PROGRESS_LABEL)
    jira_tools.add_comment.assert_not_called()
    assert result == {"triage_complete": True}


@pytest.mark.asyncio
async def test_handle_spam_judgment_missing_info_blames_reporter(router, jira_tools):
    await router.run(spam_state(jenkins_link_found=False, server_name_found=True), rr_index=0)

    _, kwargs = jira_tools.update_issue.await_args
    description = kwargs.get("description")
    assert "Verdict: INVALID (spam)" in description
    assert "Who should handle it: Reporter" in description
    assert "Jenkins job link" in description


@pytest.mark.asyncio
async def test_handle_spam_judgment_out_of_scope_blames_owning_team(router, jira_tools):
    await router.run(
        spam_state(issue_scope="hardware", jenkins_link_found=True, server_name_found=True),
        rr_index=0,
    )

    _, kwargs = jira_tools.update_issue.await_args
    description = kwargs.get("description")
    assert "Who should handle it: Hardware/Facilities team" in description


@pytest.mark.asyncio
async def test_handle_spam_idempotent_replaces_previous_judgment(router, jira_tools):
    stale = (
        "Original body.\n\n"
        "---\n**Triage Judgment**\n- Verdict: INVALID (spam)\n- Why: stale reason."
    )
    await router.run(spam_state(ticket_description=stale), rr_index=0)

    _, kwargs = jira_tools.update_issue.await_args
    description = kwargs.get("description")
    assert description.count("**Triage Judgment**") == 1
    assert "stale reason" not in description
    assert "Original body." in description


@pytest.mark.asyncio
async def test_handle_spam_no_reporter_skips_reassignment(router, jira_tools):
    result = await router.run(spam_state(ticket_reporter=None), rr_index=0)

    jira_tools.update_assignee.assert_not_awaited()
    assert result == {"triage_complete": True}

"""Jenkins build failure investigation agent."""

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ..config import create_extraction_llm
from ..state import AgentState
from ..tools.jenkins_tools import JenkinsTools
from ..utils.llm_utils import invoke_llm_with_retry

logger = logging.getLogger(__name__)

AGENT_NAME = "JenkinsInvestigator"

_CLASSIFICATION_SYSTEM_PROMPT = """You are a CI/CD expert. Analyze the Jenkins build data below and classify the failure.

Respond with ONLY a valid JSON object:
{
  "failure_type": "<one of: compilation_error, test_failure, infrastructure, pipeline_script_error, plugin_issue, permission_auth, flaky_intermittent, unknown>",
  "root_cause": "<brief root cause description>",
  "error_snippets": ["<key error lines from console log>"],
  "console_log_summary": "<2-3 sentence summary of what the console log shows>"
}
"""


class JenkinsInvestigator:
    """Investigates failed Jenkins builds from URLs found in Jira tickets.

    Non-fatal: all errors are caught and logged. The workflow continues
    without Jenkins data if anything fails.
    """

    def __init__(self, jenkins_tools: JenkinsTools):
        self.llm = create_extraction_llm()
        self.tools = jenkins_tools

    async def run(self, state: AgentState) -> AgentState:
        """Extract and analyze Jenkins failure data.

        No-op if no Jenkins URLs in state. Non-fatal on all errors.
        """
        jenkins_urls = state.get("jenkins_urls", [])
        if not jenkins_urls:
            logger.info(f"[{AGENT_NAME}] No Jenkins URLs found - skipping")
            return state

        url = jenkins_urls[0]
        if len(jenkins_urls) > 1:
            logger.info(
                f"[{AGENT_NAME}] Processing first URL only. "
                f"Skipped {len(jenkins_urls) - 1} additional URLs: "
                f"{jenkins_urls[1:]}"
            )

        try:
            findings = await self._investigate_build(url)
            state["jenkins_findings"] = findings
            logger.info(
                f"[{AGENT_NAME}] Investigation complete: "
                f"failure_type={findings.get('failure_type', 'unknown')}"
            )
        except Exception as e:
            logger.warning(
                f"[{AGENT_NAME}] Investigation failed for {url}: {e}"
            )
            state["jenkins_findings"] = {"error": str(e), "url": url}

        return state

    async def _investigate_build(self, url: str) -> dict[str, Any]:
        """Fetch build data and classify failure."""
        findings: dict[str, Any] = {"url": url}

        # 1. Fetch build info (required)
        build_info = await self.tools.get_build_info(url)
        findings["build_info"] = build_info

        # 2. Fetch console log (non-fatal)
        try:
            console_log = await self.tools.get_console_log(url)
            findings["raw_console_log"] = console_log
        except Exception as e:
            logger.warning(f"[{AGENT_NAME}] Console log fetch failed: {e}")
            findings["raw_console_log"] = None

        # 3. Fetch parent build info (non-fatal)
        try:
            parent_info = await self.tools.get_parent_build_info(url)
            findings["parent_build"] = parent_info
        except Exception as e:
            logger.warning(f"[{AGENT_NAME}] Parent build fetch failed: {e}")
            findings["parent_build"] = None

        # 4. LLM classification (non-fatal)
        try:
            classification = await self._classify_failure(findings)
            findings.update(classification)
        except Exception as e:
            logger.warning(f"[{AGENT_NAME}] LLM classification failed: {e}")
            findings["failure_type"] = "unknown"
            findings["root_cause"] = None
            findings["error_snippets"] = []
            findings["console_log_summary"] = None

        # Clear raw console log after classification to reduce state/checkpoint size
        # (up to 100KB). The console_log_summary field retains the distilled version.
        findings["raw_console_log"] = None

        return findings

    async def _classify_failure(self, findings: dict) -> dict[str, Any]:
        """Use LLM to classify the build failure."""
        context = f"""BUILD INFO:
{findings.get('build_info', 'N/A')}

CONSOLE LOG:
{findings.get('raw_console_log', 'N/A')}

PARENT BUILD:
{findings.get('parent_build', 'N/A')}"""

        messages = [
            SystemMessage(content=_CLASSIFICATION_SYSTEM_PROMPT),
            HumanMessage(content=context),
        ]

        response = await invoke_llm_with_retry(self.llm, messages)

        content = response.content
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
        if json_match:
            content = json_match.group(1)
        else:
            brace_match = re.search(r'\{[\s\S]*\}', content)
            if brace_match:
                content = brace_match.group(0)

        data = json.loads(content)
        return {
            "failure_type": data.get("failure_type", "unknown"),
            "root_cause": data.get("root_cause"),
            "error_snippets": data.get("error_snippets", []),
            "console_log_summary": data.get("console_log_summary"),
        }

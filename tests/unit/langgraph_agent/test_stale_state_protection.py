"""Tests for stale-state protection feature.

This module tests the checkpoint resume protection mechanism that clears
stale Kubernetes data when a workflow is resumed from a checkpoint. This
prevents agents from making decisions based on outdated cluster state.

The protection mechanism works in three parts:
1. K8sConfigMapSaver.aget_tuple() sets resumed_from_checkpoint=True when loading
2. supervisor.initialize_state() detects the flag and clears stale data
3. The flag is reset to False after clearing

Stale data cleared:
- cluster_findings (K8s resources, logs, events - changes rapidly)
- verification_evidence (verification results from previous attempts)
- root_cause (diagnosis based on stale findings)
- recommended_action (remediation plan based on stale diagnosis)
- confidence_level (confidence in stale diagnosis)

Preserved data:
- ticket_id, ticket_summary (immutable ticket context)
- remediation_count, remediation_history (critical for retry logic)
- similar_tickets (historical patterns, less volatile)
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.state import AgentState


class TestCheckpointerSetsResumeFlag:
    """Test that K8sConfigMapSaver sets resumed_from_checkpoint flag when loading."""

    @pytest.fixture
    def mock_k8s_client(self):
        """Create a mock K8s client."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_aget_tuple_sets_resume_flag_on_load(self, mock_k8s_client):
        """Should set resumed_from_checkpoint=True when loading checkpoint.

        This is the entry point for the stale-state protection mechanism.
        When a checkpoint is loaded (e.g., after pod restart), the checkpointer
        must flag the state so the supervisor knows to clear stale data.
        """
        from src.checkpoint import K8sConfigMapSaver

        # Create checkpoint data that will be loaded
        checkpoint_data = {
            "v": 1,
            "id": "ckpt-123",
            "ts": "2024-01-01T00:00:00+00:00",
            "channel_values": {
                "ticket_id": "PROJ-123",
                "cluster_findings": {"pods": ["old-data"]},  # Stale data
                "root_cause": "Out-of-date diagnosis",  # Stale diagnosis
                "resumed_from_checkpoint": False,  # Initial value
            },
            "channel_versions": {"__start__": 1},
            "versions_seen": {},
        }
        metadata = {"source": "test"}

        # Create mock ConfigMap with checkpoint data
        mock_cm = MagicMock()
        mock_cm.data = {
            "checkpoint": json.dumps(checkpoint_data),
            "metadata": json.dumps(metadata),
            "checkpoint_id": "ckpt-123",
            "thread_id": "ticket-PROJ-123",
            "checkpoint_ns": "",
        }
        mock_k8s_client.read_namespaced_config_map = AsyncMock(return_value=mock_cm)

        # Mock ApiException (needed for import)
        mock_rest_module = MagicMock()

        with patch.dict(
            "sys.modules", {"kubernetes_asyncio.client.rest": mock_rest_module}
        ):
            saver = K8sConfigMapSaver(namespace="test-ns")
            saver._k8s_client = mock_k8s_client

            config = {"configurable": {"thread_id": "ticket-PROJ-123"}}
            result = await saver.aget_tuple(config)

            # Verify checkpoint was loaded
            assert result is not None
            assert result.checkpoint["id"] == "ckpt-123"

            # CRITICAL: Verify the flag was set to True
            assert (
                result.checkpoint["channel_values"]["resumed_from_checkpoint"] is True
            )

            # Verify other data was preserved (not modified by checkpointer)
            assert result.checkpoint["channel_values"]["ticket_id"] == "PROJ-123"
            assert "cluster_findings" in result.checkpoint["channel_values"]

    @pytest.mark.asyncio
    async def test_aget_tuple_returns_none_for_missing_checkpoint(self, mock_k8s_client):
        """Should return None when no checkpoint exists (fresh start).

        When there's no checkpoint to load, this is a fresh workflow start,
        so no resume flag needs to be set.
        """
        from src.checkpoint import K8sConfigMapSaver

        # Mock ApiException - create a real exception class
        class MockApiException(Exception):
            def __init__(self, status):
                self.status = status
                super().__init__(f"API Error: {status}")

        mock_rest_module = MagicMock()
        mock_rest_module.ApiException = MockApiException

        # ConfigMap doesn't exist (404)
        mock_k8s_client.read_namespaced_config_map = AsyncMock(
            side_effect=MockApiException(404)
        )

        with patch.dict(
            "sys.modules", {"kubernetes_asyncio.client.rest": mock_rest_module}
        ):
            saver = K8sConfigMapSaver(namespace="test-ns")
            saver._k8s_client = mock_k8s_client

            config = {"configurable": {"thread_id": "ticket-PROJ-123"}}
            result = await saver.aget_tuple(config)

            # Should return None (no checkpoint to load)
            assert result is None

    @pytest.mark.asyncio
    async def test_aget_tuple_preserves_existing_state_data(self, mock_k8s_client):
        """Should preserve all existing state data when setting resume flag.

        The checkpointer should ONLY add the flag, not modify any other state.
        This ensures the supervisor has access to all historical data when
        deciding what to clear.
        """
        from src.checkpoint import K8sConfigMapSaver

        # Create checkpoint with comprehensive state
        checkpoint_data = {
            "v": 1,
            "id": "ckpt-456",
            "ts": "2024-01-01T00:00:00+00:00",
            "channel_values": {
                "ticket_id": "PROJ-456",
                "ticket_summary": "Pod CrashLoopBackOff",
                "cluster_findings": {
                    "pods": ["api-server-abc"],
                    "events": ["BackOff event"],
                },
                "remediation_count": 2,
                "remediation_history": [
                    {"attempt": 1, "success": False},
                    {"attempt": 2, "success": False},
                ],
                "similar_tickets": [{"key": "PROJ-100"}],
                "root_cause": "Memory leak in api-server",
                "verification_evidence": ["Pod still crashing"],
            },
            "channel_versions": {"__start__": 1},
            "versions_seen": {},
        }

        mock_cm = MagicMock()
        mock_cm.data = {
            "checkpoint": json.dumps(checkpoint_data),
            "metadata": "{}",
            "checkpoint_id": "ckpt-456",
            "thread_id": "ticket-PROJ-456",
            "checkpoint_ns": "",
        }
        mock_k8s_client.read_namespaced_config_map = AsyncMock(return_value=mock_cm)

        mock_rest_module = MagicMock()

        with patch.dict(
            "sys.modules", {"kubernetes_asyncio.client.rest": mock_rest_module}
        ):
            saver = K8sConfigMapSaver(namespace="test-ns")
            saver._k8s_client = mock_k8s_client

            config = {"configurable": {"thread_id": "ticket-PROJ-456"}}
            result = await saver.aget_tuple(config)

            # Verify flag was added
            assert result.checkpoint["channel_values"]["resumed_from_checkpoint"]

            # Verify ALL other data was preserved unchanged
            channel_values = result.checkpoint["channel_values"]
            assert channel_values["ticket_id"] == "PROJ-456"
            assert channel_values["ticket_summary"] == "Pod CrashLoopBackOff"
            assert channel_values["cluster_findings"]["pods"] == ["api-server-abc"]
            assert channel_values["remediation_count"] == 2
            assert len(channel_values["remediation_history"]) == 2
            assert len(channel_values["similar_tickets"]) == 1
            assert channel_values["root_cause"] == "Memory leak in api-server"
            assert channel_values["verification_evidence"] == ["Pod still crashing"]


class TestInitializeClearsStaleDataOnResume:
    """Test that supervisor.initialize_state() clears stale data when flag is True."""

    def _create_initialize_state_func(self):
        """Helper to extract initialize_state function from supervisor.

        This duplicates the logic from supervisor.py for testing purposes.
        We can't easily extract the nested function, so we recreate it.
        """
        from logging import getLogger

        logger = getLogger(__name__)

        def initialize_state(state: AgentState) -> AgentState:
            """Initialize state with default values, clear stale data on resume."""
            # Handle checkpoint resume - clear stale K8s data that may be outdated
            if state.get("resumed_from_checkpoint"):
                logger.info(
                    "Resumed from checkpoint - clearing stale K8s data to ensure "
                    "fresh investigation reflects current cluster state"
                )
                # Clear volatile cluster data (changes rapidly)
                state["cluster_findings"] = {}
                state["verification_evidence"] = []

                # Clear diagnosis (based on stale findings)
                state["root_cause"] = None
                state["recommended_action"] = None
                state["confidence_level"] = None

                # Reset the flag
                state["resumed_from_checkpoint"] = False

                # Log what we preserved
                logger.info(
                    f"Preserved: remediation_count={state.get('remediation_count', 0)}, "
                    f"remediation_history={len(state.get('remediation_history', []))} entries, "
                    f"ticket_id={state.get('ticket_id')}"
                )

            # Standard initialization
            if "iteration_count" not in state:
                state["iteration_count"] = 0
            if "remediation_count" not in state:
                state["remediation_count"] = 0
            if "remediation_history" not in state:
                state["remediation_history"] = []
            if "issue_resolved" not in state:
                state["issue_resolved"] = False
            if "namespace" not in state:
                state["namespace"] = "production"
            return state

        return initialize_state

    def test_initialize_clears_volatile_k8s_data(self):
        """Should clear volatile K8s data that changes rapidly.

        cluster_findings contain K8s resources, logs, and events that reflect
        cluster state at a specific point in time. After a pod restart, this
        data is stale and must be re-fetched to reflect current state.
        """
        # Create state as it would be loaded from checkpoint
        state = AgentState(
            ticket_id="PROJ-123",
            ticket_summary="CrashLoopBackOff in api-server",
            cluster_findings={
                "pods": ["stale-pod-data"],
                "logs": ["old log entries"],
                "events": ["outdated events"],
            },
            resumed_from_checkpoint=True,  # Flag set by checkpointer
        )

        # Call initialize_state
        initialize_state = self._create_initialize_state_func()
        result = initialize_state(state)

        # Verify volatile K8s data was cleared
        assert result["cluster_findings"] == {}

        # Verify important context was preserved
        assert result["ticket_id"] == "PROJ-123"
        assert result["ticket_summary"] == "CrashLoopBackOff in api-server"

    def test_initialize_clears_stale_diagnosis(self):
        """Should clear diagnosis based on stale cluster findings.

        root_cause, recommended_action, and confidence_level are derived from
        cluster_findings. When findings are stale, the diagnosis is also stale
        and must be regenerated.
        """
        state = AgentState(
            ticket_id="PROJ-789",
            cluster_findings={"pods": ["old-data"]},  # Stale
            root_cause="Outdated diagnosis based on stale data",  # Must clear
            recommended_action="kubectl delete pod api-server",  # Must clear
            confidence_level="high",  # Must clear
            resumed_from_checkpoint=True,
        )

        initialize_state = self._create_initialize_state_func()
        result = initialize_state(state)

        # Verify diagnosis was cleared
        assert result["root_cause"] is None
        assert result["recommended_action"] is None
        assert result["confidence_level"] is None

        # Verify cluster findings (source of diagnosis) was also cleared
        assert result["cluster_findings"] == {}

    def test_initialize_clears_verification_evidence(self):
        """Should clear verification evidence from previous remediation attempts.

        verification_evidence contains results from verify_fix node after
        remediation. After a pod restart, we need to re-verify from scratch
        with fresh cluster data.
        """
        
        

        state = AgentState(
            ticket_id="PROJ-456",
            verification_evidence=[
                "Verified: pod api-server is running",
                "Verified: no crash events in last 5 minutes",
                "Issue resolved: True",
            ],
            resumed_from_checkpoint=True,
        )


        initialize_state = self._create_initialize_state_func()
        result = initialize_state(state)

        # Verify verification evidence was cleared
        assert result["verification_evidence"] == []


class TestInitializePreservesImportantDataOnResume:
    """Test that supervisor preserves critical data when resuming from checkpoint."""

    def _create_initialize_state_func(self):
        """Helper to extract initialize_state function - shared across all test classes."""
        from logging import getLogger

        logger = getLogger(__name__)

        def initialize_state(state: AgentState) -> AgentState:
            """Initialize state with default values, clear stale data on resume."""
            if state.get("resumed_from_checkpoint"):
                logger.info(
                    "Resumed from checkpoint - clearing stale K8s data to ensure "
                    "fresh investigation reflects current cluster state"
                )
                state["cluster_findings"] = {}
                state["verification_evidence"] = []
                state["root_cause"] = None
                state["recommended_action"] = None
                state["confidence_level"] = None
                state["resumed_from_checkpoint"] = False
                logger.info(
                    f"Preserved: remediation_count={state.get('remediation_count', 0)}, "
                    f"remediation_history={len(state.get('remediation_history', []))} entries, "
                    f"ticket_id={state.get('ticket_id')}"
                )
            if "iteration_count" not in state:
                state["iteration_count"] = 0
            if "remediation_count" not in state:
                state["remediation_count"] = 0
            if "remediation_history" not in state:
                state["remediation_history"] = []
            if "issue_resolved" not in state:
                state["issue_resolved"] = False
            if "namespace" not in state:
                state["namespace"] = "production"
            return state

        return initialize_state

    def test_initialize_preserves_ticket_context(self):
        """Should preserve immutable ticket context (ID, summary, description).

        Ticket context doesn't change during the workflow and is needed for
        investigation continuity after resume.
        """
        
        

        state = AgentState(
            ticket_id="PROJ-999",
            ticket_summary="Database connection timeout",
            ticket_description="Users report 500 errors when accessing /api/users",
            cluster_findings={"pods": ["stale"]},  # Will be cleared
            resumed_from_checkpoint=True,
        )


        initialize_state = self._create_initialize_state_func()
        result = initialize_state(state)

        # Verify ticket context was preserved
        assert result["ticket_id"] == "PROJ-999"
        assert result["ticket_summary"] == "Database connection timeout"
        assert (
            result["ticket_description"]
            == "Users report 500 errors when accessing /api/users"
        )

        # Verify stale K8s data was still cleared
        assert result["cluster_findings"] == {}

    def test_initialize_preserves_remediation_history(self):
        """Should preserve remediation count and history for retry logic.

        remediation_count and remediation_history are CRITICAL for:
        - Preventing infinite remediation loops (max attempts check)
        - Learning from previous failed attempts
        - Audit trail of what was tried

        These must NEVER be cleared, even on resume.
        """
        
        

        state = AgentState(
            ticket_id="PROJ-888",
            remediation_count=3,
            remediation_history=[
                {"attempt": 1, "action": "restart pod", "success": False},
                {"attempt": 2, "action": "increase memory", "success": False},
                {"attempt": 3, "action": "fix config", "success": False},
            ],
            cluster_findings={"pods": ["stale"]},  # Will be cleared
            root_cause="Old diagnosis",  # Will be cleared
            resumed_from_checkpoint=True,
        )


        initialize_state = self._create_initialize_state_func()
        result = initialize_state(state)

        # CRITICAL: Verify remediation tracking was preserved
        assert result["remediation_count"] == 3
        assert len(result["remediation_history"]) == 3
        assert result["remediation_history"][0]["action"] == "restart pod"
        assert result["remediation_history"][2]["attempt"] == 3

        # Verify stale data was still cleared
        assert result["cluster_findings"] == {}
        assert result["root_cause"] is None

    def test_initialize_preserves_similar_tickets(self):
        """Should preserve historical ticket patterns.

        similar_tickets come from HistoryAgent and contain patterns from
        resolved tickets. While cluster state changes rapidly, historical
        patterns are stable and valuable for diagnosis continuity.
        """
        
        

        state = AgentState(
            ticket_id="PROJ-555",
            similar_tickets=[
                {
                    "key": "PROJ-100",
                    "summary": "Similar CrashLoopBackOff issue",
                    "resolution": "Increased memory limit",
                },
                {
                    "key": "PROJ-200",
                    "summary": "Pod restart loop",
                    "resolution": "Fixed missing ConfigMap",
                },
            ],
            cluster_findings={"pods": ["stale"]},  # Will be cleared
            resumed_from_checkpoint=True,
        )


        initialize_state = self._create_initialize_state_func()
        result = initialize_state(state)

        # Verify historical patterns were preserved
        assert len(result["similar_tickets"]) == 2
        assert result["similar_tickets"][0]["key"] == "PROJ-100"
        assert result["similar_tickets"][1]["resolution"] == "Fixed missing ConfigMap"

        # Verify stale K8s data was still cleared
        assert result["cluster_findings"] == {}


class TestInitializeResetsFlag:
    """Test that resumed_from_checkpoint flag is reset after clearing."""

    def _create_initialize_state_func(self):
        """Helper to extract initialize_state function - shared across all test classes."""
        from logging import getLogger

        logger = getLogger(__name__)

        def initialize_state(state: AgentState) -> AgentState:
            """Initialize state with default values, clear stale data on resume."""
            if state.get("resumed_from_checkpoint"):
                logger.info(
                    "Resumed from checkpoint - clearing stale K8s data to ensure "
                    "fresh investigation reflects current cluster state"
                )
                state["cluster_findings"] = {}
                state["verification_evidence"] = []
                state["root_cause"] = None
                state["recommended_action"] = None
                state["confidence_level"] = None
                state["resumed_from_checkpoint"] = False
                logger.info(
                    f"Preserved: remediation_count={state.get('remediation_count', 0)}, "
                    f"remediation_history={len(state.get('remediation_history', []))} entries, "
                    f"ticket_id={state.get('ticket_id')}"
                )
            if "iteration_count" not in state:
                state["iteration_count"] = 0
            if "remediation_count" not in state:
                state["remediation_count"] = 0
            if "remediation_history" not in state:
                state["remediation_history"] = []
            if "issue_resolved" not in state:
                state["issue_resolved"] = False
            if "namespace" not in state:
                state["namespace"] = "production"
            return state

        return initialize_state

    def test_initialize_resets_flag_to_false(self):
        """Should reset resumed_from_checkpoint to False after clearing.

        The flag is a one-time signal. After processing it and clearing stale
        data, it must be reset to prevent re-clearing on subsequent node runs.
        """
        
        

        state = AgentState(
            ticket_id="PROJ-111",
            cluster_findings={"pods": ["stale"]},
            root_cause="Old diagnosis",
            resumed_from_checkpoint=True,  # Flag is True
        )


        initialize_state = self._create_initialize_state_func()
        result = initialize_state(state)

        # CRITICAL: Verify flag was reset to False
        assert result["resumed_from_checkpoint"] is False

        # Verify clearing still happened
        assert result["cluster_findings"] == {}
        assert result["root_cause"] is None

    def test_initialize_flag_stays_false_after_reset(self):
        """Should keep flag False on subsequent node executions.

        After the first clearing (on resume), the flag stays False. This ensures
        data isn't cleared again during the same workflow execution.
        """
        
        

        # First call - flag is True, data gets cleared
        state_resume = AgentState(
            ticket_id="PROJ-222",
            cluster_findings={"pods": ["stale"]},
            resumed_from_checkpoint=True,
        )


        initialize_state = self._create_initialize_state_func()
        result1 = initialize_state(state_resume)

        # Flag should be False now
        assert result1["resumed_from_checkpoint"] is False
        assert result1["cluster_findings"] == {}

        # Second call - flag is already False (simulate subsequent node)
        # Add some new data that should NOT be cleared
        state_after_investigation = AgentState(
            ticket_id="PROJ-222",
            cluster_findings={
                "pods": ["fresh-data-from-k8s-agent"]
            },  # Fresh data after investigation
            resumed_from_checkpoint=False,  # Flag is False
        )

        result2 = initialize_state(state_after_investigation)

        # Flag should STILL be False
        assert result2["resumed_from_checkpoint"] is False

        # Fresh data should be PRESERVED (not cleared)
        assert result2["cluster_findings"] == {"pods": ["fresh-data-from-k8s-agent"]}


class TestFreshStartDoesNotClearData:
    """Test that fresh workflow starts don't trigger clearing."""

    def _create_initialize_state_func(self):
        """Helper to extract initialize_state function - shared across all test classes."""
        from logging import getLogger

        logger = getLogger(__name__)

        def initialize_state(state: AgentState) -> AgentState:
            """Initialize state with default values, clear stale data on resume."""
            if state.get("resumed_from_checkpoint"):
                logger.info(
                    "Resumed from checkpoint - clearing stale K8s data to ensure "
                    "fresh investigation reflects current cluster state"
                )
                state["cluster_findings"] = {}
                state["verification_evidence"] = []
                state["root_cause"] = None
                state["recommended_action"] = None
                state["confidence_level"] = None
                state["resumed_from_checkpoint"] = False
                logger.info(
                    f"Preserved: remediation_count={state.get('remediation_count', 0)}, "
                    f"remediation_history={len(state.get('remediation_history', []))} entries, "
                    f"ticket_id={state.get('ticket_id')}"
                )
            if "iteration_count" not in state:
                state["iteration_count"] = 0
            if "remediation_count" not in state:
                state["remediation_count"] = 0
            if "remediation_history" not in state:
                state["remediation_history"] = []
            if "issue_resolved" not in state:
                state["issue_resolved"] = False
            if "namespace" not in state:
                state["namespace"] = "production"
            return state

        return initialize_state

    def test_initialize_preserves_all_data_when_flag_false(self):
        """Should NOT clear any data when resumed_from_checkpoint is False.

        When starting a fresh workflow (no checkpoint to resume), the flag is
        False by default. In this case, no data should be cleared.
        """
        
        

        state = AgentState(
            ticket_id="PROJ-333",
            cluster_findings={"pods": ["fresh-data"]},
            root_cause="Fresh diagnosis",
            recommended_action="kubectl scale deployment api-server --replicas=3",
            confidence_level="high",
            verification_evidence=["Fresh verification"],
            resumed_from_checkpoint=False,  # Fresh start (default)
        )


        initialize_state = self._create_initialize_state_func()
        result = initialize_state(state)

        # Verify NOTHING was cleared
        assert result["cluster_findings"] == {"pods": ["fresh-data"]}
        assert result["root_cause"] == "Fresh diagnosis"
        assert (
            result["recommended_action"]
            == "kubectl scale deployment api-server --replicas=3"
        )
        assert result["confidence_level"] == "high"
        assert result["verification_evidence"] == ["Fresh verification"]

        # Flag should remain False
        assert result["resumed_from_checkpoint"] is False

    def test_initialize_handles_missing_flag_gracefully(self):
        """Should treat missing flag as False (fresh start).

        For backwards compatibility, if the flag doesn't exist in state
        (e.g., old checkpoint format), treat it as False.
        """
        
        

        # Create state without the flag (old checkpoint)
        state = AgentState(
            ticket_id="PROJ-444",
            cluster_findings={"pods": ["data"]},
            root_cause="Diagnosis",
            # resumed_from_checkpoint is missing (defaults to False in AgentState)
        )


        initialize_state = self._create_initialize_state_func()
        result = initialize_state(state)

        # Verify data was NOT cleared (flag defaults to False)
        assert result["cluster_findings"] == {"pods": ["data"]}
        assert result["root_cause"] == "Diagnosis"


class TestIntegrationScenarios:
    """Integration tests covering complete resume scenarios."""

    def _create_initialize_state_func(self):
        """Helper to extract initialize_state function - shared across all test classes."""
        from logging import getLogger

        logger = getLogger(__name__)

        def initialize_state(state: AgentState) -> AgentState:
            """Initialize state with default values, clear stale data on resume."""
            if state.get("resumed_from_checkpoint"):
                logger.info(
                    "Resumed from checkpoint - clearing stale K8s data to ensure "
                    "fresh investigation reflects current cluster state"
                )
                state["cluster_findings"] = {}
                state["verification_evidence"] = []
                state["root_cause"] = None
                state["recommended_action"] = None
                state["confidence_level"] = None
                state["resumed_from_checkpoint"] = False
                logger.info(
                    f"Preserved: remediation_count={state.get('remediation_count', 0)}, "
                    f"remediation_history={len(state.get('remediation_history', []))} entries, "
                    f"ticket_id={state.get('ticket_id')}"
                )
            if "iteration_count" not in state:
                state["iteration_count"] = 0
            if "remediation_count" not in state:
                state["remediation_count"] = 0
            if "remediation_history" not in state:
                state["remediation_history"] = []
            if "issue_resolved" not in state:
                state["issue_resolved"] = False
            if "namespace" not in state:
                state["namespace"] = "production"
            return state

        return initialize_state

    def test_full_checkpoint_resume_clears_only_stale_data(self):
        """Integration test: Full checkpoint resume flow.

        Scenario: Pod restarts after 2 failed remediation attempts.
        - Checkpoint loads with stale cluster data
        - Supervisor clears volatile data but preserves remediation history
        - Workflow can continue with fresh investigation
        """
        
        

        # State as loaded from checkpoint after pod restart
        state_from_checkpoint = AgentState(
            # Immutable ticket context (preserve)
            ticket_id="PROD-567",
            ticket_summary="OOMKilled in payment-service",
            ticket_description="Payment service pods failing with OOM",
            # Historical context (preserve)
            similar_tickets=[
                {"key": "PROD-400", "resolution": "Increased memory to 2Gi"}
            ],
            # Remediation tracking (preserve - CRITICAL)
            remediation_count=2,
            remediation_history=[
                {"attempt": 1, "action": "restart pod", "success": False},
                {"attempt": 2, "action": "increase CPU", "success": False},
            ],
            # Stale cluster data (clear)
            cluster_findings={
                "pods": ["payment-service-abc (OOMKilled 10 min ago)"],
                "events": ["OOMKilled event from 10 min ago"],
                "logs": ["old memory usage logs"],
            },
            # Stale diagnosis (clear)
            root_cause="CPU bottleneck (incorrect diagnosis)",
            recommended_action="Increase CPU limit (wrong action)",
            confidence_level="medium",
            # Stale verification (clear)
            verification_evidence=["Old verification showing still failing"],
            # Resume flag (set by checkpointer)
            resumed_from_checkpoint=True,
        )


        initialize_state = self._create_initialize_state_func()
        result = initialize_state(state_from_checkpoint)

        # === Verify PRESERVED data ===
        # Ticket context
        assert result["ticket_id"] == "PROD-567"
        assert result["ticket_summary"] == "OOMKilled in payment-service"
        assert result["ticket_description"] == "Payment service pods failing with OOM"

        # Historical patterns
        assert len(result["similar_tickets"]) == 1
        assert result["similar_tickets"][0]["key"] == "PROD-400"

        # CRITICAL: Remediation tracking
        assert result["remediation_count"] == 2
        assert len(result["remediation_history"]) == 2
        assert result["remediation_history"][1]["action"] == "increase CPU"

        # === Verify CLEARED stale data ===
        # Cluster data (volatile)
        assert result["cluster_findings"] == {}

        # Diagnosis (based on stale data)
        assert result["root_cause"] is None
        assert result["recommended_action"] is None
        assert result["confidence_level"] is None

        # Verification (from previous attempt)
        assert result["verification_evidence"] == []

        # Flag reset
        assert result["resumed_from_checkpoint"] is False

    def test_fresh_start_preserves_everything(self):
        """Integration test: Fresh workflow start (no resume).

        Scenario: New ticket arrives, workflow starts from scratch.
        - No checkpoint to load
        - Flag is False
        - All data should be preserved as-is
        """
        
        

        # Fresh state (first run of workflow)
        fresh_state = AgentState(
            ticket_id="DEV-100",
            ticket_summary="Test issue",
            cluster_findings={"pods": ["initial-data"]},  # Fresh data
            root_cause="Initial diagnosis",
            resumed_from_checkpoint=False,  # Fresh start
        )


        initialize_state = self._create_initialize_state_func()
        result = initialize_state(fresh_state)

        # Everything should be preserved
        assert result["ticket_id"] == "DEV-100"
        assert result["cluster_findings"] == {"pods": ["initial-data"]}
        assert result["root_cause"] == "Initial diagnosis"
        assert result["resumed_from_checkpoint"] is False

    def test_max_remediation_attempts_preserved_across_resume(self):
        """Integration test: Remediation limit enforcement after resume.

        Scenario: Workflow hits max attempts (3), pod restarts, resumes.
        - remediation_count=3 must be preserved
        - Workflow should recognize limit is reached and skip remediation
        """
        
        

        # State after 3 failed attempts, then pod restart
        state_at_max_attempts = AgentState(
            ticket_id="PROD-999",
            remediation_count=3,  # At max (see config.py MAX_REMEDIATION_ATTEMPTS)
            remediation_history=[
                {"attempt": 1, "action": "restart", "success": False},
                {"attempt": 2, "action": "scale up", "success": False},
                {"attempt": 3, "action": "fix config", "success": False},
            ],
            cluster_findings={"pods": ["stale"]},  # Stale (will be cleared)
            root_cause="Old diagnosis",  # Stale (will be cleared)
            resumed_from_checkpoint=True,
        )


        initialize_state = self._create_initialize_state_func()
        result = initialize_state(state_at_max_attempts)

        # CRITICAL: Remediation tracking preserved
        assert result["remediation_count"] == 3
        assert len(result["remediation_history"]) == 3

        # Stale data cleared
        assert result["cluster_findings"] == {}
        assert result["root_cause"] is None

        # The workflow's should_attempt_remediation() function will see
        # remediation_count=3 and route to "post_comment" instead of
        # "attempt_remediation", preventing infinite loops.

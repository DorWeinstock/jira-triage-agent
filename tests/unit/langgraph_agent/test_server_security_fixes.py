"""Unit tests for security and performance fixes in server.py.

Tests for:
1. Ticket ID validation (Fix #4: ConfigMap injection prevention)
2. Rate limiting configuration (Fix #5: HITL endpoints)
3. Graph caching at startup (Fix #3: Performance)
4. Checkpointer null-safety (Fix #1: Null safety)
"""

import os
import re
import pytest
import sys
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from fastapi import HTTPException


# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../langgraph-agent/src'))


# ============================================================================
# Test Suite 1: Ticket ID Validation (Fix #4)
# ============================================================================

class TestTicketIDValidation:
    """Test ticket_id validation regex and validator function."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup before each test - import server fresh."""
        # Clear cache to reload module fresh
        if 'src.server' in sys.modules:
            del sys.modules['src.server']
        
        from src import server
        self.server = server
        yield
    
    def test_valid_ticket_ids(self):
        """Test that valid ticket IDs pass validation."""
        valid_ids = [
            "PROJ-123",      # Standard format
            "infra-42",      # Lowercase project
            "BUG-1",         # Single digit
            "MYAPP-999999",  # Large numbers
            "AB-1",          # Minimal format (two letters minimum before dash)
            "ABC-1",         # Three-letter project
        ]
        
        for ticket_id in valid_ids:
            # Should not raise exception
            self.server._validate_ticket_id(ticket_id)
    
    def test_invalid_ticket_ids(self):
        """Test that invalid ticket IDs raise HTTPException."""
        invalid_ids = [
            "-123",           # No project prefix
            "PROJ-",          # No number
            "123-PROJ",       # Reversed format
            "PROJ_123",       # Underscore instead of dash
            "PROJ 123",       # Space
            "PROJ--123",      # Double dash
            "1PROJ-123",      # Starts with number
            "",               # Empty
            "PROJ-12a",       # Number contains letter
            "PROJ-123-extra", # Too many dashes
        ]
        
        for ticket_id in invalid_ids:
            with pytest.raises(HTTPException) as exc_info:
                self.server._validate_ticket_id(ticket_id)
            
            assert exc_info.value.status_code == 422
            assert "Invalid ticket_id" in exc_info.value.detail
    
    def test_validation_regex_pattern(self):
        """Test that validation regex matches expected pattern."""
        regex = self.server._TICKET_ID_RE
        
        # Valid
        assert regex.match("PROJ-123") is not None
        assert regex.match("AB-1") is not None
        assert regex.match("ABC-999") is not None
        assert regex.match("infra-42") is not None
        
        # Invalid
        assert regex.match("1PROJ-123") is None      # Starts with number
        assert regex.match("-123") is None            # Starts with dash
        assert regex.match("PROJ-") is None           # No numbers
        assert regex.match("PROJ123") is None         # No dash
        assert regex.match("PROJ_123") is None        # Underscore
        assert regex.match("A-1") is None             # Only one letter (needs at least 2 chars before dash)


# ============================================================================
# Test Suite 2: Rate Limiting Configuration (Fix #5)
# ============================================================================

class TestRateLimitConfiguration:
    """Test rate limit configuration for HITL and investigate endpoints."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup before each test."""
        if 'src.server' in sys.modules:
            del sys.modules['src.server']
        
        from src import server
        self.server = server
        yield
    
    def test_rate_limit_env_vars_set(self):
        """Test that rate limit env vars are properly configured."""
        # Default values
        assert self.server.RATE_LIMIT == "10/minute"  # INVESTIGATE_RATE_LIMIT
        assert self.server.HITL_RATE_LIMIT == "60/minute"  # HITL_RATE_LIMIT
    
    def test_hitl_rate_limit_higher_than_investigate(self):
        """Test that HITL rate limit is higher than investigate limit."""
        # Extract numbers from rate limit strings (e.g., "10/minute" -> 10)
        investigate_limit = int(self.server.RATE_LIMIT.split('/')[0])
        hitl_limit = int(self.server.HITL_RATE_LIMIT.split('/')[0])
        
        assert hitl_limit > investigate_limit, \
            f"HITL limit ({hitl_limit}) should be higher than investigate limit ({investigate_limit})"
    
    def test_limiter_configured(self):
        """Test that limiter is properly configured."""
        assert self.server.limiter is not None
        assert hasattr(self.server.limiter, 'limit')  # Has limit decorator method


# ============================================================================
# Test Suite 3: Constants in config.py (Fix: upstream dependency)
# ============================================================================

class TestConfigConstants:
    """Test that config.py properly exports required constants."""
    
    def test_max_context_chars_available(self):
        """Test that MAX_CONTEXT_CHARS constant is available."""
        from src.config import MAX_CONTEXT_CHARS
        
        # Should be a dict with expected keys
        assert isinstance(MAX_CONTEXT_CHARS, dict)
        assert "logs" in MAX_CONTEXT_CHARS
        assert "events" in MAX_CONTEXT_CHARS
        assert "cluster_findings" in MAX_CONTEXT_CHARS
        assert "deployment_status" in MAX_CONTEXT_CHARS
        assert "description" in MAX_CONTEXT_CHARS
        assert "similar_tickets" in MAX_CONTEXT_CHARS
        
        # Values should be positive integers
        for key, value in MAX_CONTEXT_CHARS.items():
            assert isinstance(value, int), f"{key} should be int, got {type(value)}"
            assert value > 0, f"{key} should be positive, got {value}"
    
    def test_max_similar_tickets_available(self):
        """Test that MAX_SIMILAR_TICKETS constant is available."""
        from src.config import MAX_SIMILAR_TICKETS
        
        # Should be a positive integer
        assert isinstance(MAX_SIMILAR_TICKETS, int)
        assert MAX_SIMILAR_TICKETS > 0


# ============================================================================
# Test Suite 4: Server Configuration
# ============================================================================

class TestServerConfiguration:
    """Test server module configuration."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup before each test."""
        if 'src.server' in sys.modules:
            del sys.modules['src.server']
        
        from src import server
        self.server = server
        yield
    
    def test_config_imports_complete(self):
        """Test that all required imports are available."""
        # Check that all security components are imported
        assert hasattr(self.server, '_validate_ticket_id')
        assert hasattr(self.server, 'RATE_LIMIT')
        assert hasattr(self.server, 'HITL_RATE_LIMIT')
        assert hasattr(self.server, 'limiter')
        assert hasattr(self.server, '_TICKET_ID_RE')
    
    def test_ticket_id_regex_compiled(self):
        """Test that ticket ID regex is properly compiled."""
        # Should be a compiled regex pattern
        assert hasattr(self.server._TICKET_ID_RE, 'match')
        assert hasattr(self.server._TICKET_ID_RE, 'pattern')
        
        # Pattern should match expected format
        assert self.server._TICKET_ID_RE.pattern == r'^[A-Za-z][A-Za-z0-9]+-\d+$'
    
    def test_graph_caching_globals_exist(self):
        """Test that graph caching globals are defined."""
        assert hasattr(self.server, '_graph_hitl_off')
        assert hasattr(self.server, '_graph_hitl_on')
        assert hasattr(self.server, '_checkpointer')
    
    def test_checkpointer_creation_function_exists(self):
        """Test that checkpointer creation function exists."""
        assert hasattr(self.server, '_create_checkpointer_if_needed')
        assert callable(self.server._create_checkpointer_if_needed)
    
    def test_lock_service_creation_function_exists(self):
        """Test that lock service creation function exists."""
        assert hasattr(self.server, '_create_lock_service')
        assert callable(self.server._create_lock_service)


# ============================================================================
# Edge Cases
# ============================================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup before each test."""
        if 'src.server' in sys.modules:
            del sys.modules['src.server']
        
        from src import server
        self.server = server
        yield
    
    def test_very_long_ticket_id(self):
        """Test validation with very long ticket ID."""
        # Valid long ticket ID
        long_id = "PROJECT" + "X" * 1000 + "-999999"
        self.server._validate_ticket_id(long_id)  # Should not raise
    
    def test_unicode_in_ticket_id_rejected(self):
        """Test that unicode characters are rejected."""
        unicode_ids = [
            "PROJ-123é",
            "PROJ™-123",
            "PROJ-123ñ",
        ]
        
        for ticket_id in unicode_ids:
            with pytest.raises(HTTPException):
                self.server._validate_ticket_id(ticket_id)
    
    def test_special_chars_in_ticket_id_rejected(self):
        """Test that special characters are rejected."""
        special_ids = [
            "PROJ!-123",
            "PROJ@-123",
            "PROJ#-123",
            "PROJ$-123",
            "PROJ%-123",
        ]
        
        for ticket_id in special_ids:
            with pytest.raises(HTTPException):
                self.server._validate_ticket_id(ticket_id)
    
    def test_whitespace_in_ticket_id_rejected(self):
        """Test that whitespace is rejected."""
        whitespace_ids = [
            "PROJ -123",
            " PROJ-123",
            "PROJ-123 ",
            "PROJ\t-123",
        ]
        
        for ticket_id in whitespace_ids:
            with pytest.raises(HTTPException):
                self.server._validate_ticket_id(ticket_id)
    
    def test_case_sensitive_validation(self):
        """Test that validation is case-insensitive for alphanumeric part."""
        # Both upper and lowercase should be valid
        self.server._validate_ticket_id("PROJ-123")  # uppercase
        self.server._validate_ticket_id("proj-123")  # lowercase
        self.server._validate_ticket_id("PrOj-123")  # mixed case
        self.server._validate_ticket_id("AB-123")    # two letters minimum


# ============================================================================
# Integration Tests
# ============================================================================

class TestSecurityIntegration:
    """Integration tests for all security fixes."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup before each test."""
        if 'src.server' in sys.modules:
            del sys.modules['src.server']
        
        from src import server
        self.server = server
        yield
    
    def test_validation_works_with_investigate_request(self):
        """Test that validation can be used with investigation requests."""
        # Valid ID should work
        valid_id = "PROJ-123"
        self.server._validate_ticket_id(valid_id)
        
        # Invalid ID should reject
        invalid_id = "invalid!@#"
        with pytest.raises(HTTPException) as exc:
            self.server._validate_ticket_id(invalid_id)
        assert exc.value.status_code == 422
    
    def test_rate_limit_configuration_complete(self):
        """Test that both rate limits are configured."""
        # Both should be defined
        assert hasattr(self.server, 'RATE_LIMIT')
        assert hasattr(self.server, 'HITL_RATE_LIMIT')
        
        # Both should be valid rate limit strings
        assert '/' in self.server.RATE_LIMIT
        assert '/' in self.server.HITL_RATE_LIMIT
        
        # HITL should be higher than investigate
        invest_num = int(self.server.RATE_LIMIT.split('/')[0])
        hitl_num = int(self.server.HITL_RATE_LIMIT.split('/')[0])
        assert hitl_num > invest_num
    
    def test_config_constants_work_with_agents(self):
        """Test that config constants can be used by agents."""
        from src.config import MAX_CONTEXT_CHARS, MAX_SIMILAR_TICKETS
        
        # These should be usable by agent code
        logs_limit = MAX_CONTEXT_CHARS["logs"]
        events_limit = MAX_CONTEXT_CHARS["events"]
        similar_tickets = MAX_SIMILAR_TICKETS
        
        # Should work as expected
        assert isinstance(logs_limit, int)
        assert isinstance(events_limit, int)
        assert isinstance(similar_tickets, int)
        
        # Example usage patterns (as in actual agents)
        test_logs = "a" * (logs_limit + 100)
        truncated_logs = test_logs[:logs_limit]
        assert len(truncated_logs) == logs_limit

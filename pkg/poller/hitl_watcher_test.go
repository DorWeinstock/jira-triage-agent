package poller

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
	"jira-triage-agent/pkg/mcp/jira"
)

func TestNewHITLWatcher(t *testing.T) {
	watcher := NewHITLWatcher(
		nil, // jiraClient
		"http://localhost:8080",
		"bot@example.com",
		30*time.Second,
		8*time.Hour,
		zap.NewNop(),
	)

	assert.NotNil(t, watcher)
	assert.Equal(t, "http://localhost:8080", watcher.langgraphURL)
	assert.Equal(t, "bot@example.com", watcher.botEmail)
	assert.Equal(t, 30*time.Second, watcher.pollInterval)
	assert.Equal(t, 8*time.Hour, watcher.hitlTimeout)
}

func TestAddPending(t *testing.T) {
	watcher := NewHITLWatcher(nil, "", "", 30*time.Second, 8*time.Hour, zap.NewNop())

	watcher.AddPending("SP-1234", "thread-1", time.Now())

	pending, exists := watcher.GetPending("SP-1234")
	require.True(t, exists)
	assert.Equal(t, "SP-1234", pending.TicketID)
	assert.Equal(t, "thread-1", pending.ThreadID)
}

func TestGetPending_ReturnsCopy(t *testing.T) {
	watcher := NewHITLWatcher(nil, "", "", 30*time.Second, 8*time.Hour, zap.NewNop())

	watcher.AddPending("SP-1234", "thread-1", time.Now())

	// Get pending twice
	pending1, _ := watcher.GetPending("SP-1234")
	pending2, _ := watcher.GetPending("SP-1234")

	// Modify the first copy
	pending1.ThreadID = "modified"

	// Second copy should be unaffected (proves it's a copy)
	assert.Equal(t, "thread-1", pending2.ThreadID)
}

func TestRemovePending(t *testing.T) {
	watcher := NewHITLWatcher(nil, "", "", 30*time.Second, 8*time.Hour, zap.NewNop())

	watcher.AddPending("SP-1234", "thread-1", time.Now())
	watcher.RemovePending("SP-1234")

	_, exists := watcher.GetPending("SP-1234")
	assert.False(t, exists)
}

func TestIsTimedOut(t *testing.T) {
	watcher := NewHITLWatcher(nil, "", "", 30*time.Second, 1*time.Hour, zap.NewNop())

	// Not timed out
	recent := time.Now().Add(-30 * time.Minute)
	watcher.AddPending("SP-1234", "thread-1", recent)
	pending, _ := watcher.GetPending("SP-1234")
	assert.False(t, watcher.IsTimedOut(pending))

	// Timed out
	old := time.Now().Add(-2 * time.Hour)
	watcher.AddPending("SP-5678", "thread-2", old)
	pending2, _ := watcher.GetPending("SP-5678")
	assert.True(t, watcher.IsTimedOut(pending2))
}

func TestParseApprovalComment(t *testing.T) {
	watcher := NewHITLWatcher(nil, "", "", 30*time.Second, 8*time.Hour, zap.NewNop())

	tests := []struct {
		name     string
		body     string
		expected ApprovalResult
	}{
		{
			name:     "simple approve",
			body:     "approve",
			expected: ApprovalResult{Action: ActionApprove},
		},
		{
			name:     "approve with whitespace",
			body:     "  approve  ",
			expected: ApprovalResult{Action: ActionApprove},
		},
		{
			name:     "approve uppercase",
			body:     "APPROVE",
			expected: ApprovalResult{Action: ActionApprove},
		},
		{
			name:     "approve mixed case",
			body:     "Approve",
			expected: ApprovalResult{Action: ActionApprove},
		},
		{
			name:     "reject with reason",
			body:     "reject: need more investigation",
			expected: ApprovalResult{Action: ActionReject, Reason: "need more investigation"},
		},
		{
			name:     "reject uppercase",
			body:     "REJECT: Not ready yet",
			expected: ApprovalResult{Action: ActionReject, Reason: "Not ready yet"},
		},
		{
			name:     "reject with extra whitespace",
			body:     "  reject:   too risky   ",
			expected: ApprovalResult{Action: ActionReject, Reason: "too risky"},
		},
		{
			name:     "no match - random text",
			body:     "What is the status?",
			expected: ApprovalResult{Action: ActionNone},
		},
		{
			name:     "no match - partial approve",
			body:     "I approve of this",
			expected: ApprovalResult{Action: ActionNone},
		},
		// Jira markup support
		{
			name:     "approve with Jira inline code markup",
			body:     "{{approve}}",
			expected: ApprovalResult{Action: ActionApprove},
		},
		{
			name:     "approve with Jira inline code and whitespace",
			body:     "  {{approve}}  ",
			expected: ApprovalResult{Action: ActionApprove},
		},
		{
			name:     "reject with Jira inline code markup",
			body:     "{{reject: not ready}}",
			expected: ApprovalResult{Action: ActionReject, Reason: "not ready"},
		},
		{
			name:     "reject with Jira markup and extra whitespace",
			body:     "  {{reject:   too risky  }}  ",
			expected: ApprovalResult{Action: ActionReject, Reason: "too risky"},
		},
		// Bare reject (no reason)
		{
			name:     "bare reject",
			body:     "reject",
			expected: ApprovalResult{Action: ActionReject, Reason: ""},
		},
		{
			name:     "bare reject uppercase",
			body:     "REJECT",
			expected: ApprovalResult{Action: ActionReject, Reason: ""},
		},
		{
			name:     "bare reject with whitespace",
			body:     "  reject  ",
			expected: ApprovalResult{Action: ActionReject, Reason: ""},
		},
		{
			name:     "bare reject Jira markup",
			body:     "{{reject}}",
			expected: ApprovalResult{Action: ActionReject, Reason: ""},
		},
		// Edge cases - malformed braces should NOT match
		{
			name:     "malformed - missing closing braces",
			body:     "{{approve",
			expected: ApprovalResult{Action: ActionNone},
		},
		{
			name:     "malformed - missing opening braces",
			body:     "approve}}",
			expected: ApprovalResult{Action: ActionNone},
		},
		{
			name:     "no match - reject colon no reason",
			body:     "reject:",
			expected: ApprovalResult{Action: ActionNone},
		},
		{
			name:     "no match - rejected (partial word)",
			body:     "rejected: this",
			expected: ApprovalResult{Action: ActionNone},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := watcher.ParseComment(tt.body)
			assert.Equal(t, tt.expected.Action, result.Action)
			assert.Equal(t, tt.expected.Reason, result.Reason)
		})
	}
}

func TestResumeWorkflow_Approve(t *testing.T) {
	var receivedPath, receivedMethod string
	var receivedBody []byte

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedPath = r.URL.Path
		receivedMethod = r.Method
		receivedBody, _ = io.ReadAll(r.Body)
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"status": "completed"}`))
	}))
	defer server.Close()

	watcher := NewHITLWatcher(nil, server.URL, "", 30*time.Second, 8*time.Hour, zap.NewNop())

	err := watcher.ResumeWorkflow(context.Background(), "SP-1234", ApprovalResult{Action: ActionApprove})

	require.NoError(t, err)
	assert.Equal(t, "/investigate/SP-1234/approve", receivedPath)
	assert.Equal(t, "POST", receivedMethod)
	assert.Contains(t, string(receivedBody), "attempt_remediation")
}

func TestResumeWorkflow_Reject(t *testing.T) {
	var receivedPath string
	var receivedBody []byte

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedPath = r.URL.Path
		receivedBody, _ = io.ReadAll(r.Body)
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"status": "rejected"}`))
	}))
	defer server.Close()

	watcher := NewHITLWatcher(nil, server.URL, "", 30*time.Second, 8*time.Hour, zap.NewNop())

	err := watcher.ResumeWorkflow(context.Background(), "SP-1234", ApprovalResult{
		Action: ActionReject,
		Reason: "too risky",
	})

	require.NoError(t, err)
	assert.Equal(t, "/investigate/SP-1234/reject", receivedPath)
	assert.Contains(t, string(receivedBody), "too risky")
}

func TestResumeWorkflow_Timeout(t *testing.T) {
	var receivedBody []byte

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedBody, _ = io.ReadAll(r.Body)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	watcher := NewHITLWatcher(nil, server.URL, "", 30*time.Second, 8*time.Hour, zap.NewNop())

	err := watcher.ResumeWorkflow(context.Background(), "SP-1234", ApprovalResult{
		Action: ActionReject,
		Reason: "HITL timeout (8h)",
	})

	require.NoError(t, err)
	assert.Contains(t, string(receivedBody), "timeout")
}

func TestResumeWorkflow_HTTPError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte(`{"error": "database connection failed"}`))
	}))
	defer server.Close()

	watcher := NewHITLWatcher(nil, server.URL, "", 30*time.Second, 8*time.Hour, zap.NewNop())

	err := watcher.ResumeWorkflow(context.Background(), "SP-1234", ApprovalResult{Action: ActionApprove})

	require.Error(t, err)
	assert.Contains(t, err.Error(), "500")
	assert.Contains(t, err.Error(), "database connection failed")
}

func TestResumeWorkflow_InvalidAction(t *testing.T) {
	watcher := NewHITLWatcher(nil, "http://localhost", "", 30*time.Second, 8*time.Hour, zap.NewNop())

	err := watcher.ResumeWorkflow(context.Background(), "SP-1234", ApprovalResult{Action: ActionNone})

	require.Error(t, err)
	assert.Contains(t, err.Error(), "invalid action")
}

func TestGetTimedOutTickets(t *testing.T) {
	watcher := NewHITLWatcher(nil, "", "", 30*time.Second, 1*time.Hour, zap.NewNop())

	// Add a timed-out ticket
	old := time.Now().Add(-2 * time.Hour)
	watcher.AddPending("SP-TIMEOUT", "thread-1", old)

	// Add a fresh ticket
	watcher.AddPending("SP-FRESH", "thread-2", time.Now())

	timedOut := watcher.GetTimedOutTickets()

	assert.Len(t, timedOut, 1)
	assert.Equal(t, "SP-TIMEOUT", timedOut[0].TicketID)
}

func TestGetAllPending(t *testing.T) {
	watcher := NewHITLWatcher(nil, "", "", 30*time.Second, 8*time.Hour, zap.NewNop())

	watcher.AddPending("SP-1", "thread-1", time.Now())
	watcher.AddPending("SP-2", "thread-2", time.Now())

	all := watcher.GetAllPending()

	assert.Len(t, all, 2)
}

func TestShouldSkipComment(t *testing.T) {
	watcher := NewHITLWatcher(nil, "", "bot@example.com", 30*time.Second, 8*time.Hour, zap.NewNop())

	assert.True(t, watcher.ShouldSkipComment("bot@example.com"))
	assert.False(t, watcher.ShouldSkipComment("user@example.com"))
}

func TestHandleTimeout(t *testing.T) {
	var receivedPath string
	var receivedBody []byte

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedPath = r.URL.Path
		receivedBody, _ = io.ReadAll(r.Body)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	watcher := NewHITLWatcher(nil, server.URL, "", 30*time.Second, 1*time.Hour, zap.NewNop())

	// Add timed-out ticket
	old := time.Now().Add(-2 * time.Hour)
	watcher.AddPending("SP-TIMEOUT", "thread-1", old)

	// Handle timeout
	err := watcher.HandleTimeout(context.Background(), watcher.GetTimedOutTickets()[0])

	require.NoError(t, err)
	assert.Equal(t, "/investigate/SP-TIMEOUT/reject", receivedPath)
	assert.Contains(t, string(receivedBody), "timeout")

	// Ticket should be removed
	_, exists := watcher.GetPending("SP-TIMEOUT")
	assert.False(t, exists)
}

func TestIsCommentAfterRequest(t *testing.T) {
	w := &HITLWatcher{logger: zap.NewNop()}
	// Request time: 2024-01-15 10:30:00 UTC
	requestedAt := time.Date(2024, 1, 15, 10, 30, 0, 0, time.UTC)

	tests := []struct {
		name           string
		commentCreated string
		want           bool
	}{
		{
			name:           "comment 1 minute after request",
			commentCreated: "2024-01-15T10:31:00.000+0000",
			want:           true,
		},
		{
			name:           "comment 1 second after request",
			commentCreated: "2024-01-15T10:30:01.000+0000",
			want:           true,
		},
		{
			name:           "comment at exact request time",
			commentCreated: "2024-01-15T10:30:00.000+0000",
			want:           true,
		},
		{
			name:           "comment 1 second before request - REJECT",
			commentCreated: "2024-01-15T10:29:59.000+0000",
			want:           false,
		},
		{
			name:           "comment 1 hour before request - REJECT",
			commentCreated: "2024-01-15T09:30:00.000+0000",
			want:           false,
		},
		{
			name:           "malformed timestamp - fail safe reject",
			commentCreated: "not-a-timestamp",
			want:           false,
		},
		{
			name:           "empty timestamp - fail safe reject",
			commentCreated: "",
			want:           false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := w.IsCommentAfterRequest(tt.commentCreated, requestedAt)
			if got != tt.want {
				t.Errorf("IsCommentAfterRequest(%q) = %v, want %v",
					tt.commentCreated, got, tt.want)
			}
		})
	}
}

func TestCheckTicketComments_SkipsOldApproveComments(t *testing.T) {
	// This test verifies that approve comments from before the HITL request
	// are ignored (the security fix).

	requestedAt := time.Date(2024, 1, 15, 10, 30, 0, 0, time.UTC)

	// Track if approve endpoint was called (it shouldn't be for old comments)
	approveEndpointCalled := false

	// Mock server that handles both Jira API and LangGraph API
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Jira API: return ticket with an old "approve" comment
		if strings.Contains(r.URL.Path, "/rest/api/2/issue/TEST-123") {
			json.NewEncoder(w).Encode(map[string]interface{}{
				"key": "TEST-123",
				"fields": map[string]interface{}{
					"summary": "Test ticket",
					"comment": map[string]interface{}{
						"comments": []map[string]interface{}{
							{
								"id":      "old-comment",
								"body":    "approve",
								"created": "2024-01-15T09:00:00.000+0000", // 1.5 hours BEFORE request
								"author": map[string]interface{}{
									"displayName":  "Old User",
									"emailAddress": "old@example.com",
								},
							},
						},
					},
				},
			})
			return
		}

		// LangGraph API: approve endpoint (should NOT be called for old comments)
		if strings.Contains(r.URL.Path, "/investigate/TEST-123/approve") {
			approveEndpointCalled = true
			w.WriteHeader(http.StatusOK)
			w.Write([]byte(`{"status": "completed"}`))
			return
		}

		w.WriteHeader(http.StatusNotFound)
	}))
	defer server.Close()

	jiraClient := jira.NewClient(server.URL, "test-pat")
	watcher := NewHITLWatcher(jiraClient, server.URL, "bot@example.com", time.Minute, 8*time.Hour, zap.NewNop())

	watcher.AddPending("TEST-123", "thread-1", requestedAt)

	// Check comments - should NOT find the old approval
	err := watcher.checkTicketComments(context.Background(), PendingApproval{
		TicketID:    "TEST-123",
		ThreadID:    "thread-1",
		RequestedAt: requestedAt,
	})

	if err != nil {
		t.Fatalf("checkTicketComments returned error: %v", err)
	}

	// Ticket should still be pending (old approve was ignored)
	if _, exists := watcher.GetPending("TEST-123"); !exists {
		t.Error("ticket was removed from pending - old approve comment was incorrectly processed")
	}

	// The approve endpoint should NOT have been called
	if approveEndpointCalled {
		t.Error("approve endpoint was called - old approve comment was incorrectly processed")
	}
}

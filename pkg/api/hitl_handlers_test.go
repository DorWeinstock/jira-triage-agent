package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"

	"jira-triage-agent/pkg/poller"
)

func TestRegisterPendingHandler(t *testing.T) {
	watcher := poller.NewHITLWatcher(nil, "", "", 30*time.Second, 8*time.Hour, zap.NewNop())
	handler := NewHITLHandler(watcher, zap.NewNop())

	reqBody := RegisterPendingRequest{
		TicketID:    "SP-1234",
		ThreadID:    "thread-1",
		RequestedAt: time.Now().Format(time.RFC3339),
	}
	body, _ := json.Marshal(reqBody)

	req := httptest.NewRequest(http.MethodPost, "/hitl/pending", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	handler.RegisterPending(w, req)

	assert.Equal(t, http.StatusCreated, w.Code)

	// Verify ticket was registered
	pending, exists := watcher.GetPending("SP-1234")
	require.True(t, exists)
	assert.Equal(t, "SP-1234", pending.TicketID)
}

func TestRemovePendingHandler(t *testing.T) {
	watcher := poller.NewHITLWatcher(nil, "", "", 30*time.Second, 8*time.Hour, zap.NewNop())
	handler := NewHITLHandler(watcher, zap.NewNop())

	// Add a pending ticket first
	watcher.AddPending("SP-1234", "thread-1", time.Now())

	// Create router with chi for URL params
	r := chi.NewRouter()
	r.Delete("/hitl/pending/{ticketID}", handler.RemovePending)

	req := httptest.NewRequest(http.MethodDelete, "/hitl/pending/SP-1234", nil)
	w := httptest.NewRecorder()

	r.ServeHTTP(w, req)

	assert.Equal(t, http.StatusOK, w.Code)

	// Verify ticket was removed
	_, exists := watcher.GetPending("SP-1234")
	assert.False(t, exists)
}

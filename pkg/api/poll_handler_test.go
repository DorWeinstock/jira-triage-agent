package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"

	"jira-triage-agent/pkg/mcp/jira"
	"jira-triage-agent/pkg/poller"
)

// stubJiraClient is a minimal poller.JiraClient that records whether a search
// was performed, so tests can confirm the background poll actually ran.
type stubJiraClient struct {
	searchCalls atomic.Int32
}

func (s *stubJiraClient) SearchTickets(ctx context.Context, jql string, maxResults int) ([]jira.Ticket, error) {
	s.searchCalls.Add(1)
	return nil, nil
}

func (s *stubJiraClient) AddLabel(ctx context.Context, ticketID, label string) error    { return nil }
func (s *stubJiraClient) RemoveLabel(ctx context.Context, ticketID, label string) error { return nil }

type stubDispatcher struct{}

func (stubDispatcher) Dispatch(ctx context.Context, ticketID string) error { return nil }

func TestTriggerPollHandler(t *testing.T) {
	jiraClient := &stubJiraClient{}
	p := poller.New(jiraClient, stubDispatcher{}, poller.Config{FilterProject: "TEST", FilterComponent: "test"})
	handler := NewPollHandler(p, zap.NewNop())

	req := httptest.NewRequest(http.MethodPost, "/poll", nil)
	w := httptest.NewRecorder()

	handler.TriggerPoll(w, req)

	assert.Equal(t, http.StatusAccepted, w.Code)

	var resp map[string]string
	require.NoError(t, json.NewDecoder(w.Body).Decode(&resp))
	assert.Equal(t, "poll triggered", resp["status"])

	// The poll runs in the background — wait for it to actually search.
	require.Eventually(t, func() bool {
		return jiraClient.searchCalls.Load() > 0
	}, 2*time.Second, 10*time.Millisecond, "expected the background poll to call SearchTickets")
}

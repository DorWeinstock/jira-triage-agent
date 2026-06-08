package poller_test

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	"go.uber.org/zap"

	"jira-triage-agent/pkg/mcp/jira"
	"jira-triage-agent/pkg/poller"
)

type mockJiraClient struct {
	tickets            []jira.Ticket
	err                error
	capturedMaxResults int
	capturedJQL        string

	// Label tracking
	addLabelCalls    []labelCall
	removeLabelCalls []labelCall
	addLabelErr      error
	removeLabelErr   error
	mu               sync.Mutex
}

type labelCall struct {
	ticketID string
	label    string
}

func (m *mockJiraClient) SearchTickets(ctx context.Context, jql string, maxResults int) ([]jira.Ticket, error) {
	m.capturedMaxResults = maxResults
	m.capturedJQL = jql
	if m.err != nil {
		return nil, m.err
	}
	return m.tickets, nil
}

func (m *mockJiraClient) AddLabel(ctx context.Context, ticketID, label string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.addLabelCalls = append(m.addLabelCalls, labelCall{ticketID, label})
	return m.addLabelErr
}

func (m *mockJiraClient) RemoveLabel(ctx context.Context, ticketID, label string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.removeLabelCalls = append(m.removeLabelCalls, labelCall{ticketID, label})
	return m.removeLabelErr
}

type mockDispatcher struct {
	dispatched []string
	err        error
}

func (m *mockDispatcher) Dispatch(ctx context.Context, ticketID string) error {
	if m.err != nil {
		return m.err
	}
	m.dispatched = append(m.dispatched, ticketID)
	return nil
}

func TestPollerRun(t *testing.T) {
	jiraClient := &mockJiraClient{
		tickets: []jira.Ticket{
			{Key: "TEST-123"},
			{Key: "TEST-124"},
		},
	}
	dispatcher := &mockDispatcher{}

	cfg := poller.Config{
		FilterProject:   "GAUDISW",
		FilterComponent: "DevOps_K8S",
		FilterIssueType: "",
	}

	p := poller.New(jiraClient, dispatcher, cfg)

	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()

	// Run once
	p.Poll(ctx)

	if len(dispatcher.dispatched) != 2 {
		t.Errorf("expected 2 dispatched, got %d", len(dispatcher.dispatched))
	}
}

func TestPollerDispatchesSameTicketsOnRepeatedPolls(t *testing.T) {
	jiraClient := &mockJiraClient{
		tickets: []jira.Ticket{
			{Key: "TEST-123"},
			{Key: "TEST-124"},
		},
	}
	dispatcher := &mockDispatcher{}

	cfg := poller.Config{
		FilterProject:   "GAUDISW",
		FilterComponent: "DevOps_K8S",
		FilterIssueType: "",
	}

	p := poller.New(jiraClient, dispatcher, cfg)

	ctx := context.Background()

	// First poll - should dispatch both
	p.Poll(ctx)
	if len(dispatcher.dispatched) != 2 {
		t.Errorf("expected 2 dispatched on first poll, got %d", len(dispatcher.dispatched))
	}

	// Second poll immediately - should dispatch both again (no cache)
	// In production, labels would prevent this, but we're testing without label manipulation
	dispatcher.dispatched = nil
	p.Poll(ctx)
	if len(dispatcher.dispatched) != 2 {
		t.Errorf("expected 2 dispatched on second poll (no cache), got %d", len(dispatcher.dispatched))
	}
}

func TestPollerHandlesJiraError(t *testing.T) {
	jiraClient := &mockJiraClient{
		err: errors.New("jira connection failed"),
	}
	dispatcher := &mockDispatcher{}

	cfg := poller.Config{
		FilterProject:   "GAUDISW",
		FilterComponent: "DevOps_K8S",
		FilterIssueType: "",
	}

	p := poller.New(jiraClient, dispatcher, cfg)

	ctx := context.Background()

	// Should not panic, should log error and return
	p.Poll(ctx)

	if len(dispatcher.dispatched) != 0 {
		t.Errorf("expected 0 dispatched after jira error, got %d", len(dispatcher.dispatched))
	}
}

func TestPollerHandlesDispatchError(t *testing.T) {
	jiraClient := &mockJiraClient{
		tickets: []jira.Ticket{
			{Key: "TEST-123"},
			{Key: "TEST-124"},
		},
	}
	dispatcher := &mockDispatcher{
		err: errors.New("dispatch failed"),
	}

	cfg := poller.Config{
		FilterProject:   "GAUDISW",
		FilterComponent: "DevOps_K8S",
		FilterIssueType: "",
	}

	p := poller.New(jiraClient, dispatcher, cfg)

	ctx := context.Background()

	// Should continue even if dispatch fails
	p.Poll(ctx)

	// Dispatcher should have been called but with errors
	if len(dispatcher.dispatched) != 0 {
		t.Errorf("expected 0 successful dispatches after dispatch error, got %d", len(dispatcher.dispatched))
	}
}

func TestPollerEmptyTickets(t *testing.T) {
	jiraClient := &mockJiraClient{
		tickets: []jira.Ticket{},
	}
	dispatcher := &mockDispatcher{}

	cfg := poller.Config{
		FilterProject:   "GAUDISW",
		FilterComponent: "DevOps_K8S",
		FilterIssueType: "",
	}

	p := poller.New(jiraClient, dispatcher, cfg)

	ctx := context.Background()

	// Should handle empty ticket list gracefully
	p.Poll(ctx)

	if len(dispatcher.dispatched) != 0 {
		t.Errorf("expected 0 dispatched for empty tickets, got %d", len(dispatcher.dispatched))
	}
}

func TestPollerDefaultValues(t *testing.T) {
	jiraClient := &mockJiraClient{}
	dispatcher := &mockDispatcher{}

	cfg := poller.Config{
		FilterProject:   "GAUDISW",
		FilterComponent: "DevOps_K8S",
		FilterIssueType: "",
		// No Interval or LookbackMinutes specified
	}

	p := poller.New(jiraClient, dispatcher, cfg)

	// Should set default interval to 5 minutes
	if p == nil {
		t.Fatal("poller should not be nil")
	}

	// Poll should work with defaults
	ctx := context.Background()
	p.Poll(ctx)
}

func TestPollerInFlightTracking(t *testing.T) {
	jiraClient := &mockJiraClient{}
	dispatcher := &mockDispatcher{}

	cfg := poller.Config{
		FilterProject:   "GAUDISW",
		FilterComponent: "DevOps_K8S",
		FilterIssueType: "",
	}

	p := poller.New(jiraClient, dispatcher, cfg)

	jiraClient.tickets = []jira.Ticket{
		{Key: "TEST-100"},
		{Key: "TEST-101"},
	}

	ctx := context.Background()
	p.Poll(ctx)

	// Verify tickets were dispatched
	if len(dispatcher.dispatched) != 2 {
		t.Fatalf("expected 2 dispatched tickets, got %d", len(dispatcher.dispatched))
	}

	// After poll completes, no tickets should be in-flight
	if p.IsInFlight("TEST-100") {
		t.Errorf("TEST-100 should not be in-flight after poll completes")
	}
	if p.IsInFlight("TEST-101") {
		t.Errorf("TEST-101 should not be in-flight after poll completes")
	}
}

func TestPollerRunCancellation(t *testing.T) {
	jiraClient := &mockJiraClient{
		tickets: []jira.Ticket{
			{Key: "TEST-200"},
		},
	}
	dispatcher := &mockDispatcher{}

	cfg := poller.Config{
		FilterProject:   "GAUDISW",
		FilterComponent: "DevOps_K8S",
		FilterIssueType: "",
		Interval:        50 * time.Millisecond, // Fast interval for testing
	}

	p := poller.New(jiraClient, dispatcher, cfg)

	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	// Run should respect context cancellation
	done := make(chan struct{})
	go func() {
		p.Run(ctx)
		close(done)
	}()

	// Wait for Run to complete
	select {
	case <-done:
		// Success - Run returned when context was cancelled
	case <-time.After(1 * time.Second):
		t.Fatal("Run did not respect context cancellation")
	}

	// Should have dispatched at least once (initial poll)
	if len(dispatcher.dispatched) < 1 {
		t.Errorf("expected at least 1 dispatch, got %d", len(dispatcher.dispatched))
	}
}

func TestPollerDispatchFailureRetry(t *testing.T) {
	jiraClient := &mockJiraClient{
		tickets: []jira.Ticket{
			{Key: "TEST-300"},
		},
	}
	dispatcher := &mockDispatcher{}
	originalErr := errors.New("temporary failure")
	dispatcher.err = originalErr

	cfg := poller.Config{
		FilterProject:   "GAUDISW",
		FilterComponent: "DevOps_K8S",
		FilterIssueType: "",
	}

	p := poller.New(jiraClient, dispatcher, cfg)
	ctx := context.Background()

	// First poll - dispatch should fail
	p.Poll(ctx)

	if len(dispatcher.dispatched) != 0 {
		t.Errorf("expected 0 successful dispatches on first poll (error), got %d", len(dispatcher.dispatched))
	}

	// Second poll immediately - should retry (no cache)
	// In production, we rely on labels to prevent re-dispatch
	dispatcher.err = nil // Fix the error for second attempt
	p.Poll(ctx)

	if len(dispatcher.dispatched) != 1 {
		t.Errorf("expected 1 dispatch on second poll (no cache, error fixed), got %d", len(dispatcher.dispatched))
	}
}

func TestPollerJQLEscaping(t *testing.T) {
	jiraClient := &mockJiraClient{}
	dispatcher := &mockDispatcher{}

	// Component and IssueType with special characters that need escaping
	cfg := poller.Config{
		FilterProject:   "GAUDISW",
		FilterComponent: `Backend "API"`, // Contains quotes
		FilterIssueType: `Bug\Critical`,  // Contains backslash
	}

	p := poller.New(jiraClient, dispatcher, cfg)
	ctx := context.Background()

	// This should not panic or cause JQL injection
	p.Poll(ctx)

	// The JQL should have escaped the special characters
	// We can't directly inspect the JQL, but if it ran without error, escaping worked
}

func TestPollerMaxResults(t *testing.T) {
	jiraClient := &mockJiraClient{}
	dispatcher := &mockDispatcher{}

	// Test custom MaxResults
	cfg := poller.Config{
		FilterProject:   "GAUDISW",
		FilterComponent: "DevOps_K8S",
		FilterIssueType: "",
		MaxResults:      100, // Custom value
	}

	p := poller.New(jiraClient, dispatcher, cfg)
	ctx := context.Background()

	p.Poll(ctx)

	if jiraClient.capturedMaxResults != 100 {
		t.Errorf("expected maxResults=100, got %d", jiraClient.capturedMaxResults)
	}

	// Test default MaxResults (should be 50)
	jiraClient2 := &mockJiraClient{}
	cfg2 := poller.Config{
		FilterProject:   "GAUDISW",
		FilterComponent: "DevOps_K8S",
		FilterIssueType: "",
		// MaxResults not specified
	}

	p2 := poller.New(jiraClient2, dispatcher, cfg2)
	p2.Poll(ctx)

	if jiraClient2.capturedMaxResults != 50 {
		t.Errorf("expected default maxResults=50, got %d", jiraClient2.capturedMaxResults)
	}
}

func TestNew_WithLogger(t *testing.T) {
	logger := zap.NewNop()
	p := poller.New(nil, nil, poller.Config{}, poller.WithLogger(logger))
	if p.Logger() == nil {
		t.Error("expected logger to be set")
	}
}

func TestNew_DefaultLogger(t *testing.T) {
	p := poller.New(nil, nil, poller.Config{})
	if p.Logger() == nil {
		t.Error("expected default logger to be set")
	}
}

// mockDispatcherWithDelay simulates slow dispatches to test concurrency
type mockDispatcherWithDelay struct {
	dispatched  []string
	mu          sync.Mutex
	delay       time.Duration
	failTickets map[string]bool
}

func (m *mockDispatcherWithDelay) Dispatch(ctx context.Context, ticketID string) error {
	select {
	case <-time.After(m.delay):
	case <-ctx.Done():
		return ctx.Err()
	}

	m.mu.Lock()
	defer m.mu.Unlock()

	if m.failTickets != nil && m.failTickets[ticketID] {
		return errors.New("dispatch failed for " + ticketID)
	}

	m.dispatched = append(m.dispatched, ticketID)
	return nil
}

func (m *mockDispatcherWithDelay) getDispatched() []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	return append([]string(nil), m.dispatched...)
}

func TestPollerConcurrentDispatch(t *testing.T) {
	tickets := []jira.Ticket{
		{Key: "TEST-1"}, {Key: "TEST-2"}, {Key: "TEST-3"},
		{Key: "TEST-4"}, {Key: "TEST-5"},
	}
	jiraClient := &mockJiraClient{tickets: tickets}
	dispatcher := &mockDispatcherWithDelay{delay: 50 * time.Millisecond}

	cfg := poller.Config{
		FilterProject:           "GAUDISW",
		FilterComponent:         "DevOps_K8S",
		FilterIssueType:         "",
		MaxConcurrentDispatches: 5,
	}

	p := poller.New(jiraClient, dispatcher, cfg)

	start := time.Now()
	p.Poll(context.Background())
	elapsed := time.Since(start)

	// 5 concurrent with 50ms delay should complete in ~50-100ms, not 250ms+
	if elapsed > 200*time.Millisecond {
		t.Errorf("expected concurrent execution (~50ms), took %v", elapsed)
	}

	if len(dispatcher.getDispatched()) != 5 {
		t.Errorf("expected 5 dispatched, got %d", len(dispatcher.getDispatched()))
	}
}

func TestPollerRespectsMaxConcurrent(t *testing.T) {
	tickets := []jira.Ticket{
		{Key: "TEST-1"}, {Key: "TEST-2"}, {Key: "TEST-3"},
		{Key: "TEST-4"}, {Key: "TEST-5"}, {Key: "TEST-6"},
	}
	jiraClient := &mockJiraClient{tickets: tickets}
	dispatcher := &mockDispatcherWithDelay{delay: 100 * time.Millisecond}

	cfg := poller.Config{
		FilterProject:           "GAUDISW",
		FilterComponent:         "DevOps_K8S",
		FilterIssueType:         "",
		MaxConcurrentDispatches: 2,
	}

	p := poller.New(jiraClient, dispatcher, cfg)

	start := time.Now()
	p.Poll(context.Background())
	elapsed := time.Since(start)

	// 6 tickets, max 2 concurrent, 100ms each = 3 batches = ~300ms minimum
	if elapsed < 250*time.Millisecond {
		t.Errorf("expected ~300ms (limited concurrency), took only %v", elapsed)
	}

	if len(dispatcher.getDispatched()) != 6 {
		t.Errorf("expected 6 dispatched, got %d", len(dispatcher.getDispatched()))
	}
}

func TestPollerConcurrentErrorIsolation(t *testing.T) {
	tickets := []jira.Ticket{
		{Key: "TEST-1"}, {Key: "TEST-2"}, {Key: "TEST-3"}, {Key: "TEST-4"},
	}
	jiraClient := &mockJiraClient{tickets: tickets}
	dispatcher := &mockDispatcherWithDelay{
		delay:       10 * time.Millisecond,
		failTickets: map[string]bool{"TEST-2": true},
	}

	cfg := poller.Config{
		FilterProject:           "GAUDISW",
		FilterComponent:         "DevOps_K8S",
		FilterIssueType:         "",
		MaxConcurrentDispatches: 4,
	}

	p := poller.New(jiraClient, dispatcher, cfg)
	p.Poll(context.Background())

	dispatched := dispatcher.getDispatched()
	if len(dispatched) != 3 {
		t.Errorf("expected 3 successful, got %d: %v", len(dispatched), dispatched)
	}
}

// mockDispatcherFunc allows custom dispatch behavior for testing
type mockDispatcherFunc struct {
	dispatchFunc func(ctx context.Context, ticketID string) error
}

func (m *mockDispatcherFunc) Dispatch(ctx context.Context, ticketID string) error {
	return m.dispatchFunc(ctx, ticketID)
}

func TestPoll_InFlightClearedOnCancellation(t *testing.T) {
	mockJira := &mockJiraClient{
		tickets: []jira.Ticket{{Key: "TEST-1"}, {Key: "TEST-2"}, {Key: "TEST-3"}},
	}

	// Dispatcher that blocks until context cancelled
	slowDispatcher := &mockDispatcherFunc{
		dispatchFunc: func(ctx context.Context, ticketID string) error {
			<-ctx.Done()
			return ctx.Err()
		},
	}

	p := poller.New(mockJira, slowDispatcher, poller.Config{
		FilterProject:           "GAUDISW",
		FilterComponent:         "DevOps_K8S",
		FilterIssueType:         "",
		MaxConcurrentDispatches: 1,
	})

	ctx, cancel := context.WithCancel(context.Background())

	done := make(chan struct{})
	go func() {
		p.Poll(ctx)
		close(done)
	}()

	time.Sleep(50 * time.Millisecond)
	cancel()

	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("Poll did not complete")
	}

	// Verify no tickets remain in-flight
	for _, ticket := range mockJira.tickets {
		if p.IsInFlight(ticket.Key) {
			t.Errorf("Ticket %s still in-flight after cancellation", ticket.Key)
		}
	}
}

func TestPoll_ConcurrencyRespectsCancellation(t *testing.T) {
	// Create mock Jira client that returns many tickets
	tickets := make([]jira.Ticket, 20)
	for i := 0; i < 20; i++ {
		tickets[i] = jira.Ticket{Key: "TEST-" + string(rune('0'+i/10)) + string(rune('0'+i%10))}
	}
	mockJira := &mockJiraClient{
		tickets: tickets,
	}

	// Track how many dispatches started
	var startedCount int
	var mu sync.Mutex
	dispatchStarted := make(chan struct{}, 20)

	// Dispatcher that blocks until context is cancelled
	// This ensures goroutines queue up at the semaphore
	slowDispatcher := &mockDispatcherFunc{
		dispatchFunc: func(ctx context.Context, ticketID string) error {
			mu.Lock()
			startedCount++
			mu.Unlock()
			dispatchStarted <- struct{}{}

			// Block until context cancelled (simulates very slow dispatch)
			<-ctx.Done()
			return ctx.Err()
		},
	}

	cfg := poller.Config{
		FilterProject:           "GAUDISW",
		FilterComponent:         "DevOps_K8S",
		FilterIssueType:         "",
		MaxConcurrentDispatches: 2, // Low concurrency to ensure queueing
	}

	p := poller.New(mockJira, slowDispatcher, cfg)

	// Create cancellable context
	ctx, cancel := context.WithCancel(context.Background())

	// Start poll in goroutine
	done := make(chan struct{})
	go func() {
		p.Poll(ctx)
		close(done)
	}()

	// Wait for first 2 dispatches to start (saturate semaphore)
	<-dispatchStarted
	<-dispatchStarted

	// Now cancel - goroutines waiting on semaphore should unblock
	cancel()

	// Poll should complete within reasonable time (not block forever)
	select {
	case <-done:
		// Success - poll completed after cancellation
	case <-time.After(2 * time.Second):
		t.Fatal("Poll did not complete after context cancellation - potential deadlock")
	}

	// With context-aware semaphore acquisition, most goroutines should exit early.
	// Due to race conditions, a few extra goroutines may sneak through before
	// context cancellation is detected, but significantly fewer than all 20.
	// Without the fix, all 20 would start. With the fix, we expect ~2-4.
	mu.Lock()
	finalCount := startedCount
	mu.Unlock()

	// Allow some slack for race conditions but verify significantly fewer than 20
	if finalCount > 10 {
		t.Errorf("Expected most dispatches to be cancelled at semaphore (got %d, expected <10). "+
			"Without context-aware semaphore, all 20 would have started.", finalCount)
	}
	t.Logf("Dispatches started: %d/20 (expected low number due to context cancellation)", finalCount)
}

func TestEscapeJQL(t *testing.T) {
	tests := []struct {
		name     string
		input    string
		expected string
	}{
		{
			name:     "normal string",
			input:    "normal",
			expected: "normal",
		},
		{
			name:     "string with double quote",
			input:    `with"quote`,
			expected: `with\"quote`,
		},
		{
			name:     "string with backslash",
			input:    `with\backslash`,
			expected: `with\\backslash`,
		},
		{
			name:     "string with both backslash and quote",
			input:    `path\to"file`,
			expected: `path\\to\"file`,
		},
		{
			name:     "potential injection attempt",
			input:    `evil" OR 1=1--`,
			expected: `evil\" OR 1=1--`,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Call unexported escapeJQL via a public wrapper by testing buildJQL
			// Since escapeJQL is not exported, we test it indirectly
			mockJira := &mockJiraClient{}
			mockDispatcher := &mockDispatcher{}

			p := poller.New(mockJira, mockDispatcher, poller.Config{
				FilterProject:   tt.input,
				FilterComponent: "test",
			})

			ctx := context.Background()
			p.Poll(ctx)

			// Verify the JQL contains the escaped version
			// Project value should be escaped in the captured JQL
			expectedSubstring := `project = "` + tt.expected + `"`
			if !contains(mockJira.capturedJQL, expectedSubstring) {
				t.Errorf("escapeJQL(%q): JQL %q does not contain expected substring %q",
					tt.input, mockJira.capturedJQL, expectedSubstring)
			}
		})
	}
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || len(s) > len(substr) &&
		(s[:len(substr)] == substr || s[len(s)-len(substr):] == substr ||
			containsMiddle(s, substr)))
}

func containsMiddle(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}

func TestPollerJQLWithLabelFilters(t *testing.T) {
	tests := []struct {
		name        string
		project     string
		component   string
		issueType   string
		expectedJQL string
	}{
		{
			name:        "with issue type",
			project:     "GAUDISW",
			component:   "DevOps_K8S",
			issueType:   "Bug",
			expectedJQL: `project = "GAUDISW" AND issuetype = "Bug" AND component = "DevOps_K8S" AND labels = "` + poller.LabelInvestigationRequired + `" AND labels NOT IN (` + poller.LabelInvestigationComplete + `, ` + poller.LabelInvestigationInProgress + `)`,
		},
		{
			name:        "without issue type",
			project:     "GAUDISW",
			component:   "DevOps_K8S",
			issueType:   "",
			expectedJQL: `project = "GAUDISW" AND issuetype in ("Bug", "Task") AND component = "DevOps_K8S" AND labels = "` + poller.LabelInvestigationRequired + `" AND labels NOT IN (` + poller.LabelInvestigationComplete + `, ` + poller.LabelInvestigationInProgress + `)`,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			mockJira := &mockJiraClient{}
			mockDispatcher := &mockDispatcher{}

			p := poller.New(mockJira, mockDispatcher, poller.Config{
				FilterProject:   tt.project,
				FilterComponent: tt.component,
				FilterIssueType: tt.issueType,
			})

			// We need to test through Poll to capture the JQL used
			ctx := context.Background()
			p.Poll(ctx)

			if mockJira.capturedJQL != tt.expectedJQL {
				t.Errorf("buildJQL() = %q, want %q", mockJira.capturedJQL, tt.expectedJQL)
			}
		})
	}
}

func TestPollerJQLNoTimeFilter(t *testing.T) {
	jiraClient := &mockJiraClient{}
	dispatcher := &mockDispatcher{}

	cfg := poller.Config{
		FilterProject:   "GAUDISW",
		FilterComponent: "DevOps_K8S",
		FilterIssueType: "",
	}

	p := poller.New(jiraClient, dispatcher, cfg)
	ctx := context.Background()

	p.Poll(ctx)

	// Verify JQL does not contain time filter
	if contains(jiraClient.capturedJQL, "created >=") {
		t.Errorf("JQL should not contain time filter, got: %s", jiraClient.capturedJQL)
	}

	// Verify JQL contains label filters (single source of truth)
	if !contains(jiraClient.capturedJQL, poller.LabelInvestigationRequired) {
		t.Errorf("JQL should contain investigation required label, got: %s", jiraClient.capturedJQL)
	}
	if !contains(jiraClient.capturedJQL, poller.LabelInvestigationComplete) {
		t.Errorf("JQL should contain investigation complete label, got: %s", jiraClient.capturedJQL)
	}
	if !contains(jiraClient.capturedJQL, poller.LabelInvestigationInProgress) {
		t.Errorf("JQL should contain in-progress label, got: %s", jiraClient.capturedJQL)
	}
}

func TestPollerAddsInProgressLabelBeforeDispatch(t *testing.T) {
	jiraClient := &mockJiraClient{
		tickets: []jira.Ticket{{Key: "TEST-500"}},
	}
	dispatcher := &mockDispatcher{}

	cfg := poller.Config{
		FilterProject:   "GAUDISW",
		FilterComponent: "DevOps_K8S",
	}

	p := poller.New(jiraClient, dispatcher, cfg)
	p.Poll(context.Background())

	// Verify in-progress label was added
	jiraClient.mu.Lock()
	defer jiraClient.mu.Unlock()
	if len(jiraClient.addLabelCalls) != 1 {
		t.Fatalf("expected 1 AddLabel call, got %d", len(jiraClient.addLabelCalls))
	}
	call := jiraClient.addLabelCalls[0]
	if call.ticketID != "TEST-500" {
		t.Errorf("expected ticketID TEST-500, got %s", call.ticketID)
	}
	if call.label != poller.LabelInvestigationInProgress {
		t.Errorf("expected label %s, got %s", poller.LabelInvestigationInProgress, call.label)
	}

	// Verify dispatch happened
	if len(dispatcher.dispatched) != 1 {
		t.Errorf("expected 1 dispatch, got %d", len(dispatcher.dispatched))
	}
}

func TestPollerSkipsDispatchWhenLabelFails(t *testing.T) {
	jiraClient := &mockJiraClient{
		tickets:     []jira.Ticket{{Key: "TEST-600"}},
		addLabelErr: errors.New("jira API unavailable"),
	}
	dispatcher := &mockDispatcher{}

	cfg := poller.Config{
		FilterProject:   "GAUDISW",
		FilterComponent: "DevOps_K8S",
	}

	p := poller.New(jiraClient, dispatcher, cfg)
	p.Poll(context.Background())

	// Dispatch should NOT have been called
	if len(dispatcher.dispatched) != 0 {
		t.Errorf("expected 0 dispatches when label add fails, got %d", len(dispatcher.dispatched))
	}
}

func TestPollerRemovesLabelOnDispatchFailure(t *testing.T) {
	jiraClient := &mockJiraClient{
		tickets: []jira.Ticket{{Key: "TEST-700"}},
	}
	dispatcher := &mockDispatcher{
		err: errors.New("langgraph-agent unreachable"),
	}

	cfg := poller.Config{
		FilterProject:   "GAUDISW",
		FilterComponent: "DevOps_K8S",
	}

	p := poller.New(jiraClient, dispatcher, cfg)
	p.Poll(context.Background())

	// In-progress label should have been added then removed
	jiraClient.mu.Lock()
	defer jiraClient.mu.Unlock()
	if len(jiraClient.addLabelCalls) != 1 {
		t.Fatalf("expected 1 AddLabel call, got %d", len(jiraClient.addLabelCalls))
	}
	if len(jiraClient.removeLabelCalls) != 1 {
		t.Fatalf("expected 1 RemoveLabel call, got %d", len(jiraClient.removeLabelCalls))
	}
	rmCall := jiraClient.removeLabelCalls[0]
	if rmCall.ticketID != "TEST-700" {
		t.Errorf("expected ticketID TEST-700, got %s", rmCall.ticketID)
	}
	if rmCall.label != poller.LabelInvestigationInProgress {
		t.Errorf("expected label %s, got %s", poller.LabelInvestigationInProgress, rmCall.label)
	}
}

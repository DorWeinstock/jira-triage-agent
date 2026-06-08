package poller

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strings"
	"sync"
	"time"

	"go.uber.org/zap"
	"jira-triage-agent/pkg/mcp/jira"
)

// ApprovalAction represents the type of action parsed from a HITL comment.
type ApprovalAction int

const (
	// ActionNone indicates no recognized action in the comment.
	ActionNone ApprovalAction = iota
	// ActionApprove indicates user approval to proceed with remediation.
	ActionApprove
	// ActionReject indicates user rejection with a reason.
	ActionReject
)

// ApprovalResult represents parsed approval/rejection from a Jira comment.
type ApprovalResult struct {
	Action ApprovalAction
	Reason string // Only populated for ActionReject
}

// Regular expressions for parsing HITL commands from Jira comments.
// Patterns are case-insensitive and handle surrounding whitespace.
// Also handles Jira markup like {{approve}} (inline code).
// Braces must be paired - {{approve}} works, but {{approve or approve}} does not.
var (
	// Matches: "approve" or "{{approve}}" (case-insensitive, with optional whitespace)
	approveRegex = regexp.MustCompile(`(?i)^\s*(?:approve|\{\{approve\}\})\s*$`)
	// Matches: "reject" (bare), "reject: reason", or "{{reject: reason}}" (case-insensitive)
	// Bare "reject" is accepted with an empty reason.
	// Uses two capture groups - one for plain text reason, one for braced version reason
	rejectRegex = regexp.MustCompile(`(?i)^\s*(?:reject(?::\s*(.+?))?|\{\{reject(?::\s*(.+?))?\}\})\s*$`)
)

// jiraTimestampFormats are the possible timestamp formats from Jira REST API.
// Jira may return timestamps with varying millisecond precision.
var jiraTimestampFormats = []string{
	"2006-01-02T15:04:05.000-0700", // Standard: 3 decimal places (most common)
	"2006-01-02T15:04:05-0700",     // No milliseconds
	"2006-01-02T15:04:05.0-0700",   // 1 decimal place
	"2006-01-02T15:04:05.00-0700",  // 2 decimal places
}

// PendingApproval represents a ticket awaiting HITL approval.
type PendingApproval struct {
	TicketID    string
	ThreadID    string
	RequestedAt time.Time
}

// HITLCheckpoint is the checkpoint name used for HITL approval.
const HITLCheckpoint = "attempt_remediation"

// defaultHTTPTimeout is the HTTP client timeout for calls to the LangGraph agent.
// Set to 10 minutes because /approve triggers workflow resumption which can take time:
// - Remediation execution (kubectl commands)
// - Verification grace period (30s for K8s controller reconciliation)
// - Stability polling (multiple checks with delays)
//
// NOTE: This is different from hitlTimeout (in HITLWatcher struct), which is the
// maximum time to wait for a human to approve/reject before auto-rejecting.
// - defaultHTTPTimeout: How long a single HTTP request can take (workflow execution)
// - hitlTimeout: How long to wait for human response (typically hours)
const defaultHTTPTimeout = 10 * time.Minute

// HITLWatcher monitors Jira comments for HITL approval/rejection.
type HITLWatcher struct {
	jiraClient   *jira.Client
	langgraphURL string
	botEmail     string
	pollInterval time.Duration
	hitlTimeout  time.Duration // Time to wait for human approval before auto-reject
	httpClient   *http.Client

	pending map[string]PendingApproval // Store by value, not pointer
	mu      sync.RWMutex
	logger  *zap.Logger
}

// NewHITLWatcher creates a new HITL watcher with configured HTTP client.
func NewHITLWatcher(
	jiraClient *jira.Client,
	langgraphURL string,
	botEmail string,
	pollInterval time.Duration,
	timeout time.Duration,
	logger *zap.Logger,
) *HITLWatcher {
	return &HITLWatcher{
		jiraClient:   jiraClient,
		langgraphURL: langgraphURL,
		botEmail:     botEmail,
		pollInterval: pollInterval,
		hitlTimeout:  timeout,
		httpClient:   &http.Client{Timeout: defaultHTTPTimeout},
		pending:      make(map[string]PendingApproval),
		logger:       logger,
	}
}

// AddPending registers a ticket for HITL monitoring.
func (w *HITLWatcher) AddPending(ticketID, threadID string, requestedAt time.Time) {
	w.mu.Lock()
	defer w.mu.Unlock()

	w.pending[ticketID] = PendingApproval{
		TicketID:    ticketID,
		ThreadID:    threadID,
		RequestedAt: requestedAt,
	}
}

// RemovePending removes a ticket from HITL monitoring.
func (w *HITLWatcher) RemovePending(ticketID string) {
	w.mu.Lock()
	defer w.mu.Unlock()

	delete(w.pending, ticketID)
}

// GetPending returns a COPY of a pending approval by ticket ID.
// Returning a copy ensures thread-safety.
func (w *HITLWatcher) GetPending(ticketID string) (PendingApproval, bool) {
	w.mu.RLock()
	defer w.mu.RUnlock()

	pending, exists := w.pending[ticketID]
	return pending, exists // Returns copy due to value semantics
}

// IsTimedOut checks if a pending approval has exceeded the timeout.
func (w *HITLWatcher) IsTimedOut(pending PendingApproval) bool {
	return time.Since(pending.RequestedAt) > w.hitlTimeout
}

// ParseComment parses a Jira comment for approval/rejection commands.
func (w *HITLWatcher) ParseComment(body string) ApprovalResult {
	body = strings.TrimSpace(body)

	if approveRegex.MatchString(body) {
		return ApprovalResult{Action: ActionApprove}
	}

	if matches := rejectRegex.FindStringSubmatch(body); len(matches) > 0 {
		// Regex has two capture groups: matches[1] for plain text, matches[2] for braced version
		// Both may be empty for bare "reject" (no reason provided)
		reason := matches[1]
		if reason == "" && len(matches) > 2 {
			reason = matches[2]
		}
		return ApprovalResult{
			Action: ActionReject,
			Reason: strings.TrimSpace(reason),
		}
	}

	return ApprovalResult{Action: ActionNone}
}

// ApproveRequest matches Python's ApproveRequest model.
type ApproveRequest struct {
	Checkpoint string `json:"checkpoint"`
}

// RejectRequest matches Python's RejectRequest model.
type RejectRequest struct {
	Reason string `json:"reason"`
}

// GetAllPending returns copies of all pending approvals.
func (w *HITLWatcher) GetAllPending() []PendingApproval {
	w.mu.RLock()
	defer w.mu.RUnlock()

	result := make([]PendingApproval, 0, len(w.pending))
	for _, p := range w.pending {
		result = append(result, p) // Copy
	}
	return result
}

// GetTimedOutTickets returns copies of all pending approvals that have timed out.
func (w *HITLWatcher) GetTimedOutTickets() []PendingApproval {
	w.mu.RLock()
	defer w.mu.RUnlock()

	var timedOut []PendingApproval
	for _, p := range w.pending {
		if w.IsTimedOut(p) {
			timedOut = append(timedOut, p) // Copy
		}
	}
	return timedOut
}

// ShouldSkipComment returns true if the comment is from the bot.
func (w *HITLWatcher) ShouldSkipComment(authorEmail string) bool {
	return authorEmail == w.botEmail
}

// parseJiraTimestamp tries multiple timestamp formats that Jira may return.
func parseJiraTimestamp(ts string) (time.Time, error) {
	for _, fmt := range jiraTimestampFormats {
		if t, err := time.Parse(fmt, ts); err == nil {
			return t, nil
		}
	}
	return time.Time{}, fmt.Errorf("unable to parse Jira timestamp: %s", ts)
}

// IsCommentAfterRequest reports whether a comment was created at or after
// the HITL request time. Returns false if the timestamp cannot be parsed.
func (w *HITLWatcher) IsCommentAfterRequest(commentCreated string, requestedAt time.Time) bool {
	created, err := parseJiraTimestamp(commentCreated)
	if err != nil {
		w.logger.Warn("skipped comment with unparseable timestamp",
			zap.String("created", commentCreated),
			zap.Error(err))
		return false
	}

	// Comment must be AT or AFTER request time (no backward tolerance)
	return !created.Before(requestedAt)
}

// HandleTimeout rejects a timed-out pending approval and removes it from monitoring.
func (w *HITLWatcher) HandleTimeout(ctx context.Context, pending PendingApproval) error {
	reason := fmt.Sprintf("HITL timeout (%v) - no human response received", w.hitlTimeout)

	err := w.ResumeWorkflow(ctx, pending.TicketID, ApprovalResult{
		Action: ActionReject,
		Reason: reason,
	})
	if err != nil {
		return fmt.Errorf("handle timeout for %s: %w", pending.TicketID, err)
	}

	w.RemovePending(pending.TicketID)
	return nil
}

// ResumeWorkflow sends an approval or rejection to the Python LangGraph agent
// to resume a paused workflow. It constructs the appropriate endpoint and payload
// based on the approval action.
//
// Returns an error if the action is invalid, JSON marshaling fails,
// the HTTP request fails, or the server returns a non-2xx status code.
func (w *HITLWatcher) ResumeWorkflow(ctx context.Context, ticketID string, result ApprovalResult) error {
	var endpoint string
	var body []byte
	var err error

	switch result.Action {
	case ActionApprove:
		endpoint = fmt.Sprintf("%s/investigate/%s/approve", w.langgraphURL, ticketID)
		body, err = json.Marshal(ApproveRequest{Checkpoint: HITLCheckpoint})
	case ActionReject:
		endpoint = fmt.Sprintf("%s/investigate/%s/reject", w.langgraphURL, ticketID)
		body, err = json.Marshal(RejectRequest{Reason: result.Reason})
	default:
		return fmt.Errorf("invalid action: %d", result.Action)
	}

	if err != nil {
		return fmt.Errorf("marshal request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := w.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("POST %s: %w", endpoint, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		respBody, err := io.ReadAll(resp.Body)
		if err != nil {
			return fmt.Errorf("resume failed (status %d): could not read response: %w", resp.StatusCode, err)
		}
		return fmt.Errorf("resume failed (status %d): %s", resp.StatusCode, string(respBody))
	}

	return nil
}

// Start begins the polling loop to check Jira comments for pending approvals.
// It runs until the context is cancelled.
func (w *HITLWatcher) Start(ctx context.Context) {
	ticker := time.NewTicker(w.pollInterval)
	defer ticker.Stop()

	w.logger.Info("hitl watcher started",
		zap.Duration("pollInterval", w.pollInterval),
		zap.Duration("hitlTimeout", w.hitlTimeout))

	for {
		select {
		case <-ctx.Done():
			w.logger.Info("hitl watcher stopped")
			return
		case <-ticker.C:
			w.pollAllPending(ctx)
		}
	}
}

// pollAllPending checks all pending tickets for approval comments.
func (w *HITLWatcher) pollAllPending(ctx context.Context) {
	pending := w.GetAllPending()
	if len(pending) == 0 {
		return
	}

	w.logger.Info("polling pending approvals", zap.Int("count", len(pending)))

	for _, p := range pending {
		if w.IsTimedOut(p) {
			w.logger.Warn("hitl approval timed out",
				zap.String("ticketID", p.TicketID),
				zap.Duration("hitlTimeout", w.hitlTimeout))
			if err := w.HandleTimeout(ctx, p); err != nil {
				w.logger.Error("failed to handle timeout",
					zap.String("ticketID", p.TicketID),
					zap.Error(err))
			}
			continue
		}

		if err := w.checkTicketComments(ctx, p); err != nil {
			w.logger.Error("failed to check comments",
				zap.String("ticketID", p.TicketID),
				zap.Error(err))
		}
	}
}

// checkTicketComments fetches ticket comments and processes any approval/rejection.
func (w *HITLWatcher) checkTicketComments(ctx context.Context, pending PendingApproval) error {
	ticket, err := w.jiraClient.GetTicket(ctx, pending.TicketID)
	if err != nil {
		return fmt.Errorf("get ticket %s: %w", pending.TicketID, err)
	}

	if ticket.Fields.Comment == nil || len(ticket.Fields.Comment.Comments) == 0 {
		return nil
	}

	// Process comments from newest to oldest to find approval/rejection
	comments := ticket.Fields.Comment.Comments
	for i := len(comments) - 1; i >= 0; i-- {
		comment := comments[i]

		// Skip bot's own comments
		if w.ShouldSkipComment(comment.Author.EmailAddress) {
			continue
		}

		// Skip comments from before this HITL request
		if !w.IsCommentAfterRequest(comment.Created, pending.RequestedAt) {
			w.logger.Debug("skipping old comment",
				zap.String("ticketID", pending.TicketID),
				zap.String("commentID", comment.ID),
				zap.String("commentCreated", comment.Created))
			continue
		}

		result := w.ParseComment(comment.Body)
		if result.Action == ActionNone {
			continue
		}

		// Log with action type in message
		actionType := "approval"
		if result.Action == ActionReject {
			actionType = "rejection"
		}
		w.logger.Info("hitl "+actionType+" received",
			zap.String("ticketID", pending.TicketID),
			zap.String("action", actionName(result.Action)),
			zap.String("reviewer", comment.Author.DisplayName))

		// Remove from pending BEFORE resuming workflow to avoid a race condition:
		// ResumeWorkflow blocks until the workflow completes (remediation + verification),
		// which can take minutes. During that time, the workflow may loop back and
		// re-register the same ticket via AddPending. If we removed AFTER ResumeWorkflow,
		// we'd delete the new registration.
		w.RemovePending(pending.TicketID)

		if err := w.ResumeWorkflow(ctx, pending.TicketID, result); err != nil {
			// Re-register to avoid orphaning the ticket if resume fails
			w.AddPending(pending.TicketID, pending.ThreadID, pending.RequestedAt)
			return fmt.Errorf("resume workflow (ticket re-added to pending): %w", err)
		}
		return nil
	}

	w.logger.Info("no matching HITL comment found",
		zap.String("ticketID", pending.TicketID),
		zap.Int("commentsChecked", len(comments)),
		zap.Time("requestedAt", pending.RequestedAt))
	return nil
}

// actionName returns a human-readable name for an approval action.
func actionName(action ApprovalAction) string {
	switch action {
	case ActionApprove:
		return "APPROVE"
	case ActionReject:
		return "REJECT"
	default:
		return "UNKNOWN"
	}
}

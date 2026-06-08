// Package poller provides a periodic Jira ticket polling service that
// automatically discovers and dispatches tickets for AI agent investigation.
// It implements deduplication, caching, and automatic cleanup of processed tickets.
package poller

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"time"

	"go.uber.org/zap"

	"jira-triage-agent/pkg/mcp/jira"
)

// Label constants for the triage workflow.
// LabelInvestigationRequired is the label the QA AI agent stamps on every
// ticket it creates — we use it as the trigger to start triage.
// The in-progress label prevents the same ticket from being dispatched twice
// across concurrent poll cycles.
const (
	LabelInvestigationRequired   = "ai-generated"
	LabelInvestigationInProgress = "triage-in-progress"
	LabelInvestigationComplete   = "triage-agent-done"
)

// Default configuration values for the poller
const (
	defaultMaxResults              = 50
	defaultInterval                = 3 * time.Minute
	defaultMaxConcurrentDispatches = 5
)

// JiraClient defines the interface for querying and labeling Jira tickets
type JiraClient interface {
	SearchTickets(ctx context.Context, jql string, maxResults int) ([]jira.Ticket, error)
	AddLabel(ctx context.Context, ticketID, label string) error
	RemoveLabel(ctx context.Context, ticketID, label string) error
}

// Dispatcher defines the interface for dispatching ticket investigations
type Dispatcher interface {
	Dispatch(ctx context.Context, ticketID string) error
}

// Config holds the configuration for the Poller
type Config struct {
	FilterProject           string
	FilterComponent         string
	FilterIssueType         string
	ProcessedLabel          string
	Interval                time.Duration
	MaxResults              int
	MaxConcurrentDispatches int
}

// Poller periodically polls Jira for new tickets and dispatches them for investigation
type Poller struct {
	jira       JiraClient
	dispatcher Dispatcher
	config     Config

	// In-flight tracking to prevent concurrent processing of the same ticket within a poll cycle
	inFlight   map[string]struct{}
	inFlightMu sync.RWMutex

	logger        *zap.Logger
	maxConcurrent int
}

// Option is a functional option for configuring Poller.
type Option func(*Poller)

// WithLogger sets a custom logger for the Poller.
func WithLogger(logger *zap.Logger) Option {
	return func(p *Poller) {
		p.logger = logger
	}
}

// Logger returns the Poller's logger instance.
func (p *Poller) Logger() *zap.Logger {
	return p.logger
}

// New creates a new Poller with the given configuration.
// Default values are applied for any zero-valued configuration fields.
func New(jira JiraClient, dispatcher Dispatcher, cfg Config, opts ...Option) *Poller {
	// Set default interval if not provided
	if cfg.Interval == 0 {
		cfg.Interval = defaultInterval
	}

	// Set default maxResults if not provided
	if cfg.MaxResults == 0 {
		cfg.MaxResults = defaultMaxResults
	}

	// Set default maxConcurrentDispatches if not provided
	if cfg.MaxConcurrentDispatches == 0 {
		cfg.MaxConcurrentDispatches = defaultMaxConcurrentDispatches
	}

	p := &Poller{
		jira:          jira,
		dispatcher:    dispatcher,
		config:        cfg,
		inFlight:      make(map[string]struct{}),
		logger:        zap.NewNop(), // Default to no-op
		maxConcurrent: cfg.MaxConcurrentDispatches,
	}

	for _, opt := range opts {
		opt(p)
	}

	return p
}

// escapeJQL escapes special characters in JQL string values to prevent injection
func escapeJQL(s string) string {
	// Escape double quotes and backslashes
	s = strings.ReplaceAll(s, `\`, `\\`)
	s = strings.ReplaceAll(s, `"`, `\"`)
	return s
}

// buildJQL constructs a JQL query that finds AI-generated tickets needing triage.
// Excludes tickets already processed or currently in-flight.
func (p *Poller) buildJQL() string {
	var parts []string

	parts = append(parts, fmt.Sprintf(`project = "%s"`, escapeJQL(p.config.FilterProject)))
	parts = append(parts, `issuetype in ("Bug", "Task")`)
	parts = append(parts, fmt.Sprintf(`component = "%s"`, escapeJQL(p.config.FilterComponent)))

	// Optional additional issue type filter (overrides the default above when set)
	if p.config.FilterIssueType != "" {
		parts[1] = fmt.Sprintf(`issuetype = "%s"`, escapeJQL(p.config.FilterIssueType))
	}

	// Trigger label: ticket was created by the QA AI agent
	parts = append(parts, `labels = "`+LabelInvestigationRequired+`"`)

	// Exclusion labels: already processed or currently being triaged
	processedLabel := LabelInvestigationComplete
	if p.config.ProcessedLabel != "" {
		processedLabel = escapeJQL(p.config.ProcessedLabel)
	}
	parts = append(parts, `labels NOT IN (`+processedLabel+`, `+LabelInvestigationInProgress+`)`)

	return strings.Join(parts, " AND ")
}

// isInFlight checks if a ticket is currently being processed
func (p *Poller) isInFlight(ticketID string) bool {
	p.inFlightMu.RLock()
	defer p.inFlightMu.RUnlock()
	_, ok := p.inFlight[ticketID]
	return ok
}

// IsInFlight is the exported version for testing purposes
func (p *Poller) IsInFlight(ticketID string) bool {
	return p.isInFlight(ticketID)
}

// markInFlight marks a ticket as currently being processed
func (p *Poller) markInFlight(ticketID string) {
	p.inFlightMu.Lock()
	defer p.inFlightMu.Unlock()
	p.inFlight[ticketID] = struct{}{}
}

// clearInFlight removes a ticket from the in-flight set
func (p *Poller) clearInFlight(ticketID string) {
	p.inFlightMu.Lock()
	defer p.inFlightMu.Unlock()
	delete(p.inFlight, ticketID)
}

// dispatchTicket handles a single ticket dispatch with error handling.
// Adds an in-progress label before dispatch to prevent re-dispatch across poll cycles.
// If label add fails, dispatch is skipped (ticket retried next cycle).
// If dispatch fails, the in-progress label is removed (best-effort) so the ticket is retried.
func (p *Poller) dispatchTicket(ctx context.Context, ticketKey string) {
	defer p.clearInFlight(ticketKey)

	// Add in-progress label BEFORE dispatch to prevent re-dispatch
	if err := p.jira.AddLabel(ctx, ticketKey, LabelInvestigationInProgress); err != nil {
		p.logger.Error("failed to add in-progress label, skipping dispatch",
			zap.String("ticketID", ticketKey), zap.Error(err))
		return
	}

	p.logger.Info("dispatching ticket", zap.String("ticketID", ticketKey))
	if err := p.dispatcher.Dispatch(ctx, ticketKey); err != nil {
		p.logger.Error("dispatch failed, removing in-progress label",
			zap.String("ticketID", ticketKey), zap.Error(err))
		// Best-effort: remove label so ticket is retried next cycle
		if rmErr := p.jira.RemoveLabel(ctx, ticketKey, LabelInvestigationInProgress); rmErr != nil {
			p.logger.Error("failed to remove in-progress label after dispatch failure",
				zap.String("ticketID", ticketKey), zap.Error(rmErr))
		}
		return
	}
}

// Poll performs a single poll cycle: queries Jira and dispatches new tickets concurrently.
// Labels prevent re-dispatch across polls; in-flight tracking prevents races within a poll cycle.
func (p *Poller) Poll(ctx context.Context) {
	jql := p.buildJQL()
	p.logger.Debug("polling jira", zap.String("jql", jql))

	tickets, err := p.jira.SearchTickets(ctx, jql, p.config.MaxResults)
	if err != nil {
		p.logger.Error("jira search failed", zap.Error(err))
		return
	}

	p.logger.Info("found tickets", zap.Int("count", len(tickets)))

	// Filter tickets that can be dispatched (only check in-flight status)
	var toDispatch []jira.Ticket
	for _, ticket := range tickets {
		ticketKey := ticket.Key
		if p.isInFlight(ticketKey) {
			p.logger.Debug("skipping in-flight ticket", zap.String("ticketID", ticketKey))
			continue
		}
		toDispatch = append(toDispatch, ticket)
	}

	if len(toDispatch) == 0 {
		return
	}

	p.logger.Info("dispatching tickets concurrently",
		zap.Int("count", len(toDispatch)),
		zap.Int("maxConcurrent", p.maxConcurrent))

	// Bounded worker pool using semaphore channel
	sem := make(chan struct{}, p.maxConcurrent)
	var wg sync.WaitGroup

	for _, ticket := range toDispatch {
		ticketKey := ticket.Key

		// Mark in-flight before spawning goroutine to prevent race
		p.markInFlight(ticketKey)

		wg.Add(1)
		go func(key string) {
			defer wg.Done()

			// Context-aware semaphore acquisition
			select {
			case sem <- struct{}{}:
				defer func() { <-sem }()
				p.dispatchTicket(ctx, key)
			case <-ctx.Done():
				// Context cancelled while waiting for semaphore
				p.clearInFlight(key)
				p.logger.Debug("skipping dispatch due to context cancellation",
					zap.String("ticketID", key))
				return
			}
		}(ticketKey)
	}

	// Wait for all dispatches to complete
	wg.Wait()
}

// Run starts the poller loop, blocking until the context is cancelled
func (p *Poller) Run(ctx context.Context) {
	ticker := time.NewTicker(p.config.Interval)
	defer ticker.Stop()

	// Initial poll
	p.Poll(ctx)

	for {
		select {
		case <-ctx.Done():
			p.logger.Info("poller stopping")
			return
		case <-ticker.C:
			p.Poll(ctx)
		}
	}
}

package poller

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

// HTTPDispatcher sends investigation requests to the LangGraph agent via HTTP
type HTTPDispatcher struct {
	agentURL   string
	httpClient *http.Client
}

// Compile-time interface compliance check
var _ Dispatcher = (*HTTPDispatcher)(nil)

// NewHTTPDispatcher creates a new HTTP dispatcher with the given agent URL
func NewHTTPDispatcher(agentURL string) *HTTPDispatcher {
	return &HTTPDispatcher{
		agentURL: agentURL,
		httpClient: &http.Client{
			// Investigation workflows on CPU-based LLM can take 10-15 minutes
			// (includes multiple LLM calls, K8s operations, verification polling)
			Timeout: 20 * time.Minute,
		},
	}
}

// InvestigationRequest represents the JSON payload sent to the agent
type InvestigationRequest struct {
	TicketID string `json:"ticket_id"`
}

// Dispatch sends a ticket investigation request to the agent
func (d *HTTPDispatcher) Dispatch(ctx context.Context, ticketID string) error {
	if ticketID == "" {
		return fmt.Errorf("ticket ID cannot be empty")
	}

	reqBody := InvestigationRequest{TicketID: ticketID}
	bodyBytes, err := json.Marshal(reqBody)
	if err != nil {
		return fmt.Errorf("marshaling request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, d.agentURL, bytes.NewReader(bodyBytes))
	if err != nil {
		return fmt.Errorf("creating request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")

	resp, err := d.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("executing request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusAccepted {
		return fmt.Errorf("unexpected status: %d", resp.StatusCode)
	}

	return nil
}

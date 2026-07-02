package jira

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"

	"golang.org/x/time/rate"
)

// Rate limiting configuration for Jira API
// Default: 10 requests/second with burst of 20 (conservative to avoid Jira rate limits)
const (
	defaultRequestsPerSecond = 10
	defaultBurstSize         = 20
)

type Client struct {
	baseURL    string
	pat        string
	httpClient *http.Client
	limiter    *rate.Limiter
}

func NewClient(baseURL, pat string) *Client {
	// Skip TLS verification for internal corporate Jira servers
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{InsecureSkipVerify: true},
	}

	// Respect proxy settings from environment
	transport.Proxy = http.ProxyFromEnvironment

	return &Client{
		baseURL: baseURL,
		pat:     pat,
		httpClient: &http.Client{
			Timeout:   30 * time.Second,
			Transport: transport,
		},
		limiter: rate.NewLimiter(rate.Limit(defaultRequestsPerSecond), defaultBurstSize),
	}
}

// waitForRateLimit blocks until the rate limiter allows the request or context is cancelled.
func (c *Client) waitForRateLimit(ctx context.Context) error {
	if err := c.limiter.Wait(ctx); err != nil {
		return fmt.Errorf("rate limit wait cancelled: %w", err)
	}
	return nil
}

type Ticket struct {
	Key    string       `json:"key"`
	Fields TicketFields `json:"fields"`
}

type TicketFields struct {
	Summary     string           `json:"summary"`
	Description string           `json:"description"`
	Labels      []string         `json:"labels"`
	Components  []Component      `json:"components"`
	IssueType   IssueType        `json:"issuetype"`
	Project     Project          `json:"project"`
	Resolution  *Resolution      `json:"resolution,omitempty"`
	Comment     *CommentsWrapper `json:"comment,omitempty"`
	Reporter    *User            `json:"reporter,omitempty"`
	Assignee    *User            `json:"assignee,omitempty"`
	Updated     string           `json:"updated"`
	Created     string           `json:"created"`
}

// User represents a Jira user (reporter or assignee).
// On self-hosted Jira Server, the Name field is the username used for assignment.
type User struct {
	Name        string `json:"name"`        // Jira Server username (used for assignment)
	DisplayName string `json:"displayName"`
}

// Resolution represents the resolution status of a ticket
type Resolution struct {
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
}

// CommentsWrapper holds the paginated comments response from Jira
type CommentsWrapper struct {
	Comments   []Comment `json:"comments"`
	MaxResults int       `json:"maxResults"`
	Total      int       `json:"total"`
	StartAt    int       `json:"startAt"`
}

// Comment represents a single Jira comment
type Comment struct {
	ID      string        `json:"id"`
	Body    string        `json:"body"`
	Author  CommentAuthor `json:"author"`
	Created string        `json:"created"`
	Updated string        `json:"updated"`
}

// CommentAuthor represents the author of a comment
type CommentAuthor struct {
	DisplayName  string `json:"displayName"`
	EmailAddress string `json:"emailAddress,omitempty"`
}

type Component struct {
	Name string `json:"name"`
}

type IssueType struct {
	Name string `json:"name"`
}

type Project struct {
	Key string `json:"key"`
}

func (c *Client) handleErrorResponse(resp *http.Response, expectedStatus ...int) error {
	for _, status := range expectedStatus {
		if resp.StatusCode == status {
			return nil
		}
	}

	var errBody map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&errBody); err == nil {
		return fmt.Errorf("jira API error (status %d): %v", resp.StatusCode, errBody)
	}
	return fmt.Errorf("jira API error: unexpected status %d", resp.StatusCode)
}

func (c *Client) GetTicket(ctx context.Context, ticketID string) (*Ticket, error) {
	// Wait for rate limiter
	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	// Request expand=comment to include comments in response
	// This is needed for HistoryAgent to fetch resolution comments from similar tickets
	ticketURL := fmt.Sprintf("%s/rest/api/2/issue/%s?expand=renderedFields,comment", c.baseURL, ticketID)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, ticketURL, nil)
	if err != nil {
		return nil, fmt.Errorf("creating request: %w", err)
	}

	req.Header.Set("Authorization", "Bearer "+c.pat)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("executing request: %w", err)
	}
	defer resp.Body.Close()

	if err := c.handleErrorResponse(resp, http.StatusOK); err != nil {
		return nil, err
	}

	var ticket Ticket
	if err := json.NewDecoder(resp.Body).Decode(&ticket); err != nil {
		return nil, fmt.Errorf("decoding response: %w", err)
	}

	return &ticket, nil
}

type SearchResult struct {
	Issues []Ticket `json:"issues"`
}

func (c *Client) SearchTickets(ctx context.Context, jql string, maxResults int) ([]Ticket, error) {
	// Wait for rate limiter
	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	apiURL := fmt.Sprintf("%s/rest/api/2/search?jql=%s&maxResults=%d", c.baseURL, url.QueryEscape(jql), maxResults)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, apiURL, nil)
	if err != nil {
		return nil, fmt.Errorf("creating request: %w", err)
	}

	req.Header.Set("Authorization", "Bearer "+c.pat)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("executing request: %w", err)
	}
	defer resp.Body.Close()

	if err := c.handleErrorResponse(resp, http.StatusOK); err != nil {
		return nil, err
	}

	var result SearchResult
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decoding response: %w", err)
	}

	return result.Issues, nil
}

// AddComment adds a comment to a Jira ticket and returns the created comment.
// The returned Comment contains the server-assigned ID and Created timestamp.
func (c *Client) AddComment(ctx context.Context, ticketID, comment string) (*Comment, error) {
	// Wait for rate limiter
	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	apiURL := fmt.Sprintf("%s/rest/api/2/issue/%s/comment", c.baseURL, ticketID)

	body := map[string]string{"body": comment}
	bodyBytes, err := json.Marshal(body)
	if err != nil {
		return nil, fmt.Errorf("marshaling request body: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, apiURL, bytes.NewReader(bodyBytes))
	if err != nil {
		return nil, fmt.Errorf("creating request: %w", err)
	}

	req.Header.Set("Authorization", "Bearer "+c.pat)
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("executing request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusCreated {
		var errBody map[string]interface{}
		if err := json.NewDecoder(resp.Body).Decode(&errBody); err == nil {
			return nil, fmt.Errorf("jira API error (status %d): %v", resp.StatusCode, errBody)
		}
		return nil, fmt.Errorf("jira API error: unexpected status %d", resp.StatusCode)
	}

	// Parse the created comment from response
	var createdComment Comment
	if err := json.NewDecoder(resp.Body).Decode(&createdComment); err != nil {
		return nil, fmt.Errorf("parsing comment response: %w", err)
	}

	return &createdComment, nil
}

func (c *Client) AddLabel(ctx context.Context, ticketID, label string) error {
	// Wait for rate limiter
	if err := c.waitForRateLimit(ctx); err != nil {
		return err
	}

	apiURL := fmt.Sprintf("%s/rest/api/2/issue/%s", c.baseURL, ticketID)

	body := map[string]interface{}{
		"update": map[string]interface{}{
			"labels": []map[string]string{
				{"add": label},
			},
		},
	}
	bodyBytes, err := json.Marshal(body)
	if err != nil {
		return fmt.Errorf("marshaling request body: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPut, apiURL, bytes.NewReader(bodyBytes))
	if err != nil {
		return fmt.Errorf("creating request: %w", err)
	}

	req.Header.Set("Authorization", "Bearer "+c.pat)
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("executing request: %w", err)
	}
	defer resp.Body.Close()

	if err := c.handleErrorResponse(resp, http.StatusOK, http.StatusNoContent); err != nil {
		return err
	}

	return nil
}

func (c *Client) RemoveLabel(ctx context.Context, ticketID, label string) error {
	if err := c.waitForRateLimit(ctx); err != nil {
		return err
	}

	apiURL := fmt.Sprintf("%s/rest/api/2/issue/%s", c.baseURL, ticketID)

	body := map[string]interface{}{
		"update": map[string]interface{}{
			"labels": []map[string]string{
				{"remove": label},
			},
		},
	}
	bodyBytes, err := json.Marshal(body)
	if err != nil {
		return fmt.Errorf("marshaling request body: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPut, apiURL, bytes.NewReader(bodyBytes))
	if err != nil {
		return fmt.Errorf("creating request: %w", err)
	}

	req.Header.Set("Authorization", "Bearer "+c.pat)
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("executing request: %w", err)
	}
	defer resp.Body.Close()

	if err := c.handleErrorResponse(resp, http.StatusOK, http.StatusNoContent); err != nil {
		return err
	}

	return nil
}

// TransitionIssue moves a Jira ticket to a new status using a transition ID.
// Calls POST /rest/api/2/issue/{ticketID}/transitions with the given transition ID.
func (c *Client) TransitionIssue(ctx context.Context, ticketID, transitionID string) error {
	if err := c.waitForRateLimit(ctx); err != nil {
		return err
	}

	apiURL := fmt.Sprintf("%s/rest/api/2/issue/%s/transitions", c.baseURL, ticketID)

	body := map[string]interface{}{
		"transition": map[string]string{
			"id": transitionID,
		},
	}
	bodyBytes, err := json.Marshal(body)
	if err != nil {
		return fmt.Errorf("marshaling request body: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, apiURL, bytes.NewReader(bodyBytes))
	if err != nil {
		return fmt.Errorf("creating request: %w", err)
	}

	req.Header.Set("Authorization", "Bearer "+c.pat)
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("executing request: %w", err)
	}
	defer resp.Body.Close()

	if err := c.handleErrorResponse(resp, http.StatusOK, http.StatusNoContent); err != nil {
		return err
	}

	return nil
}

// CreateIssueResult holds the identifiers Jira returns for a newly created issue.
type CreateIssueResult struct {
	Key string `json:"key"`
	ID  string `json:"id"`
}

// CreateIssue opens a new Jira ticket in the given project.
// Note: this instance's /rest/api/2/issue/createmeta endpoint returned an error
// when probed, so any project-specific required custom fields beyond the
// standard project/summary/description/issuetype couldn't be discovered ahead
// of time — Jira's own error response (surfaced verbatim below) will name them
// on first use if the project requires more.
func (c *Client) CreateIssue(ctx context.Context, projectKey, issueType, summary, description string) (*CreateIssueResult, error) {
	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	apiURL := fmt.Sprintf("%s/rest/api/2/issue", c.baseURL)

	body := map[string]interface{}{
		"fields": map[string]interface{}{
			"project":     map[string]string{"key": projectKey},
			"summary":     summary,
			"description": description,
			"issuetype":   map[string]string{"name": issueType},
		},
	}
	bodyBytes, err := json.Marshal(body)
	if err != nil {
		return nil, fmt.Errorf("marshaling request body: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, apiURL, bytes.NewReader(bodyBytes))
	if err != nil {
		return nil, fmt.Errorf("creating request: %w", err)
	}

	req.Header.Set("Authorization", "Bearer "+c.pat)
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("executing request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusCreated {
		var errBody map[string]interface{}
		if err := json.NewDecoder(resp.Body).Decode(&errBody); err == nil {
			return nil, fmt.Errorf("jira API error (status %d): %v", resp.StatusCode, errBody)
		}
		return nil, fmt.Errorf("jira API error: unexpected status %d", resp.StatusCode)
	}

	var result CreateIssueResult
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("parsing create issue response: %w", err)
	}

	return &result, nil
}

// transitionsResponse mirrors the subset of GET .../transitions we need to
// look up a transition by its destination status name.
type transitionsResponse struct {
	Transitions []struct {
		ID string `json:"id"`
		To struct {
			Name string `json:"name"`
		} `json:"to"`
	} `json:"transitions"`
}

// ResolveIssue transitions a Jira ticket to its "Resolved" status.
// The transition ID for "Resolved" is workflow- and current-status-dependent
// (verified: the same target status can have a different transition ID
// depending on which status the ticket is currently in), so this looks the ID
// up dynamically via the transitions endpoint rather than assuming a fixed ID.
func (c *Client) ResolveIssue(ctx context.Context, ticketID string) error {
	if err := c.waitForRateLimit(ctx); err != nil {
		return err
	}

	apiURL := fmt.Sprintf("%s/rest/api/2/issue/%s/transitions", c.baseURL, ticketID)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, apiURL, nil)
	if err != nil {
		return fmt.Errorf("creating request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+c.pat)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("executing request: %w", err)
	}
	defer resp.Body.Close()

	if err := c.handleErrorResponse(resp, http.StatusOK); err != nil {
		return fmt.Errorf("fetching transitions: %w", err)
	}

	var result transitionsResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return fmt.Errorf("decoding transitions response: %w", err)
	}

	var resolveID string
	for _, t := range result.Transitions {
		if strings.EqualFold(t.To.Name, "Resolved") {
			resolveID = t.ID
			break
		}
	}
	if resolveID == "" {
		return fmt.Errorf("no 'Resolved' transition available for %s from its current status", ticketID)
	}

	return c.TransitionIssue(ctx, ticketID, resolveID)
}

// UpdateIssue updates a Jira ticket's summary and/or description. Pass nil for
// a field to leave it unchanged; at least one of summary or description must
// be non-nil.
func (c *Client) UpdateIssue(ctx context.Context, ticketID string, summary, description *string) error {
	if summary == nil && description == nil {
		return fmt.Errorf("at least one of summary or description must be provided")
	}
	if err := c.waitForRateLimit(ctx); err != nil {
		return err
	}

	apiURL := fmt.Sprintf("%s/rest/api/2/issue/%s", c.baseURL, ticketID)

	fields := map[string]interface{}{}
	if summary != nil {
		fields["summary"] = *summary
	}
	if description != nil {
		fields["description"] = *description
	}
	body := map[string]interface{}{"fields": fields}
	bodyBytes, err := json.Marshal(body)
	if err != nil {
		return fmt.Errorf("marshaling request body: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPut, apiURL, bytes.NewReader(bodyBytes))
	if err != nil {
		return fmt.Errorf("creating request: %w", err)
	}

	req.Header.Set("Authorization", "Bearer "+c.pat)
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("executing request: %w", err)
	}
	defer resp.Body.Close()

	if err := c.handleErrorResponse(resp, http.StatusOK, http.StatusNoContent); err != nil {
		return err
	}

	return nil
}

// UpdateAssignee sets the assignee of a Jira ticket to the given username.
// On Jira Server (self-hosted) the assignee is identified by their username (Name field),
// not an account ID.
func (c *Client) UpdateAssignee(ctx context.Context, ticketID, username string) error {
	if err := c.waitForRateLimit(ctx); err != nil {
		return err
	}

	apiURL := fmt.Sprintf("%s/rest/api/2/issue/%s/assignee", c.baseURL, ticketID)

	body := map[string]string{"name": username}
	bodyBytes, err := json.Marshal(body)
	if err != nil {
		return fmt.Errorf("marshaling request body: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPut, apiURL, bytes.NewReader(bodyBytes))
	if err != nil {
		return fmt.Errorf("creating request: %w", err)
	}

	req.Header.Set("Authorization", "Bearer "+c.pat)
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("executing request: %w", err)
	}
	defer resp.Body.Close()

	if err := c.handleErrorResponse(resp, http.StatusOK, http.StatusNoContent); err != nil {
		return err
	}

	return nil
}

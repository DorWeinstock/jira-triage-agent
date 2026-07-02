package jira

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const (
	defaultMaxResults = 10
	maxSearchResults  = 100
)

func NewMCPServer(client *Client) *mcp.Server {
	server := mcp.NewServer(
		&mcp.Implementation{
			Name:    "jira-mcp",
			Version: "1.0.0",
		},
		nil,
	)

	registerGetTicketTool(server, client)
	registerSearchTicketsTool(server, client)
	registerAddCommentTool(server, client)
	registerAddLabelTool(server, client)
	registerRemoveLabelTool(server, client)
	registerUpdateAssigneeTool(server, client)

	return server
}

// registerGetTicketTool registers the get_ticket MCP tool.
func registerGetTicketTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "get_ticket",
			Description: "Get Jira ticket details by ID including comments",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			TicketID string `json:"ticket_id" jsonschema:"The Jira ticket ID (e.g., PROJ-123)"`
		}) (*mcp.CallToolResult, any, error) {
			if input.TicketID == "" {
				return nil, nil, fmt.Errorf("ticket_id is required")
			}
			ticket, err := client.GetTicket(ctx, input.TicketID)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to get ticket: %w", err)
			}

			output := formatTicketOutput(ticket)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// registerSearchTicketsTool registers the search_tickets MCP tool.
func registerSearchTicketsTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "search_tickets",
			Description: "Search Jira tickets using JQL",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			JQL        string `json:"jql" jsonschema:"JQL query string"`
			MaxResults *int   `json:"max_results,omitempty" jsonschema:"Maximum results to return (default 10)"`
		}) (*mcp.CallToolResult, any, error) {
			if input.JQL == "" {
				return nil, nil, fmt.Errorf("jql is required")
			}
			maxResults := defaultMaxResults
			if input.MaxResults != nil {
				if *input.MaxResults <= 0 {
					return nil, nil, fmt.Errorf("max_results must be positive")
				}
				if *input.MaxResults > maxSearchResults {
					return nil, nil, fmt.Errorf("max_results cannot exceed %d", maxSearchResults)
				}
				maxResults = *input.MaxResults
			}
			tickets, err := client.SearchTickets(ctx, input.JQL, maxResults)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to search tickets: %w", err)
			}
			result := formatSearchResults(tickets)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: result}},
			}, nil, nil
		},
	)
}

// registerAddCommentTool registers the add_comment MCP tool.
// Returns a JSON object with comment_id and created timestamp.
func registerAddCommentTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "add_comment",
			Description: "Add a comment to a Jira ticket. Returns comment_id and created timestamp.",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			TicketID string `json:"ticket_id" jsonschema:"The Jira ticket ID"`
			Comment  string `json:"comment" jsonschema:"The comment text"`
		}) (*mcp.CallToolResult, any, error) {
			if input.TicketID == "" {
				return nil, nil, fmt.Errorf("ticket_id is required")
			}
			if input.Comment == "" {
				return nil, nil, fmt.Errorf("comment is required")
			}
			createdComment, err := client.AddComment(ctx, input.TicketID, input.Comment)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to add comment: %w", err)
			}
			// Return JSON with comment details for HITL timestamp tracking
			// Using json.Marshal for proper escaping of special characters
			response := map[string]string{
				"status":     "success",
				"ticket_id":  input.TicketID,
				"comment_id": createdComment.ID,
				"created":    createdComment.Created,
			}
			msgBytes, err := json.Marshal(response)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to marshal response: %w", err)
			}
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: string(msgBytes)}},
			}, nil, nil
		},
	)
}

// registerAddLabelTool registers the add_label MCP tool.
func registerAddLabelTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "add_label",
			Description: "Add a label to a Jira ticket",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			TicketID string `json:"ticket_id" jsonschema:"The Jira ticket ID"`
			Label    string `json:"label" jsonschema:"The label to add"`
		}) (*mcp.CallToolResult, any, error) {
			if input.TicketID == "" {
				return nil, nil, fmt.Errorf("ticket_id is required")
			}
			if input.Label == "" {
				return nil, nil, fmt.Errorf("label is required")
			}
			if err := client.AddLabel(ctx, input.TicketID, input.Label); err != nil {
				return nil, nil, fmt.Errorf("failed to add label: %w", err)
			}
			msg := fmt.Sprintf("Label '%s' added successfully to %s", input.Label, input.TicketID)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: msg}},
			}, nil, nil
		},
	)
}

// registerRemoveLabelTool registers the remove_label MCP tool.
func registerRemoveLabelTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "remove_label",
			Description: "Remove a label from a Jira ticket",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			TicketID string `json:"ticket_id" jsonschema:"The Jira ticket ID"`
			Label    string `json:"label" jsonschema:"The label to remove"`
		}) (*mcp.CallToolResult, any, error) {
			if input.TicketID == "" {
				return nil, nil, fmt.Errorf("ticket_id is required")
			}
			if input.Label == "" {
				return nil, nil, fmt.Errorf("label is required")
			}
			if err := client.RemoveLabel(ctx, input.TicketID, input.Label); err != nil {
				return nil, nil, fmt.Errorf("failed to remove label: %w", err)
			}
			msg := fmt.Sprintf("Label '%s' removed successfully from %s", input.Label, input.TicketID)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: msg}},
			}, nil, nil
		},
	)
}

// formatTicketOutput formats a ticket into human-readable output with resolution and comments.
func formatTicketOutput(ticket *Ticket) string {
	output := fmt.Sprintf("**Ticket Information**\n\nKey: %s\nSummary: %s\nDescription: %s",
		ticket.Key, ticket.Fields.Summary, ticket.Fields.Description)

	if ticket.Fields.Reporter != nil {
		output += fmt.Sprintf("\nReporter: %s", ticket.Fields.Reporter.Name)
	}
	if ticket.Fields.Assignee != nil {
		output += fmt.Sprintf("\nAssignee: %s", ticket.Fields.Assignee.Name)
	}

	// Add resolution if present
	if ticket.Fields.Resolution != nil {
		output += fmt.Sprintf("\nResolution: %s", ticket.Fields.Resolution.Name)
	}

	// Add components if present
	if len(ticket.Fields.Components) > 0 {
		names := make([]string, 0, len(ticket.Fields.Components))
		for _, c := range ticket.Fields.Components {
			names = append(names, c.Name)
		}
		output += fmt.Sprintf("\nComponents: %s", strings.Join(names, ", "))
	}

	// Add comments if present - critical for HistoryAgent to find resolution details
	if ticket.Fields.Comment != nil && len(ticket.Fields.Comment.Comments) > 0 {
		output += fmt.Sprintf("\n\n**Comments** (%d total):\n", ticket.Fields.Comment.Total)
		for i, comment := range ticket.Fields.Comment.Comments {
			output += fmt.Sprintf("\n--- Comment %d (by %s on %s) ---\n%s\n",
				i+1, comment.Author.DisplayName, comment.Created, comment.Body)
		}
	}

	return output
}

// formatSearchResults formats search results into human-readable output.
// Format: "- KEY [UPDATED] (STATUS): Summary" to preserve date and status info.
func formatSearchResults(tickets []Ticket) string {
	result := fmt.Sprintf("Found %d tickets:\n", len(tickets))
	for _, t := range tickets {
		// Include updated date and resolution status for sorting and display
		status := "OPEN"
		if t.Fields.Resolution != nil && t.Fields.Resolution.Name != "" {
			status = t.Fields.Resolution.Name
		}
		componentTag := ""
		if len(t.Fields.Components) > 0 {
			names := make([]string, 0, len(t.Fields.Components))
			for _, c := range t.Fields.Components {
				names = append(names, c.Name)
			}
			componentTag = fmt.Sprintf(" {%s}", strings.Join(names, ","))
		}
		result += fmt.Sprintf("- %s [%s] (%s)%s: %s\n", t.Key, t.Fields.Updated, status, componentTag, t.Fields.Summary)
	}
	return result
}

// registerMoveToInProgressTool registers the move_to_in_progress MCP tool.
func registerMoveToInProgressTool(server *mcp.Server, client *Client, transitionID string) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "move_to_in_progress",
			Description: "Move a Jira ticket to In Progress status",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			TicketID string `json:"ticket_id" jsonschema:"The Jira ticket ID (e.g., PROJ-123)"`
		}) (*mcp.CallToolResult, any, error) {
			if input.TicketID == "" {
				return nil, nil, fmt.Errorf("ticket_id is required")
			}
			if err := client.TransitionIssue(ctx, input.TicketID, transitionID); err != nil {
				return nil, nil, fmt.Errorf("failed to transition ticket: %w", err)
			}
			response := map[string]string{
				"status":     "success",
				"ticket_id":  input.TicketID,
				"new_status": "In Progress",
			}
			msgBytes, err := json.Marshal(response)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to marshal response: %w", err)
			}
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: string(msgBytes)}},
			}, nil, nil
		},
	)
}

// registerMoveToInReviewTool registers the move_to_in_review MCP tool.
func registerMoveToInReviewTool(server *mcp.Server, client *Client, transitionID string) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "move_to_in_review",
			Description: "Move a Jira ticket to In Review status",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			TicketID string `json:"ticket_id" jsonschema:"The Jira ticket ID (e.g., PROJ-123)"`
		}) (*mcp.CallToolResult, any, error) {
			if input.TicketID == "" {
				return nil, nil, fmt.Errorf("ticket_id is required")
			}
			if err := client.TransitionIssue(ctx, input.TicketID, transitionID); err != nil {
				return nil, nil, fmt.Errorf("failed to transition ticket: %w", err)
			}
			response := map[string]string{
				"status":     "success",
				"ticket_id":  input.TicketID,
				"new_status": "In Review",
			}
			msgBytes, err := json.Marshal(response)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to marshal response: %w", err)
			}
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: string(msgBytes)}},
			}, nil, nil
		},
	)
}

// registerUpdateAssigneeTool registers the update_assignee MCP tool.
// On Jira Server (self-hosted), assignees are set by username (not account ID).
func registerUpdateAssigneeTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "update_assignee",
			Description: "Assign a Jira ticket to a user. On self-hosted Jira, use the Jira username.",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			TicketID string `json:"ticket_id" jsonschema:"The Jira ticket ID (e.g., PROJ-123)"`
			Username string `json:"username" jsonschema:"Jira username to assign the ticket to"`
		}) (*mcp.CallToolResult, any, error) {
			if input.TicketID == "" {
				return nil, nil, fmt.Errorf("ticket_id is required")
			}
			if input.Username == "" {
				return nil, nil, fmt.Errorf("username is required")
			}
			if err := client.UpdateAssignee(ctx, input.TicketID, input.Username); err != nil {
				return nil, nil, fmt.Errorf("failed to update assignee: %w", err)
			}
			response := map[string]string{
				"status":    "success",
				"ticket_id": input.TicketID,
				"assignee":  input.Username,
			}
			msgBytes, err := json.Marshal(response)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to marshal response: %w", err)
			}
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: string(msgBytes)}},
			}, nil, nil
		},
	)
}

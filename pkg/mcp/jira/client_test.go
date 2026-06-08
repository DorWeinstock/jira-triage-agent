package jira_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"jira-triage-agent/pkg/mcp/jira"
)

func TestGetTicket(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/rest/api/2/issue/TEST-123" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		// Verify expand parameter is sent (needed for comments)
		expand := r.URL.Query().Get("expand")
		if !strings.Contains(expand, "comment") {
			t.Errorf("expected expand to contain 'comment', got: %s", expand)
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"key": "TEST-123",
			"fields": map[string]interface{}{
				"summary":     "Test ticket",
				"description": "Test description",
			},
		})
	}))
	defer server.Close()

	client := jira.NewClient(server.URL, "test@example.com", "token")
	ticket, err := client.GetTicket(context.Background(), "TEST-123")

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ticket.Key != "TEST-123" {
		t.Errorf("expected key TEST-123, got %s", ticket.Key)
	}
}

func TestGetTicketWithComments(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Return a ticket with comments and resolution
		json.NewEncoder(w).Encode(map[string]interface{}{
			"key": "TEST-456",
			"fields": map[string]interface{}{
				"summary":     "payment-service failing",
				"description": "CreateContainerConfigError",
				"resolution": map[string]interface{}{
					"name": "Done",
				},
				"comment": map[string]interface{}{
					"total":      2,
					"maxResults": 50,
					"startAt":    0,
					"comments": []map[string]interface{}{
						{
							"id":      "12345",
							"body":    "Fixed by creating ConfigMap: kubectl create configmap payment-config -n production --from-literal=DB_HOST=postgres:5432",
							"created": "2024-01-15T10:30:00.000+0000",
							"author": map[string]interface{}{
								"displayName":  "John Doe",
								"emailAddress": "john@example.com",
							},
						},
						{
							"id":      "12346",
							"body":    "Verified working",
							"created": "2024-01-15T11:00:00.000+0000",
							"author": map[string]interface{}{
								"displayName":  "Jane Smith",
								"emailAddress": "jane@example.com",
							},
						},
					},
				},
			},
		})
	}))
	defer server.Close()

	client := jira.NewClient(server.URL, "test@example.com", "token")
	ticket, err := client.GetTicket(context.Background(), "TEST-456")

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ticket.Key != "TEST-456" {
		t.Errorf("expected key TEST-456, got %s", ticket.Key)
	}
	if ticket.Fields.Resolution == nil {
		t.Fatal("expected resolution to be present")
	}
	if ticket.Fields.Resolution.Name != "Done" {
		t.Errorf("expected resolution name 'Done', got %s", ticket.Fields.Resolution.Name)
	}
	if ticket.Fields.Comment == nil {
		t.Fatal("expected comments to be present")
	}
	if len(ticket.Fields.Comment.Comments) != 2 {
		t.Errorf("expected 2 comments, got %d", len(ticket.Fields.Comment.Comments))
	}
	// Verify first comment contains the fix information
	firstComment := ticket.Fields.Comment.Comments[0]
	if !strings.Contains(firstComment.Body, "kubectl create configmap") {
		t.Errorf("expected first comment to contain ConfigMap creation command, got: %s", firstComment.Body)
	}
	if firstComment.Author.DisplayName != "John Doe" {
		t.Errorf("expected author 'John Doe', got %s", firstComment.Author.DisplayName)
	}
}

func TestGetTicket_NotFound(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"errorMessages": []string{"Issue does not exist"},
		})
	}))
	defer server.Close()

	client := jira.NewClient(server.URL, "test@example.com", "token")
	_, err := client.GetTicket(context.Background(), "NOTFOUND-999")

	if err == nil {
		t.Fatal("expected error for 404 response")
	}
	if !strings.Contains(err.Error(), "404") && !strings.Contains(err.Error(), "Issue does not exist") {
		t.Errorf("error should mention status or message, got: %v", err)
	}
}

func TestGetTicket_Unauthorized(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
	}))
	defer server.Close()

	client := jira.NewClient(server.URL, "test@example.com", "bad-token")
	_, err := client.GetTicket(context.Background(), "TEST-123")

	if err == nil {
		t.Fatal("expected error for 401 response")
	}
}

func TestSearchTickets(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/rest/api/2/search" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"issues": []map[string]interface{}{
				{"key": "TEST-123"},
				{"key": "TEST-124"},
			},
		})
	}))
	defer server.Close()

	client := jira.NewClient(server.URL, "test@example.com", "token")
	tickets, err := client.SearchTickets(context.Background(), "project = TEST", 10)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(tickets) != 2 {
		t.Errorf("expected 2 tickets, got %d", len(tickets))
	}
}

func TestAddComment(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/rest/api/2/issue/TEST-123/comment" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		if r.Method != http.MethodPost {
			t.Errorf("expected POST, got %s", r.Method)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		// Return a comment response matching Jira's API format
		w.Write([]byte(`{
			"id": "12345",
			"body": "Test comment",
			"created": "2026-01-07T10:30:00.000+0000",
			"updated": "2026-01-07T10:30:00.000+0000",
			"author": {"displayName": "Test User", "emailAddress": "test@example.com"}
		}`))
	}))
	defer server.Close()

	client := jira.NewClient(server.URL, "test@example.com", "token")
	comment, err := client.AddComment(context.Background(), "TEST-123", "Test comment")

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if comment == nil {
		t.Fatal("expected comment to be returned")
	}
	if comment.ID != "12345" {
		t.Errorf("expected comment ID '12345', got '%s'", comment.ID)
	}
	if comment.Created != "2026-01-07T10:30:00.000+0000" {
		t.Errorf("expected created timestamp, got '%s'", comment.Created)
	}
}

func TestAddLabel(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/rest/api/2/issue/TEST-123" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		if r.Method != http.MethodPut {
			t.Errorf("expected PUT, got %s", r.Method)
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()

	client := jira.NewClient(server.URL, "test@example.com", "token")
	err := client.AddLabel(context.Background(), "TEST-123", "ai-agent-investigated")

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestTransitionIssue(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/rest/api/2/issue/TEST-123/transitions" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		if r.Method != http.MethodPost {
			t.Errorf("expected POST, got %s", r.Method)
		}

		var body map[string]interface{}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatalf("failed to decode request body: %v", err)
		}
		transition, ok := body["transition"].(map[string]interface{})
		if !ok {
			t.Fatal("expected 'transition' key in body")
		}
		if transition["id"] != "21" {
			t.Errorf("expected transition id '21', got '%v'", transition["id"])
		}

		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()

	client := jira.NewClient(server.URL, "test@example.com", "token")
	err := client.TransitionIssue(context.Background(), "TEST-123", "21")

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestTransitionIssue_InvalidTransition(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"errorMessages": []string{"It is not on the appropriate screen, or unknown."},
		})
	}))
	defer server.Close()

	client := jira.NewClient(server.URL, "test@example.com", "token")
	err := client.TransitionIssue(context.Background(), "TEST-123", "999")

	if err == nil {
		t.Fatal("expected error for invalid transition")
	}
	if !strings.Contains(err.Error(), "400") {
		t.Errorf("error should mention status code, got: %v", err)
	}
}

func TestRemoveLabel(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/rest/api/2/issue/TEST-123" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		if r.Method != http.MethodPut {
			t.Errorf("expected PUT, got %s", r.Method)
		}

		// Verify the request body contains "remove"
		var body map[string]interface{}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatalf("failed to decode request body: %v", err)
		}
		update, ok := body["update"].(map[string]interface{})
		if !ok {
			t.Fatal("expected 'update' key in body")
		}
		labels, ok := update["labels"].([]interface{})
		if !ok || len(labels) == 0 {
			t.Fatal("expected 'labels' array in update")
		}
		labelOp, ok := labels[0].(map[string]interface{})
		if !ok {
			t.Fatal("expected label operation map")
		}
		if _, ok := labelOp["remove"]; !ok {
			t.Error("expected 'remove' operation, got something else")
		}

		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()

	client := jira.NewClient(server.URL, "test@example.com", "token")
	err := client.RemoveLabel(context.Background(), "TEST-123", "ai-investigate-in-progress")

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

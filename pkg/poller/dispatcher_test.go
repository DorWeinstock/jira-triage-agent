package poller_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"jira-triage-agent/pkg/poller"
)

func TestHTTPDispatcher(t *testing.T) {
	var receivedTicketID string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			TicketID string `json:"ticket_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Errorf("failed to decode request: %v", err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		receivedTicketID = req.TicketID
		w.WriteHeader(http.StatusAccepted)
	}))
	defer server.Close()

	dispatcher := poller.NewHTTPDispatcher(server.URL + "/investigate")
	err := dispatcher.Dispatch(context.Background(), "TEST-123")

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if receivedTicketID != "TEST-123" {
		t.Errorf("expected TEST-123, got %s", receivedTicketID)
	}
}

func TestHTTPDispatcher_StatusOK(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"status":"completed","ticket_id":"TEST-456"}`))
	}))
	defer server.Close()

	dispatcher := poller.NewHTTPDispatcher(server.URL)
	err := dispatcher.Dispatch(context.Background(), "TEST-456")

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

// TestHTTPDispatcher_InternalFailureWith200 reproduces the real bug found while
// triaging GAUDISW-249881 and 19 other tickets: langgraph-agent's /triage catches
// its own exceptions and still replies HTTP 200 with status:"failed" in the body.
// Checking only the HTTP status code treated this as a successful dispatch, so
// jira-agent never removed the triage-in-progress label — permanently excluding
// the ticket from every future poll (labels NOT IN (triage-agent-done,
// triage-in-progress)) despite the ticket never actually being triaged.
func TestHTTPDispatcher_InternalFailureWith200(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"status":"failed","ticket_id":"GAUDISW-249881","error":"'Settings' object has no attribute 'mcp_connection_timeout'"}`))
	}))
	defer server.Close()

	dispatcher := poller.NewHTTPDispatcher(server.URL)
	err := dispatcher.Dispatch(context.Background(), "GAUDISW-249881")

	if err == nil {
		t.Fatal("expected error for status:\"failed\" body despite HTTP 200, got nil")
	}
}

func TestHTTPDispatcher_MalformedBodyWith200(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("not json"))
	}))
	defer server.Close()

	dispatcher := poller.NewHTTPDispatcher(server.URL)
	err := dispatcher.Dispatch(context.Background(), "TEST-MALFORMED")

	if err == nil {
		t.Fatal("expected error for unparseable body on 200, got nil")
	}
}

func TestHTTPDispatcher_Non2xxStatus(t *testing.T) {
	tests := []struct {
		name       string
		statusCode int
	}{
		{"BadRequest", http.StatusBadRequest},
		{"InternalServerError", http.StatusInternalServerError},
		{"NotFound", http.StatusNotFound},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(tt.statusCode)
			}))
			defer server.Close()

			dispatcher := poller.NewHTTPDispatcher(server.URL)
			err := dispatcher.Dispatch(context.Background(), "TEST-789")

			if err == nil {
				t.Fatal("expected error for non-2xx status, got nil")
			}
		})
	}
}

func TestHTTPDispatcher_ContextCancellation(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(100 * time.Millisecond)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // Cancel immediately

	dispatcher := poller.NewHTTPDispatcher(server.URL)
	err := dispatcher.Dispatch(ctx, "TEST-CANCEL")

	if err == nil {
		t.Fatal("expected error for cancelled context, got nil")
	}
}

func TestHTTPDispatcher_InvalidURL(t *testing.T) {
	dispatcher := poller.NewHTTPDispatcher("http://invalid-host-that-does-not-exist:99999")
	err := dispatcher.Dispatch(context.Background(), "TEST-INVALID")

	if err == nil {
		t.Fatal("expected error for invalid URL, got nil")
	}
}

func TestHTTPDispatcher_EmptyTicketID(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			TicketID string `json:"ticket_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Errorf("failed to decode request: %v", err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		if req.TicketID == "" {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	dispatcher := poller.NewHTTPDispatcher(server.URL)
	err := dispatcher.Dispatch(context.Background(), "")

	if err == nil {
		t.Fatal("expected error for empty ticket ID, got nil")
	}
}

func TestHTTPDispatcher_ContentType(t *testing.T) {
	var receivedContentType string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedContentType = r.Header.Get("Content-Type")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"status":"completed","ticket_id":"TEST-CT"}`))
	}))
	defer server.Close()

	dispatcher := poller.NewHTTPDispatcher(server.URL)
	if err := dispatcher.Dispatch(context.Background(), "TEST-CT"); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if receivedContentType != "application/json" {
		t.Errorf("expected Content-Type: application/json, got %s", receivedContentType)
	}
}

func TestHTTPDispatcher_HTTPMethod(t *testing.T) {
	var receivedMethod string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedMethod = r.Method
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"status":"completed","ticket_id":"TEST-METHOD"}`))
	}))
	defer server.Close()

	dispatcher := poller.NewHTTPDispatcher(server.URL)
	if err := dispatcher.Dispatch(context.Background(), "TEST-METHOD"); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if receivedMethod != http.MethodPost {
		t.Errorf("expected POST method, got %s", receivedMethod)
	}
}

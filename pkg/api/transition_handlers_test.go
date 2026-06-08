package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/go-chi/chi/v5"
	"go.uber.org/zap"

	"jira-triage-agent/pkg/mcp/jira"
)

func TestTransitionToInProgress(t *testing.T) {
	// Mock Jira server that accepts transition requests
	jiraServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost && r.URL.Path == "/rest/api/2/issue/TEST-123/transitions" {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		http.Error(w, "not found", http.StatusNotFound)
	}))
	defer jiraServer.Close()

	client := jira.NewClient(jiraServer.URL, "test@example.com", "token")
	handler := NewTransitionHandler(client, "11", "31", "41", zap.NewNop())

	r := chi.NewRouter()
	r.Post("/api/transition/{ticketID}/in-progress", handler.TransitionToInProgress)

	req := httptest.NewRequest(http.MethodPost, "/api/transition/TEST-123/in-progress", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]string
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	if resp["status"] != "success" {
		t.Fatalf("expected success, got %v", resp)
	}
	if resp["new_status"] != "In Progress" {
		t.Fatalf("expected 'In Progress', got %q", resp["new_status"])
	}
}

func TestTransitionToInReview(t *testing.T) {
	jiraServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost && r.URL.Path == "/rest/api/2/issue/TEST-456/transitions" {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		http.Error(w, "not found", http.StatusNotFound)
	}))
	defer jiraServer.Close()

	client := jira.NewClient(jiraServer.URL, "test@example.com", "token")
	handler := NewTransitionHandler(client, "11", "31", "41", zap.NewNop())

	r := chi.NewRouter()
	r.Post("/api/transition/{ticketID}/in-review", handler.TransitionToInReview)

	req := httptest.NewRequest(http.MethodPost, "/api/transition/TEST-456/in-review", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]string
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	if resp["status"] != "success" {
		t.Fatalf("expected success, got %v", resp)
	}
}

func TestTransitionToInProgressChainsTwoTransitions(t *testing.T) {
	var calls []string
	jiraServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost {
			calls = append(calls, r.URL.Path)
			w.WriteHeader(http.StatusNoContent)
			return
		}
		http.Error(w, "not found", http.StatusNotFound)
	}))
	defer jiraServer.Close()

	client := jira.NewClient(jiraServer.URL, "test@example.com", "token")
	handler := NewTransitionHandler(client, "11", "31", "41", zap.NewNop())

	r := chi.NewRouter()
	r.Post("/api/transition/{ticketID}/in-progress", handler.TransitionToInProgress)

	req := httptest.NewRequest(http.MethodPost, "/api/transition/TEST-789/in-progress", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if len(calls) != 2 {
		t.Fatalf("expected 2 Jira API calls (assign + in-progress), got %d: %v", len(calls), calls)
	}
	if calls[0] != "/rest/api/2/issue/TEST-789/transitions" {
		t.Fatalf("expected assign call first, got %s", calls[0])
	}
}

func TestTransitionNotConfigured(t *testing.T) {
	client := jira.NewClient("http://fake", "test@example.com", "token")
	handler := NewTransitionHandler(client, "", "", "", zap.NewNop())

	r := chi.NewRouter()
	r.Post("/api/transition/{ticketID}/in-progress", handler.TransitionToInProgress)

	req := httptest.NewRequest(http.MethodPost, "/api/transition/TEST-123/in-progress", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusNotImplemented {
		t.Fatalf("expected 501, got %d", w.Code)
	}
}

func TestTransitionJiraError(t *testing.T) {
	jiraServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		w.Write([]byte(`{"errorMessages":["invalid transition"]}`))
	}))
	defer jiraServer.Close()

	client := jira.NewClient(jiraServer.URL, "test@example.com", "token")
	handler := NewTransitionHandler(client, "11", "31", "41", zap.NewNop())

	r := chi.NewRouter()
	r.Post("/api/transition/{ticketID}/in-progress", handler.TransitionToInProgress)

	req := httptest.NewRequest(http.MethodPost, "/api/transition/TEST-123/in-progress", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusBadGateway {
		t.Fatalf("expected 502, got %d", w.Code)
	}
}

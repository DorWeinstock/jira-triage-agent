package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func vllmServer(t *testing.T, result EvalResult, statusCode int) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if statusCode != http.StatusOK {
			w.WriteHeader(statusCode)
			return
		}
		content, _ := json.Marshal(result)
		resp := map[string]any{
			"choices": []map[string]any{
				{"message": map[string]string{"content": string(content)}},
			},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
}

func TestEvaluate_ValidTicket(t *testing.T) {
	want := EvalResult{
		Verdict:          VerdictValid,
		Comment:          "",
		JenkinsLinkFound: true,
		ServerNameFound:  true,
		Scope:            "k8s",
	}
	srv := vllmServer(t, want, http.StatusOK)
	defer srv.Close()

	e := newEvaluator(srv.URL, "test-model")
	got, err := e.Evaluate(context.Background(), "Pod crash", "Jenkins: http://jenkins/job/abc server: node-01")
	if err != nil {
		t.Fatal(err)
	}
	if got.Verdict != VerdictValid {
		t.Errorf("verdict = %q, want %q", got.Verdict, VerdictValid)
	}
	if !got.JenkinsLinkFound || !got.ServerNameFound {
		t.Error("expected jenkins and server found to be true")
	}
}

func TestEvaluate_SpamNoJenkinsLink(t *testing.T) {
	want := EvalResult{
		Verdict:          VerdictSpam,
		Comment:          "Please include a Jenkins job link so the team can reproduce the issue.",
		JenkinsLinkFound: false,
		ServerNameFound:  false,
		Scope:            "k8s",
	}
	srv := vllmServer(t, want, http.StatusOK)
	defer srv.Close()

	e := newEvaluator(srv.URL, "test-model")
	got, err := e.Evaluate(context.Background(), "Something failed", "No details.")
	if err != nil {
		t.Fatal(err)
	}
	if got.Verdict != VerdictSpam {
		t.Errorf("verdict = %q, want %q", got.Verdict, VerdictSpam)
	}
	if got.Comment != want.Comment {
		t.Errorf("comment = %q, want %q", got.Comment, want.Comment)
	}
}

func TestEvaluate_SpamOutOfScope(t *testing.T) {
	want := EvalResult{
		Verdict:          VerdictSpam,
		Comment:          "This appears to be a hardware issue, outside the K8s team's scope.",
		JenkinsLinkFound: true,
		ServerNameFound:  true,
		Scope:            "out_of_scope",
	}
	srv := vllmServer(t, want, http.StatusOK)
	defer srv.Close()

	e := newEvaluator(srv.URL, "test-model")
	got, err := e.Evaluate(context.Background(), "NIC failure on node", "Jenkins: http://j/job/x server: srv-01 NIC link down")
	if err != nil {
		t.Fatal(err)
	}
	if got.Scope != "out_of_scope" {
		t.Errorf("scope = %q, want out_of_scope", got.Scope)
	}
}

func TestEvaluate_Non200Status(t *testing.T) {
	srv := vllmServer(t, EvalResult{}, http.StatusInternalServerError)
	defer srv.Close()

	e := newEvaluator(srv.URL, "test-model")
	_, err := e.Evaluate(context.Background(), "title", "desc")
	if err == nil {
		t.Fatal("expected error on non-200 status, got nil")
	}
}

func TestEvaluate_InvalidJSON(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		resp := map[string]any{
			"choices": []map[string]any{
				{"message": map[string]string{"content": "not valid json {{{}"}},
			},
		}
		json.NewEncoder(w).Encode(resp)
	}))
	defer srv.Close()

	e := newEvaluator(srv.URL, "test-model")
	_, err := e.Evaluate(context.Background(), "title", "desc")
	if err == nil {
		t.Fatal("expected error on invalid JSON content, got nil")
	}
}

func TestEvaluate_EmptyChoices(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"choices": []any{}})
	}))
	defer srv.Close()

	e := newEvaluator(srv.URL, "test-model")
	_, err := e.Evaluate(context.Background(), "title", "desc")
	if err == nil {
		t.Fatal("expected error on empty choices, got nil")
	}
}

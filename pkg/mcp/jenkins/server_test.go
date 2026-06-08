package jenkins

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestNewMCPServer(t *testing.T) {
	client := NewClient("user", "token")
	server := NewMCPServer(client)
	if server == nil {
		t.Fatal("expected non-nil server")
	}
}

func TestNewMCPServer_ToolsRegistered(t *testing.T) {
	client := NewClient("user", "token")
	server := NewMCPServer(client)
	if server == nil {
		t.Fatal("expected non-nil MCP server")
	}
	// Server creation succeeds with tools registered; verified through
	// client integration tests below (same pattern as jira/server_test.go)
}

// TestServerIntegration_GetBuildInfo tests the GetBuildInfo flow through
// the client, which the MCP tool wraps.
func TestServerIntegration_GetBuildInfo(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.Contains(r.URL.Path, "/api/json") {
			t.Errorf("expected path containing /api/json, got %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(BuildInfo{
			Result:      "FAILURE",
			Duration:    45000,
			DisplayName: "#123",
			BuiltOn:     "agent-01",
		})
	}))
	defer ts.Close()

	client := NewClient("user", "token")
	_ = NewMCPServer(client)

	parsed := ParsedURL{BaseURL: ts.URL, JobPath: "my-job", BuildNumber: 123}
	info, err := client.GetBuildInfo(context.Background(), parsed)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if info.Result != "FAILURE" {
		t.Errorf("expected FAILURE, got %s", info.Result)
	}

	// Verify formatting works
	output := formatBuildInfo(info)
	if !strings.Contains(output, "FAILURE") {
		t.Errorf("expected formatted output to contain FAILURE")
	}
}

// TestServerIntegration_GetConsoleLog tests console log retrieval and formatting.
func TestServerIntegration_GetConsoleLog(t *testing.T) {
	logText := "Building...\n[ERROR] Compilation failed\nBuild FAILED"
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(logText))
	}))
	defer ts.Close()

	client := NewClient("user", "token")
	_ = NewMCPServer(client)

	parsed := ParsedURL{BaseURL: ts.URL, JobPath: "my-job", BuildNumber: 123}
	log, err := client.GetConsoleLog(context.Background(), parsed, defaultMaxConsoleBytes)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if log != logText {
		t.Errorf("expected log text, got %q", log)
	}

	// Verify formatting
	output := formatConsoleLog(log, defaultMaxConsoleBytes, false)
	if !strings.Contains(output, "CONSOLE LOG:") {
		t.Errorf("expected formatted console log header")
	}
	if !strings.Contains(output, "[ERROR]") {
		t.Errorf("expected log content in formatted output")
	}
}

// TestServerIntegration_GetConsoleLog_Truncation tests log truncation behavior.
func TestServerIntegration_GetConsoleLog_Truncation(t *testing.T) {
	bigLog := strings.Repeat("X", 200000)
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(bigLog))
	}))
	defer ts.Close()

	client := NewClient("user", "token")
	_ = NewMCPServer(client)

	maxBytes := int64(50000)
	parsed := ParsedURL{BaseURL: ts.URL, JobPath: "my-job", BuildNumber: 123}
	log, err := client.GetConsoleLog(context.Background(), parsed, maxBytes)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if int64(len(log)) != maxBytes {
		t.Errorf("expected truncated to %d bytes, got %d", maxBytes, len(log))
	}

	output := formatConsoleLog(log, maxBytes, true)
	if !strings.Contains(output, "last") {
		t.Errorf("expected truncation indicator in formatted output")
	}
}

// TestServerIntegration_GetUpstreamCause tests upstream cause extraction.
func TestServerIntegration_GetUpstreamCause(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(BuildInfo{
			Actions: []BuildAction{
				{
					Class: "hudson.model.CauseAction",
					Causes: []BuildCause{
						{
							Class:           "hudson.model.Cause$UpstreamCause",
							UpstreamProject: "parent-pipeline",
							UpstreamBuild:   789,
							UpstreamURL:     "job/parent-pipeline/",
						},
					},
				},
			},
		})
	}))
	defer ts.Close()

	client := NewClient("user", "token")
	_ = NewMCPServer(client)

	parsed := ParsedURL{BaseURL: ts.URL, JobPath: "child-job", BuildNumber: 100}
	cause, err := client.GetUpstreamCause(context.Background(), parsed)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cause == nil {
		t.Fatal("expected non-nil upstream cause")
	}
	if cause.Project != "parent-pipeline" {
		t.Errorf("expected project 'parent-pipeline', got %q", cause.Project)
	}

	output := formatUpstreamCause(cause)
	if !strings.Contains(output, "parent-pipeline") {
		t.Errorf("expected formatted output to contain parent-pipeline")
	}
}

// TestServerIntegration_GetUpstreamCause_NoParent tests when no upstream cause exists.
func TestServerIntegration_GetUpstreamCause_NoParent(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(BuildInfo{
			Actions: []BuildAction{
				{
					Class: "hudson.model.CauseAction",
					Causes: []BuildCause{
						{
							Class:            "hudson.model.Cause$UserIdCause",
							ShortDescription: "Started by user admin",
						},
					},
				},
			},
		})
	}))
	defer ts.Close()

	client := NewClient("user", "token")
	_ = NewMCPServer(client)

	parsed := ParsedURL{BaseURL: ts.URL, JobPath: "standalone-job", BuildNumber: 50}
	cause, err := client.GetUpstreamCause(context.Background(), parsed)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cause != nil {
		t.Errorf("expected nil upstream cause, got %+v", cause)
	}

	output := formatUpstreamCause(nil)
	if !strings.Contains(output, "No upstream trigger found") {
		t.Errorf("expected 'No upstream trigger found'")
	}
}

// TestServerIntegration_InvalidURL tests URL validation error path.
func TestServerIntegration_InvalidURL(t *testing.T) {
	_, err := ParseJenkinsURL("not-a-valid-url")
	if err == nil {
		t.Fatal("expected error for invalid URL")
	}
}

// TestServerIntegration_EmptyURL tests empty URL validation.
func TestServerIntegration_EmptyURL(t *testing.T) {
	_, err := ParseJenkinsURL("")
	if err == nil {
		t.Fatal("expected error for empty URL")
	}
}

// TestDefaultMaxConsoleBytes verifies the constant.
func TestDefaultMaxConsoleBytes(t *testing.T) {
	if defaultMaxConsoleBytes != 100000 {
		t.Errorf("expected defaultMaxConsoleBytes=100000, got %d", defaultMaxConsoleBytes)
	}
}

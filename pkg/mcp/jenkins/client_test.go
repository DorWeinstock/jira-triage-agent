package jenkins

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"unicode/utf8"
)

func TestNewClient(t *testing.T) {
	c := NewClient("testuser", "testtoken")
	if c == nil {
		t.Fatal("expected non-nil client")
	}
	if c.username != "testuser" {
		t.Errorf("expected username 'testuser', got %q", c.username)
	}
	if c.apiToken != "testtoken" {
		t.Errorf("expected apiToken 'testtoken', got %q", c.apiToken)
	}
	if c.httpClient == nil {
		t.Fatal("expected non-nil httpClient")
	}
	if c.httpClient.Timeout == 0 {
		t.Error("expected non-zero timeout")
	}
}

func TestNewClient_NoAuth(t *testing.T) {
	c := NewClient("", "")
	if c == nil {
		t.Fatal("expected non-nil client even without auth")
	}
	if c.username != "" {
		t.Errorf("expected empty username, got %q", c.username)
	}
}

func TestGetBuildInfo_Success(t *testing.T) {
	buildInfo := BuildInfo{
		Result:      "FAILURE",
		Duration:    45000,
		Timestamp:   1709900000000,
		DisplayName: "#123 - my-branch",
		BuiltOn:     "build-agent-03",
		URL:         "https://jenkins.example.com/job/my-job/123/",
		FullName:    "my-job #123",
		Actions:     []BuildAction{},
	}

	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Verify auth header
		user, pass, ok := r.BasicAuth()
		if !ok || user != "testuser" || pass != "testtoken" {
			t.Errorf("expected Basic Auth with testuser/testtoken, got %s/%s (ok=%v)", user, pass, ok)
		}
		if !strings.HasSuffix(r.URL.Path, "/api/json") {
			t.Errorf("expected path ending with /api/json, got %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(buildInfo)
	}))
	defer ts.Close()

	c := NewClient("testuser", "testtoken")
	parsed := ParsedURL{BaseURL: ts.URL, JobPath: "my-job", BuildNumber: 123}

	info, err := c.GetBuildInfo(context.Background(), parsed)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if info.Result != "FAILURE" {
		t.Errorf("expected Result 'FAILURE', got %q", info.Result)
	}
	if info.Duration != 45000 {
		t.Errorf("expected Duration 45000, got %d", info.Duration)
	}
	if info.DisplayName != "#123 - my-branch" {
		t.Errorf("expected DisplayName '#123 - my-branch', got %q", info.DisplayName)
	}
	if info.BuiltOn != "build-agent-03" {
		t.Errorf("expected BuiltOn 'build-agent-03', got %q", info.BuiltOn)
	}
}

func TestGetBuildInfo_NoAuth(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Verify NO auth header when username is empty
		_, _, ok := r.BasicAuth()
		if ok {
			t.Error("expected no Basic Auth header for anonymous client")
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(BuildInfo{Result: "SUCCESS"})
	}))
	defer ts.Close()

	c := NewClient("", "")
	parsed := ParsedURL{BaseURL: ts.URL, JobPath: "my-job", BuildNumber: 1}
	info, err := c.GetBuildInfo(context.Background(), parsed)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if info.Result != "SUCCESS" {
		t.Errorf("expected Result 'SUCCESS', got %q", info.Result)
	}
}

func TestGetBuildInfo_NotFound(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}))
	defer ts.Close()

	c := NewClient("user", "token")
	parsed := ParsedURL{BaseURL: ts.URL, JobPath: "missing-job", BuildNumber: 999}

	_, err := c.GetBuildInfo(context.Background(), parsed)
	if err == nil {
		t.Fatal("expected error for 404")
	}
	if !strings.Contains(err.Error(), "404") {
		t.Errorf("expected error to contain '404', got %q", err.Error())
	}
}

func TestGetBuildInfo_AuthFailure(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
	}))
	defer ts.Close()

	c := NewClient("baduser", "badtoken")
	parsed := ParsedURL{BaseURL: ts.URL, JobPath: "my-job", BuildNumber: 1}

	_, err := c.GetBuildInfo(context.Background(), parsed)
	if err == nil {
		t.Fatal("expected error for 401")
	}
	if !strings.Contains(err.Error(), "401") {
		t.Errorf("expected error to contain '401', got %q", err.Error())
	}
}

func TestGetConsoleLog_Success(t *testing.T) {
	logText := "Building project...\nCompiling...\nDone."
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/consoleText") {
			t.Errorf("expected path ending with /consoleText, got %s", r.URL.Path)
		}
		w.Write([]byte(logText))
	}))
	defer ts.Close()

	c := NewClient("user", "token")
	parsed := ParsedURL{BaseURL: ts.URL, JobPath: "my-job", BuildNumber: 123}

	log, err := c.GetConsoleLog(context.Background(), parsed, 100000)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if log != logText {
		t.Errorf("expected log %q, got %q", logText, log)
	}
}

func TestGetConsoleLog_LargeLog(t *testing.T) {
	// Create a 200KB log
	bigLog := strings.Repeat("X", 200000)
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(bigLog))
	}))
	defer ts.Close()

	c := NewClient("user", "token")
	parsed := ParsedURL{BaseURL: ts.URL, JobPath: "my-job", BuildNumber: 123}
	maxBytes := int64(100000)

	log, err := c.GetConsoleLog(context.Background(), parsed, maxBytes)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if int64(len(log)) != maxBytes {
		t.Errorf("expected log length %d, got %d", maxBytes, len(log))
	}
	// Should be the last maxBytes (tail)
	if log != bigLog[200000-100000:] {
		t.Error("expected last 100KB of log")
	}
}

func TestGetConsoleLog_UTF8Truncation(t *testing.T) {
	// Create log with multi-byte UTF-8 chars at the truncation boundary.
	// "日" is 3 bytes (0xE6, 0x97, 0xA5). If we truncate mid-character,
	// strings.ToValidUTF8 should strip the invalid leading bytes.
	prefix := strings.Repeat("A", 50)
	suffix := strings.Repeat("日", 20) // 20 * 3 = 60 bytes of UTF-8
	fullLog := prefix + suffix           // 50 + 60 = 110 bytes

	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(fullLog))
	}))
	defer ts.Close()

	c := NewClient("user", "token")
	parsed := ParsedURL{BaseURL: ts.URL, JobPath: "my-job", BuildNumber: 123}

	// Truncate to 100 bytes — will cut into the multi-byte region
	log, err := c.GetConsoleLog(context.Background(), parsed, 100)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Result must be valid UTF-8 (no broken multi-byte sequences)
	if !utf8.ValidString(log) {
		t.Errorf("expected valid UTF-8 after truncation, got invalid string")
	}

	// Length should be <= 100 bytes (may be slightly less due to stripped partial char)
	if len(log) > 100 {
		t.Errorf("expected log length <= 100, got %d", len(log))
	}
}

func TestGetUpstreamCause_Found(t *testing.T) {
	buildInfo := BuildInfo{
		Actions: []BuildAction{
			{
				Class: "hudson.model.CauseAction",
				Causes: []BuildCause{
					{
						Class:            "hudson.model.Cause$UpstreamCause",
						ShortDescription: "Started by upstream project",
						UpstreamProject:  "parent-job",
						UpstreamBuild:    456,
						UpstreamURL:      "job/parent-job/",
					},
				},
			},
		},
	}

	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(buildInfo)
	}))
	defer ts.Close()

	c := NewClient("user", "token")
	parsed := ParsedURL{BaseURL: ts.URL, JobPath: "my-job", BuildNumber: 123}

	cause, err := c.GetUpstreamCause(context.Background(), parsed)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cause == nil {
		t.Fatal("expected non-nil upstream cause")
	}
	if cause.Project != "parent-job" {
		t.Errorf("expected project 'parent-job', got %q", cause.Project)
	}
	if cause.Build != 456 {
		t.Errorf("expected build 456, got %d", cause.Build)
	}
}

func TestGetUpstreamCause_NoUpstream(t *testing.T) {
	buildInfo := BuildInfo{
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
	}

	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(buildInfo)
	}))
	defer ts.Close()

	c := NewClient("user", "token")
	parsed := ParsedURL{BaseURL: ts.URL, JobPath: "my-job", BuildNumber: 123}

	cause, err := c.GetUpstreamCause(context.Background(), parsed)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cause != nil {
		t.Errorf("expected nil upstream cause, got %+v", cause)
	}
}

func TestParseJenkinsURL_Valid(t *testing.T) {
	tests := []struct {
		name        string
		url         string
		wantBase    string
		wantPath    string
		wantBuild   int
	}{
		{
			name:      "simple job",
			url:       "https://jenkins.example.com/job/my-job/123/",
			wantBase:  "https://jenkins.example.com",
			wantPath:  "my-job",
			wantBuild: 123,
		},
		{
			name:      "nested folder",
			url:       "https://sw-jenkins.com/job/folder/job/sub/job/name/456/",
			wantBase:  "https://sw-jenkins.com",
			wantPath:  "folder/job/sub/job/name",
			wantBuild: 456,
		},
		{
			name:      "no trailing slash",
			url:       "https://jenkins.example.com/job/my-job/789",
			wantBase:  "https://jenkins.example.com",
			wantPath:  "my-job",
			wantBuild: 789,
		},
		{
			name:      "http scheme",
			url:       "http://ci.internal.com/job/build-pipeline/42/",
			wantBase:  "http://ci.internal.com",
			wantPath:  "build-pipeline",
			wantBuild: 42,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			parsed, err := ParseJenkinsURL(tt.url)
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if parsed.BaseURL != tt.wantBase {
				t.Errorf("BaseURL: want %q, got %q", tt.wantBase, parsed.BaseURL)
			}
			if parsed.JobPath != tt.wantPath {
				t.Errorf("JobPath: want %q, got %q", tt.wantPath, parsed.JobPath)
			}
			if parsed.BuildNumber != tt.wantBuild {
				t.Errorf("BuildNumber: want %d, got %d", tt.wantBuild, parsed.BuildNumber)
			}
		})
	}
}

func TestParseJenkinsURL_Invalid(t *testing.T) {
	tests := []struct {
		name string
		url  string
	}{
		{"empty", ""},
		{"no job path", "https://jenkins.example.com/123/"},
		{"no build number", "https://jenkins.example.com/job/my-job/"},
		{"not a URL", "not-a-url"},
		{"missing scheme", "jenkins.example.com/job/my-job/123/"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := ParseJenkinsURL(tt.url)
			if err == nil {
				t.Error("expected error for invalid URL")
			}
		})
	}
}

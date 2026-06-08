package jenkins

import (
	"strings"
	"testing"
)

func TestFormatBuildInfo_Complete(t *testing.T) {
	info := &BuildInfo{
		Result:      "FAILURE",
		Duration:    2723000, // 45m 23s
		Timestamp:   1709900000000,
		DisplayName: "#123 - my-branch",
		BuiltOn:     "build-agent-03",
	}
	result := formatBuildInfo(info)

	checks := []string{
		"BUILD INFO:",
		"Result: FAILURE",
		"Duration:",
		"Display Name: #123 - my-branch",
		"Node: build-agent-03",
	}
	for _, check := range checks {
		if !strings.Contains(result, check) {
			t.Errorf("expected output to contain %q, got:\n%s", check, result)
		}
	}
}

func TestFormatBuildInfo_NoResult(t *testing.T) {
	info := &BuildInfo{
		Result:   "",
		Duration: 1000,
	}
	result := formatBuildInfo(info)

	if !strings.Contains(result, "IN_PROGRESS") {
		t.Errorf("expected 'IN_PROGRESS' for empty result, got:\n%s", result)
	}
}

func TestFormatConsoleLog_Normal(t *testing.T) {
	log := "Building...\nCompiling...\nDone."
	result := formatConsoleLog(log, 100000, false)

	if !strings.Contains(result, "CONSOLE LOG:") {
		t.Errorf("expected 'CONSOLE LOG:' header, got:\n%s", result)
	}
	if !strings.Contains(result, "Building...") {
		t.Errorf("expected log content, got:\n%s", result)
	}
}

func TestFormatConsoleLog_Truncated(t *testing.T) {
	log := "...truncated content..."
	result := formatConsoleLog(log, 100000, true)

	if !strings.Contains(result, "last") {
		t.Errorf("expected truncation indicator with 'last', got:\n%s", result)
	}
}

func TestFormatConsoleLog_Empty(t *testing.T) {
	result := formatConsoleLog("", 100000, false)

	if !strings.Contains(result, "No console output available") {
		t.Errorf("expected 'No console output available', got:\n%s", result)
	}
}

func TestFormatUpstreamCause_Found(t *testing.T) {
	cause := &UpstreamCause{
		Project: "parent-job",
		Build:   456,
		URL:     "job/parent-job/",
	}
	result := formatUpstreamCause(cause)

	if !strings.Contains(result, "UPSTREAM CAUSE:") {
		t.Errorf("expected 'UPSTREAM CAUSE:' header, got:\n%s", result)
	}
	if !strings.Contains(result, "parent-job") {
		t.Errorf("expected 'parent-job' in output, got:\n%s", result)
	}
	if !strings.Contains(result, "456") {
		t.Errorf("expected build number '456', got:\n%s", result)
	}
}

func TestFormatUpstreamCause_None(t *testing.T) {
	result := formatUpstreamCause(nil)

	if !strings.Contains(result, "No upstream trigger found") {
		t.Errorf("expected 'No upstream trigger found', got:\n%s", result)
	}
}

func TestFormatDuration(t *testing.T) {
	tests := []struct {
		name     string
		ms       int64
		contains string
	}{
		{"seconds", 45000, "45s"},
		{"minutes and seconds", 123000, "2m"},
		{"hours", 8130000, "2h"},
		{"zero", 0, "0s"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := formatDuration(tt.ms)
			if !strings.Contains(result, tt.contains) {
				t.Errorf("expected %q to contain %q", result, tt.contains)
			}
		})
	}
}

package logger

import (
	"os"
	"testing"

	"go.uber.org/zap/zapcore"
)

func TestNewLogger_DefaultsToJSONInfo(t *testing.T) {
	// Clear env vars
	os.Unsetenv("LOG_FORMAT")
	os.Unsetenv("LOG_LEVEL")

	logger := New()
	if logger == nil {
		t.Fatal("expected non-nil logger")
	}

	// Verify it can log without panic
	logger.Info("test message")
}

func TestNewLogger_ConsoleFormat(t *testing.T) {
	os.Setenv("LOG_FORMAT", "console")
	defer os.Unsetenv("LOG_FORMAT")

	logger := New()
	if logger == nil {
		t.Fatal("expected non-nil logger")
	}

	logger.Info("test console message")
}

func TestNewLogger_DebugLevel(t *testing.T) {
	os.Setenv("LOG_LEVEL", "debug")
	defer os.Unsetenv("LOG_LEVEL")

	logger := New()
	if logger == nil {
		t.Fatal("expected non-nil logger")
	}

	// Debug should be enabled
	if !logger.Core().Enabled(zapcore.DebugLevel) {
		t.Error("expected debug level to be enabled")
	}
}

func TestNewLogger_WarnLevel(t *testing.T) {
	os.Setenv("LOG_LEVEL", "warn")
	defer os.Unsetenv("LOG_LEVEL")

	logger := New()
	if logger == nil {
		t.Fatal("expected non-nil logger")
	}

	// Debug and Info should be disabled
	if logger.Core().Enabled(zapcore.DebugLevel) {
		t.Error("expected debug level to be disabled")
	}
	if logger.Core().Enabled(zapcore.InfoLevel) {
		t.Error("expected info level to be disabled")
	}
	// Warn should be enabled
	if !logger.Core().Enabled(zapcore.WarnLevel) {
		t.Error("expected warn level to be enabled")
	}
}

func TestParseLevel(t *testing.T) {
	tests := []struct {
		input    string
		expected zapcore.Level
	}{
		{"debug", zapcore.DebugLevel},
		{"DEBUG", zapcore.DebugLevel},
		{"info", zapcore.InfoLevel},
		{"warn", zapcore.WarnLevel},
		{"error", zapcore.ErrorLevel},
		{"invalid", zapcore.InfoLevel}, // Default
		{"", zapcore.InfoLevel},        // Default
	}

	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := parseLevel(tt.input)
			if got != tt.expected {
				t.Errorf("parseLevel(%q) = %v, want %v", tt.input, got, tt.expected)
			}
		})
	}
}

func TestWithComponent_UsesComponentLevel(t *testing.T) {
	os.Setenv("LOG_LEVEL", "info")
	os.Setenv("POLLER_LOG_LEVEL", "debug")
	defer os.Unsetenv("LOG_LEVEL")
	defer os.Unsetenv("POLLER_LOG_LEVEL")

	logger := WithComponent("poller")

	// Debug should be enabled due to POLLER_LOG_LEVEL
	if !logger.Core().Enabled(zapcore.DebugLevel) {
		t.Error("expected debug level enabled for poller component")
	}
}

func TestWithComponent_FallsBackToGlobalLevel(t *testing.T) {
	os.Setenv("LOG_LEVEL", "warn")
	os.Unsetenv("POLLER_LOG_LEVEL")
	defer os.Unsetenv("LOG_LEVEL")

	logger := WithComponent("poller")

	// Info should be disabled, falling back to global warn level
	if logger.Core().Enabled(zapcore.InfoLevel) {
		t.Error("expected info level disabled when falling back to global warn")
	}
	if !logger.Core().Enabled(zapcore.WarnLevel) {
		t.Error("expected warn level enabled from global LOG_LEVEL")
	}
}

func TestWithComponent_EmptyComponentReturnsDefaultLogger(t *testing.T) {
	os.Setenv("LOG_LEVEL", "info")
	defer os.Unsetenv("LOG_LEVEL")

	logger := WithComponent("")
	if logger == nil {
		t.Fatal("expected non-nil logger for empty component")
	}

	// Should function as default logger
	logger.Info("test message with empty component")
}

func TestWithComponent_HandlesHyphensInComponentName(t *testing.T) {
	os.Setenv("API_SERVER_LOG_LEVEL", "debug")
	defer os.Unsetenv("API_SERVER_LOG_LEVEL")

	// Component "api-server" should map to "API_SERVER_LOG_LEVEL"
	logger := WithComponent("api-server")

	if !logger.Core().Enabled(zapcore.DebugLevel) {
		t.Error("expected debug level for api-server component (hyphen converted to underscore)")
	}
}

func TestWithComponent_AddsComponentField(t *testing.T) {
	os.Unsetenv("POLLER_LOG_LEVEL")
	defer os.Unsetenv("LOG_LEVEL")

	logger := WithComponent("poller")

	// At minimum, verify it doesn't panic when logging
	logger.Info("test message")
}

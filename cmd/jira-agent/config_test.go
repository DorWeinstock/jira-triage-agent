package main

import (
	"os"
	"testing"
)

func TestLoadConfig_LogFields(t *testing.T) {
	// Set environment variables
	os.Setenv("LOG_FORMAT", "json")
	os.Setenv("LOG_LEVEL", "debug")
	defer os.Unsetenv("LOG_FORMAT")
	defer os.Unsetenv("LOG_LEVEL")

	cfg := LoadConfig()

	if cfg.LogFormat != "json" {
		t.Errorf("LogFormat = %q, want %q", cfg.LogFormat, "json")
	}
	if cfg.LogLevel != "debug" {
		t.Errorf("LogLevel = %q, want %q", cfg.LogLevel, "debug")
	}
}

func TestLoadConfig_LogFieldsDefaults(t *testing.T) {
	// Ensure env vars are not set
	os.Unsetenv("LOG_FORMAT")
	os.Unsetenv("LOG_LEVEL")

	cfg := LoadConfig()

	if cfg.LogFormat != "console" {
		t.Errorf("LogFormat default = %q, want %q", cfg.LogFormat, "console")
	}
	if cfg.LogLevel != "info" {
		t.Errorf("LogLevel default = %q, want %q", cfg.LogLevel, "info")
	}
}

func TestParseIntOrDefault(t *testing.T) {
	tests := []struct {
		input    string
		fallback int
		want     int
	}{
		{"5", 10, 5},
		{"", 10, 10},
		{"invalid", 10, 10},
		{"-1", 10, 10},
		{"0", 10, 10},
	}
	for _, tt := range tests {
		got := parseIntOrDefault(tt.input, tt.fallback)
		if got != tt.want {
			t.Errorf("parseIntOrDefault(%q, %d) = %d, want %d", tt.input, tt.fallback, got, tt.want)
		}
	}
}

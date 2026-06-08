// Package logger provides structured logging using Uber's zap library.
// Configuration is via environment variables:
//   - LOG_FORMAT: "json" (default) or "console"
//   - LOG_LEVEL: "debug", "info" (default), "warn", "error", "fatal", "panic"
//   - {COMPONENT}_LOG_LEVEL: Per-component log level override (e.g., POLLER_LOG_LEVEL=warn)
//
// Usage:
//
//	log := logger.New()
//	defer log.Sync() // Flush any buffered log entries
//	pollerLog := logger.WithComponent("poller") // Checks POLLER_LOG_LEVEL first
package logger

import (
	"fmt"
	"os"
	"strings"

	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
)

// newWithLevel creates a logger with the specified level.
// Single source of truth for logger configuration.
func newWithLevel(level zapcore.Level) *zap.Logger {
	format := strings.ToLower(os.Getenv("LOG_FORMAT"))

	var config zap.Config
	if format == "console" {
		config = zap.NewDevelopmentConfig()
	} else {
		config = zap.NewProductionConfig()
	}

	config.Level = zap.NewAtomicLevelAt(level)
	// Use ISO8601 timestamps for human readability
	config.EncoderConfig.EncodeTime = zapcore.ISO8601TimeEncoder

	logger, err := config.Build()
	if err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: failed to initialize logger: %v\n", err)
		return zap.NewNop()
	}
	return logger
}

// New creates a new zap.Logger configured via environment variables.
// LOG_FORMAT controls output format: "json" (default) or "console".
// LOG_LEVEL controls minimum level: "debug", "info" (default), "warn", "error".
func New() *zap.Logger {
	return newWithLevel(parseLevel(os.Getenv("LOG_LEVEL")))
}

// WithComponent creates a logger for a specific component with optional level override.
// Checks {COMPONENT}_LOG_LEVEL env var first (e.g., POLLER_LOG_LEVEL), falls back to LOG_LEVEL.
// Hyphens in component names are converted to underscores for env var lookup.
// The component name is added to log entries via the "component" field.
func WithComponent(component string) *zap.Logger {
	if component == "" {
		return New()
	}

	// Normalize: hyphens → underscores for env var lookup
	envKey := strings.ToUpper(strings.ReplaceAll(component, "-", "_")) + "_LOG_LEVEL"
	levelStr := os.Getenv(envKey)
	if levelStr == "" {
		levelStr = os.Getenv("LOG_LEVEL")
	}

	logger := newWithLevel(parseLevel(levelStr))
	return logger.With(zap.String("component", component))
}

// parseLevel converts a string log level to zapcore.Level.
// Returns InfoLevel for unrecognized values.
func parseLevel(s string) zapcore.Level {
	switch strings.ToLower(s) {
	case "debug":
		return zapcore.DebugLevel
	case "info":
		return zapcore.InfoLevel
	case "warn", "warning":
		return zapcore.WarnLevel
	case "error":
		return zapcore.ErrorLevel
	case "fatal":
		return zapcore.FatalLevel
	case "panic":
		return zapcore.PanicLevel
	default:
		return zapcore.InfoLevel
	}
}

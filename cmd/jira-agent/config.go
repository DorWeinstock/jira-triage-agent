package main

import (
	"errors"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	// Server
	Port string

	// Jira (self-hosted — PAT via Bearer token auth)
	JiraURL string
	JiraPAT string

	// Filters
	FilterProject   string
	FilterComponent string
	FilterIssueType string

	// Triage
	TeamMembers    []string
	ProcessedLabel string

	// Polling
	PollingInterval         time.Duration
	MaxConcurrentDispatches int

	// Agent
	AgentURL string

	// Logging
	LogFormat string
	LogLevel  string
}

func (c *Config) Validate() error {
	if c.JiraURL == "" {
		return errors.New("missing Jira URL")
	}
	if c.JiraPAT == "" {
		return errors.New("missing Jira PAT (JIRA_PAT)")
	}
	if len(c.TeamMembers) == 0 {
		return errors.New("missing team members (TEAM_MEMBERS)")
	}
	if c.PollingInterval <= 0 {
		return errors.New("polling interval must be positive")
	}
	if c.MaxConcurrentDispatches <= 0 {
		return errors.New("max concurrent dispatches must be positive")
	}
	return nil
}

func LoadConfig() *Config {
	return &Config{
		Port:                    getenv("PORT", "8080"),
		JiraURL:                 getenv("JIRA_URL", ""),
		JiraPAT:                 getenv("JIRA_PAT", ""),
		FilterProject:           getenv("FILTER_PROJECT", "GAUDISW"),
		FilterComponent:         getenv("FILTER_COMPONENT", "DevOps_K8S"),
		FilterIssueType:         getenv("FILTER_ISSUE_TYPE", ""),
		TeamMembers:             parseCSV(getenv("TEAM_MEMBERS", "dweinsto,davidtal,gennadyd")),
		ProcessedLabel:          getenv("PROCESSED_LABEL", "triage-agent-done"),
		PollingInterval:         parseDurationOrDefault(getenv("POLLING_INTERVAL", "5m"), 5*time.Minute),
		MaxConcurrentDispatches: parseIntOrDefault(getenv("MAX_CONCURRENT_DISPATCHES", "5"), 5),
		AgentURL:                getenv("AGENT_URL", "http://langgraph-agent:8000"),
		LogFormat:               getenv("LOG_FORMAT", "console"),
		LogLevel:                getenv("LOG_LEVEL", "info"),
	}
}

func getenv(key, defaultValue string) string {
	if value, ok := os.LookupEnv(key); ok {
		return value
	}
	return defaultValue
}

func parseCSV(s string) []string {
	var result []string
	for _, part := range strings.Split(s, ",") {
		if trimmed := strings.TrimSpace(part); trimmed != "" {
			result = append(result, trimmed)
		}
	}
	return result
}

func parseDurationOrDefault(s string, defaultVal time.Duration) time.Duration {
	d, err := time.ParseDuration(s)
	if err != nil {
		return defaultVal
	}
	return d
}

func parseIntOrDefault(s string, defaultVal int) int {
	if s == "" {
		return defaultVal
	}
	v, err := strconv.Atoi(s)
	if err != nil || v <= 0 {
		return defaultVal
	}
	return v
}

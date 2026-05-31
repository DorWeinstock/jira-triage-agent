package main

import (
	"fmt"
	"os"
	"strings"
	"time"
)

type Config struct {
	JiraURL        string
	JiraEmail      string
	JiraToken      string
	JiraProject    string
	ProcessedLabel string
	TeamMembers    []string
	PollInterval   time.Duration
	LLMEndpoint string
	LLMModel    string
}

func loadConfig() (Config, error) {
	pollInterval, err := time.ParseDuration(getEnv("POLL_INTERVAL", "5m"))
	if err != nil {
		return Config{}, fmt.Errorf("invalid POLL_INTERVAL: %w", err)
	}

	members := strings.Split(os.Getenv("TEAM_MEMBERS"), ",")
	for i := range members {
		members[i] = strings.TrimSpace(members[i])
	}
	if len(members) == 0 || (len(members) == 1 && members[0] == "") {
		return Config{}, fmt.Errorf("TEAM_MEMBERS must be a non-empty comma-separated list of Jira account IDs")
	}

	cfg := Config{
		JiraURL:        requireEnv("JIRA_URL"),
		JiraEmail:      requireEnv("JIRA_EMAIL"),
		JiraToken:      requireEnv("JIRA_TOKEN"),
		JiraProject:    getEnv("JIRA_PROJECT", "GAUDISW"),
		ProcessedLabel: getEnv("PROCESSED_LABEL", "triage-agent-done"),
		TeamMembers:    members,
		PollInterval:   pollInterval,
		LLMEndpoint:    requireEnv("LLM_ENDPOINT"),
		LLMModel:       getEnv("LLM_MODEL", "Qwen/Qwen3-Next-80B-A3B-Instruct-FP8"),
	}

	return cfg, nil
}

func requireEnv(key string) string {
	v := os.Getenv(key)
	if v == "" {
		fmt.Fprintf(os.Stderr, "required environment variable %s is not set\n", key)
		os.Exit(1)
	}
	return v
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

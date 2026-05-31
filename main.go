package main

import (
	"log"
	"time"
)

func main() {
	cfg, err := loadConfig()
	if err != nil {
		log.Fatalf("Config error: %v", err)
	}

	jiraClient, err := newJiraClient(cfg.JiraURL, cfg.JiraEmail, cfg.JiraToken)
	if err != nil {
		log.Fatalf("Jira client error: %v", err)
	}

	evaluator := newEvaluator(cfg.LLMEndpoint, cfg.LLMModel)
	agent := newAgent(cfg, jiraClient, evaluator)

	log.Printf("Triage agent started — project=%s poll=%s team=%v model=%s",
		cfg.JiraProject, cfg.PollInterval, cfg.TeamMembers, cfg.LLMModel)

	for {
		if err := agent.run(); err != nil {
			log.Printf("Run error: %v", err)
		}
		time.Sleep(cfg.PollInterval)
	}
}

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
)

type Verdict string

const (
	VerdictSpam  Verdict = "spam"
	VerdictValid Verdict = "valid"
)

// EvalResult is the structured response from the LLM.
type EvalResult struct {
	Verdict          Verdict `json:"verdict"`
	Comment          string  `json:"comment"`
	JenkinsLinkFound bool    `json:"jenkins_link_found"`
	ServerNameFound  bool    `json:"server_name_found"`
	Scope            string  `json:"scope"` // "k8s" | "out_of_scope"
}

var responseSchema = map[string]any{
	"type": "json_schema",
	"json_schema": map[string]any{
		"name": "triage_result",
		"schema": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"verdict":            map[string]any{"type": "string", "enum": []string{"spam", "valid"}},
				"comment":            map[string]any{"type": "string"},
				"jenkins_link_found": map[string]any{"type": "boolean"},
				"server_name_found":  map[string]any{"type": "boolean"},
				"scope":              map[string]any{"type": "string", "enum": []string{"k8s", "out_of_scope"}},
			},
			"required": []string{"verdict", "comment", "jenkins_link_found", "server_name_found", "scope"},
		},
	},
}

var prompt = `You are a triage agent for a DevOps/Kubernetes team. You review Jira tickets created by an AI QA agent to decide if they are actionable by the team.

Your team handles Kubernetes-level issues only. You are NOT responsible for:
- Hardware failures or degradation
- Firmware issues
- Jenkins or CI pipeline failures
- IT infrastructure issues
- Kernel issues
- Network issues that are not Kubernetes-related (e.g. physical NIC failures, switch issues)

Evaluate the ticket by following these steps in order:

STEP 1 — Jenkins link:
Does the ticket contain a Jenkins job URL or link? Without it the team cannot reproduce or debug the issue.
Set jenkins_link_found accordingly.

STEP 2 — Server name:
Can a server name or hostname be identified? First try to extract it from the Jenkins URL (e.g. a node name in the URL path). If not there, look in the ticket body.
Set server_name_found accordingly.

STEP 3 — Scope:
Based on the error description and context, does this look like a Kubernetes issue, or does it appear to be caused by hardware, firmware, Jenkins, IT, kernel, or non-Kubernetes network problems?
Set scope to "k8s" or "out_of_scope".

VERDICT rules (apply in this order):
1. If jenkins_link_found is false → verdict is "spam"
2. If server_name_found is false → verdict is "spam"
3. If scope is "out_of_scope" → verdict is "spam"
4. Otherwise → verdict is "valid"

COMMENT:
For spam tickets, write a short, polite comment addressed to the reporter explaining specifically why the ticket is being returned. Be concrete — name what is missing or why it falls outside the team's scope. Do NOT use the word "spam". Do NOT be dismissive.
For valid tickets, leave comment empty.`

type Evaluator struct {
	endpoint string
	model    string
	http     *http.Client
}

func newEvaluator(endpoint, model string) *Evaluator {
	return &Evaluator{
		endpoint: strings.TrimRight(endpoint, "/"),
		model:    model,
		http:     &http.Client{},
	}
}

func (e *Evaluator) Evaluate(ctx context.Context, summary, description string) (EvalResult, error) {
	userMsg := fmt.Sprintf("Ticket title: %s\n\nTicket description:\n%s\n\n/no_think", summary, description)

	body, err := json.Marshal(map[string]any{
		"model":           e.model,
		"max_tokens":      512,
		"temperature":     0.1,
		"response_format": responseSchema,
		"messages": []map[string]string{
			{"role": "system", "content": prompt},
			{"role": "user", "content": userMsg},
		},
	})
	if err != nil {
		return EvalResult{}, fmt.Errorf("marshalling request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, e.endpoint+"/v1/chat/completions", bytes.NewReader(body))
	if err != nil {
		return EvalResult{}, fmt.Errorf("building request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := e.http.Do(req)
	if err != nil {
		return EvalResult{}, fmt.Errorf("vllm request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return EvalResult{}, fmt.Errorf("vllm returned status %d", resp.StatusCode)
	}

	var raw struct {
		Choices []struct {
			Message struct {
				Content string `json:"content"`
			} `json:"message"`
		} `json:"choices"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&raw); err != nil {
		return EvalResult{}, fmt.Errorf("decoding vllm response: %w", err)
	}
	if len(raw.Choices) == 0 {
		return EvalResult{}, fmt.Errorf("empty choices in vllm response")
	}

	var result EvalResult
	if err := json.Unmarshal([]byte(raw.Choices[0].Message.Content), &result); err != nil {
		return EvalResult{}, fmt.Errorf("parsing structured result: %w", err)
	}

	return result, nil
}

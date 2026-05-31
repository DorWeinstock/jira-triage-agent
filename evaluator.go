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

// Evaluate asks the LLM whether an AI-generated Jira ticket is spam.
// Uses the OpenAI-compatible /v1/chat/completions endpoint (vLLM).
// /no_think disables Qwen3's chain-of-thought mode for fast inference.
func (e *Evaluator) Evaluate(ctx context.Context, summary, description string) (Verdict, string, error) {
	prompt := fmt.Sprintf(`You are reviewing a Jira ticket created by an AI QA agent without human validation.
Decide if the ticket is spam (not actionable) or valid (worth a developer's time).

Spam: vague title, no reproduction steps, no error messages, template-only content, too generic to investigate.
Valid: specific failure, enough context to investigate, references components or error messages.

Ticket title: %s

Ticket description:
%s

Reply with exactly one word on the first line — either "spam" or "valid" — then one sentence explaining why. /no_think`, summary, description)

	body, err := json.Marshal(map[string]any{
		"model":       e.model,
		"max_tokens":  100,
		"temperature": 0.1,
		"messages": []map[string]string{
			{"role": "user", "content": prompt},
		},
	})
	if err != nil {
		return "", "", fmt.Errorf("marshalling request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, e.endpoint+"/v1/chat/completions", bytes.NewReader(body))
	if err != nil {
		return "", "", fmt.Errorf("building request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := e.http.Do(req)
	if err != nil {
		return "", "", fmt.Errorf("vllm request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", "", fmt.Errorf("vllm returned status %d", resp.StatusCode)
	}

	var result struct {
		Choices []struct {
			Message struct {
				Content string `json:"content"`
			} `json:"message"`
		} `json:"choices"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", "", fmt.Errorf("decoding vllm response: %w", err)
	}
	if len(result.Choices) == 0 {
		return "", "", fmt.Errorf("empty choices in vllm response")
	}

	raw := strings.TrimSpace(result.Choices[0].Message.Content)
	lines := strings.SplitN(raw, "\n", 2)

	verdict := Verdict(strings.ToLower(strings.TrimSpace(lines[0])))
	if verdict != VerdictSpam && verdict != VerdictValid {
		return "", "", fmt.Errorf("unexpected verdict from model: %q", lines[0])
	}

	reason := ""
	if len(lines) > 1 {
		reason = strings.TrimSpace(lines[1])
	}

	return verdict, reason, nil
}

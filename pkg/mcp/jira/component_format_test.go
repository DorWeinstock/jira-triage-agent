package jira

import (
	"strings"
	"testing"
)

func TestFormatTicketOutput_WithComponents(t *testing.T) {
	ticket := &Ticket{
		Key: "SP-100",
		Fields: TicketFields{
			Summary:     "order-service CrashLoopBackOff",
			Description: "Pods are crashing",
			Components:  []Component{{Name: "order-service"}, {Name: "payments"}},
		},
	}

	output := formatTicketOutput(ticket)

	if !strings.Contains(output, "Components: order-service, payments") {
		t.Errorf("expected Components line in output, got:\n%s", output)
	}
}

func TestFormatTicketOutput_NoComponents(t *testing.T) {
	ticket := &Ticket{
		Key: "SP-101",
		Fields: TicketFields{
			Summary:     "generic issue",
			Description: "something happened",
			Components:  nil,
		},
	}

	output := formatTicketOutput(ticket)

	// Should NOT contain Components line when empty
	if strings.Contains(output, "Components:") {
		t.Errorf("should not contain Components line for empty components, got:\n%s", output)
	}
}

func TestFormatSearchResults_WithComponents(t *testing.T) {
	tickets := []Ticket{
		{
			Key: "SP-200",
			Fields: TicketFields{
				Summary:    "memory issue",
				Updated:    "2026-01-15",
				Components: []Component{{Name: "api-gateway"}},
			},
		},
		{
			Key: "SP-201",
			Fields: TicketFields{
				Summary:    "disk full",
				Updated:    "2026-01-10",
				Components: nil,
			},
		},
	}

	result := formatSearchResults(tickets)

	// SP-200 should have component tag
	if !strings.Contains(result, "{api-gateway}") {
		t.Errorf("expected {api-gateway} component tag in search results, got:\n%s", result)
	}
	// SP-201 should NOT have component tag
	lines := strings.Split(result, "\n")
	for _, line := range lines {
		if strings.Contains(line, "SP-201") && strings.Contains(line, "{") {
			t.Errorf("SP-201 should not have component tag, got: %s", line)
		}
	}
}

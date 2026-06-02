package main

import (
	"context"
	"errors"
	"slices"
	"testing"

	jira "github.com/andygrunwald/go-jira/v2/cloud"
)

// ── fakes ────────────────────────────────────────────────────────────────────

type fakeJira struct {
	tickets    []jira.Issue
	reassigned map[string]string   // issueKey → accountID
	comments   map[string]string   // issueKey → comment body
	labels     map[string][]string // issueKey → labels added
	findErr    error
	reassignErr error
}

func newFakeJira(tickets ...jira.Issue) *fakeJira {
	return &fakeJira{
		tickets:    tickets,
		reassigned: map[string]string{},
		comments:   map[string]string{},
		labels:     map[string][]string{},
	}
}

func (f *fakeJira) findIncomingTickets(_ string, _ []string, _ string) ([]jira.Issue, error) {
	return f.tickets, f.findErr
}

func (f *fakeJira) reassign(issueKey, accountID string) error {
	if f.reassignErr != nil {
		return f.reassignErr
	}
	f.reassigned[issueKey] = accountID
	return nil
}

func (f *fakeJira) addComment(issueKey, body string) error {
	f.comments[issueKey] = body
	return nil
}

func (f *fakeJira) addLabel(issue *jira.Issue, label string) error {
	f.labels[issue.Key] = append(f.labels[issue.Key], label)
	return nil
}

type fakeEvaluator struct {
	result EvalResult
	err    error
}

func (f *fakeEvaluator) Evaluate(_ context.Context, _, _ string) (EvalResult, error) {
	return f.result, f.err
}

// ── helpers ──────────────────────────────────────────────────────────────────

func makeIssue(key string, labels []string, description string, reporter *jira.User) jira.Issue {
	return jira.Issue{
		Key: key,
		Fields: &jira.IssueFields{
			Summary:     "Test issue " + key,
			Description: description,
			Labels:      labels,
			Reporter:    reporter,
		},
	}
}

func aiDescription() string {
	return "Some details. " + aiBodyMarker + " More text."
}

func testAgent(j jiraFacade, e evaluatorFacade, members ...string) *Agent {
	if len(members) == 0 {
		members = []string{"alice", "bob", "carol"}
	}
	return &Agent{
		cfg: Config{
			TeamMembers:    members,
			ProcessedLabel: "triage-agent-done",
			JiraProject:    "TEST",
		},
		jira:      j,
		evaluator: e,
	}
}

// ── isAIGenerated ─────────────────────────────────────────────────────────────

func TestIsAIGenerated(t *testing.T) {
	cases := []struct {
		name   string
		labels []string
		desc   string
		want   bool
	}{
		{"both signals", []string{aiGeneratedLabel}, aiDescription(), true},
		{"label only", []string{aiGeneratedLabel}, "no marker here", false},
		{"marker only", []string{"other"}, aiDescription(), false},
		{"neither", []string{"other"}, "plain description", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			issue := makeIssue("T-1", tc.labels, tc.desc, nil)
			if got := isAIGenerated(&issue); got != tc.want {
				t.Errorf("isAIGenerated = %v, want %v", got, tc.want)
			}
		})
	}
}

// ── hasLabel ──────────────────────────────────────────────────────────────────

func TestHasLabel(t *testing.T) {
	issue := makeIssue("T-1", []string{"foo", "bar"}, "", nil)
	if !hasLabel(&issue, "foo") {
		t.Error("expected hasLabel to find 'foo'")
	}
	if hasLabel(&issue, "baz") {
		t.Error("expected hasLabel to not find 'baz'")
	}
}

// ── nextMember ────────────────────────────────────────────────────────────────

func TestNextMember_RoundRobin(t *testing.T) {
	members := []string{"alice", "bob", "carol"}
	a := testAgent(newFakeJira(), &fakeEvaluator{}, members...)

	got := make([]string, 9)
	for i := range got {
		got[i] = a.nextMember()
	}
	want := []string{"alice", "bob", "carol", "alice", "bob", "carol", "alice", "bob", "carol"}
	for i, w := range want {
		if got[i] != w {
			t.Errorf("call %d: got %q, want %q", i, got[i], w)
		}
	}
}

// ── process ───────────────────────────────────────────────────────────────────

func TestProcess_NotAIGenerated_RoutesToTeam(t *testing.T) {
	j := newFakeJira()
	e := &fakeEvaluator{err: errors.New("should not be called")}
	a := testAgent(j, e)

	issue := makeIssue("T-1", []string{"some-label"}, "no marker", nil)
	if err := a.process(context.Background(), &issue); err != nil {
		t.Fatal(err)
	}

	if _, called := j.reassigned["T-1"]; !called {
		t.Error("expected ticket to be reassigned to a team member")
	}
	if _, commented := j.comments["T-1"]; commented {
		t.Error("expected no comment on a valid (non-AI-generated) ticket")
	}
}

func TestProcess_AIGenerated_Valid_RoutesToTeam(t *testing.T) {
	j := newFakeJira()
	e := &fakeEvaluator{result: EvalResult{Verdict: VerdictValid}}
	a := testAgent(j, e)

	reporter := &jira.User{AccountID: "reporter-id"}
	issue := makeIssue("T-2", []string{aiGeneratedLabel}, aiDescription(), reporter)
	if err := a.process(context.Background(), &issue); err != nil {
		t.Fatal(err)
	}

	assignee := j.reassigned["T-2"]
	if assignee == "reporter-id" {
		t.Error("valid ticket should not be reassigned to reporter")
	}
	if assignee == "" {
		t.Error("valid ticket should be assigned to a team member")
	}
}

func TestProcess_AIGenerated_Spam_ReassignsToReporter(t *testing.T) {
	cases := []struct {
		name   string
		result EvalResult
	}{
		{
			"no jenkins link",
			EvalResult{Verdict: VerdictSpam, Comment: "Missing Jenkins link.", JenkinsLinkFound: false, ServerNameFound: false, Scope: "k8s"},
		},
		{
			"no server name",
			EvalResult{Verdict: VerdictSpam, Comment: "Cannot identify server.", JenkinsLinkFound: true, ServerNameFound: false, Scope: "k8s"},
		},
		{
			"out of scope",
			EvalResult{Verdict: VerdictSpam, Comment: "This looks like a hardware issue.", JenkinsLinkFound: true, ServerNameFound: true, Scope: "out_of_scope"},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			j := newFakeJira()
			e := &fakeEvaluator{result: tc.result}
			a := testAgent(j, e)

			reporter := &jira.User{AccountID: "reporter-id"}
			issue := makeIssue("T-3", []string{aiGeneratedLabel}, aiDescription(), reporter)
			if err := a.process(context.Background(), &issue); err != nil {
				t.Fatal(err)
			}

			if j.reassigned["T-3"] != "reporter-id" {
				t.Errorf("expected reassign to reporter, got %q", j.reassigned["T-3"])
			}
			if j.comments["T-3"] != tc.result.Comment {
				t.Errorf("expected comment %q, got %q", tc.result.Comment, j.comments["T-3"])
			}
			if !containsLabel(j.labels["T-3"], "triage-agent-done") {
				t.Error("expected processed label to be stamped")
			}
		})
	}
}

func TestProcess_Spam_NoReporter_StampsLabelOnly(t *testing.T) {
	j := newFakeJira()
	e := &fakeEvaluator{result: EvalResult{Verdict: VerdictSpam, Comment: "Missing Jenkins link."}}
	a := testAgent(j, e)

	issue := makeIssue("T-4", []string{aiGeneratedLabel}, aiDescription(), nil) // no reporter
	if err := a.process(context.Background(), &issue); err != nil {
		t.Fatal(err)
	}

	if _, ok := j.reassigned["T-4"]; ok {
		t.Error("should not reassign when reporter is nil")
	}
	if !containsLabel(j.labels["T-4"], "triage-agent-done") {
		t.Error("expected processed label to be stamped even without reporter")
	}
}

// ── run ───────────────────────────────────────────────────────────────────────

func TestRun_EvalErrorOnOneTicket_ContinuesOthers(t *testing.T) {
	reporter := &jira.User{AccountID: "reporter-id"}
	tickets := []jira.Issue{
		makeIssue("T-1", []string{aiGeneratedLabel}, aiDescription(), reporter),
		makeIssue("T-2", []string{aiGeneratedLabel}, aiDescription(), reporter),
	}
	j := newFakeJira(tickets...)

	callCount := 0
	e := &callCountEvaluator{
		results: []EvalResult{
			{Verdict: VerdictValid},
		},
		errs: []error{
			nil,
			errors.New("LLM unavailable"),
		},
		count: &callCount,
	}

	a := testAgent(j, e)
	if err := a.run(); err != nil {
		t.Fatal(err)
	}

	// T-1 succeeded → assigned to team; T-2 errored → no action but no panic
	if _, ok := j.reassigned["T-1"]; !ok {
		t.Error("T-1 should have been processed successfully")
	}
}

// callCountEvaluator returns results/errs in sequence.
type callCountEvaluator struct {
	results []EvalResult
	errs    []error
	count   *int
}

func (c *callCountEvaluator) Evaluate(_ context.Context, _, _ string) (EvalResult, error) {
	i := *c.count
	*c.count++
	if i < len(c.results) {
		return c.results[i], c.errs[i]
	}
	return EvalResult{}, errors.New("unexpected call")
}

// ── helpers ───────────────────────────────────────────────────────────────────

func containsLabel(labels []string, label string) bool {
	return slices.Contains(labels, label)
}

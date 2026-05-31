package main

import (
	"context"
	"fmt"
	"net/http"
	"strings"

	jira "github.com/andygrunwald/go-jira/v2/cloud"
)

type JiraClient struct {
	client *jira.Client
}

func newJiraClient(url, email, token string) (*JiraClient, error) {
	tp := jira.BasicAuthTransport{
		Username: email,
		APIToken: token,
	}
	client, err := jira.NewClient(url, &http.Client{Transport: &tp})
	if err != nil {
		return nil, fmt.Errorf("creating jira client: %w", err)
	}
	return &JiraClient{client: client}, nil
}

// findIncomingTickets returns unprocessed ai-generated tickets assigned to any of the given team members.
func (j *JiraClient) findIncomingTickets(project string, teamMembers []string, processedLabel string) ([]jira.Issue, error) {
	// Build: assignee in ("id1","id2",...)
	quoted := make([]string, len(teamMembers))
	for i, id := range teamMembers {
		quoted[i] = `"` + id + `"`
	}
	assigneeClause := "assignee in (" + strings.Join(quoted, ",") + ")"

	jql := fmt.Sprintf(
		`project = "%s" AND issuetype in ("Bug", "Task") AND component = "DevOps_K8S" AND %s AND labels = "ai-generated" AND labels != "%s" ORDER BY created ASC`,
		project, assigneeClause, processedLabel,
	)

	var all []jira.Issue
	opts := &jira.SearchOptions{
		StartAt:    0,
		MaxResults: 50,
		Fields:     []string{"summary", "description", "labels", "reporter", "assignee", "status"},
	}

	for {
		issues, resp, err := j.client.Issue.Search(context.Background(), jql, opts)
		if err != nil {
			return nil, fmt.Errorf("searching issues: %w", err)
		}
		all = append(all, issues...)
		if resp.StartAt+len(issues) >= resp.Total {
			break
		}
		opts.StartAt += len(issues)
	}

	return all, nil
}

func (j *JiraClient) reassign(issueKey, accountID string) error {
	_, err := j.client.Issue.UpdateAssignee(context.Background(), issueKey, &jira.User{AccountID: accountID})
	if err != nil {
		return fmt.Errorf("reassigning %s to %s: %w", issueKey, accountID, err)
	}
	return nil
}

func (j *JiraClient) addComment(issueKey, body string) error {
	_, _, err := j.client.Issue.AddComment(context.Background(), issueKey, &jira.Comment{Body: body})
	if err != nil {
		return fmt.Errorf("adding comment to %s: %w", issueKey, err)
	}
	return nil
}

// addLabel appends a label to an issue without removing existing labels.
func (j *JiraClient) addLabel(issue *jira.Issue, label string) error {
	updated := *issue
	fields := *issue.Fields
	fields.Labels = append(append([]string{}, issue.Fields.Labels...), label)
	updated.Fields = &fields

	_, _, err := j.client.Issue.Update(context.Background(), &updated, nil)
	if err != nil {
		return fmt.Errorf("adding label %q to %s: %w", label, issue.Key, err)
	}
	return nil
}

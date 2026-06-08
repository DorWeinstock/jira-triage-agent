package jenkins

import (
	"context"
	"fmt"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const defaultMaxConsoleBytes = 100000

// NewMCPServer creates an MCP server with Jenkins tools registered.
func NewMCPServer(client *Client) *mcp.Server {
	server := mcp.NewServer(
		&mcp.Implementation{
			Name:    "jenkins-mcp",
			Version: "1.0.0",
		},
		nil,
	)

	registerGetBuildInfoTool(server, client)
	registerGetConsoleLogTool(server, client)
	registerGetParentBuildInfoTool(server, client)

	return server
}

// registerGetBuildInfoTool registers the jenkins_get_build_info tool.
func registerGetBuildInfoTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "jenkins_get_build_info",
			Description: "Get build metadata from a Jenkins build URL",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			JenkinsURL string `json:"jenkins_url" jsonschema:"The full Jenkins build URL"`
		}) (*mcp.CallToolResult, any, error) {
			if input.JenkinsURL == "" {
				return nil, nil, fmt.Errorf("jenkins_url is required")
			}

			parsed, err := ParseJenkinsURL(input.JenkinsURL)
			if err != nil {
				return nil, nil, fmt.Errorf("invalid Jenkins URL: %w", err)
			}

			info, err := client.GetBuildInfo(ctx, parsed)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to get build info: %w", err)
			}

			output := formatBuildInfo(info)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// registerGetConsoleLogTool registers the jenkins_get_console_log tool.
func registerGetConsoleLogTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "jenkins_get_console_log",
			Description: "Get console log output from a Jenkins build URL",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			JenkinsURL string `json:"jenkins_url" jsonschema:"The full Jenkins build URL"`
			MaxBytes   *int64 `json:"max_bytes,omitempty" jsonschema:"Maximum bytes to return (default 100000)"`
		}) (*mcp.CallToolResult, any, error) {
			if input.JenkinsURL == "" {
				return nil, nil, fmt.Errorf("jenkins_url is required")
			}

			parsed, err := ParseJenkinsURL(input.JenkinsURL)
			if err != nil {
				return nil, nil, fmt.Errorf("invalid Jenkins URL: %w", err)
			}

			maxBytes := int64(defaultMaxConsoleBytes)
			if input.MaxBytes != nil {
				maxBytes = *input.MaxBytes
			}

			log, err := client.GetConsoleLog(ctx, parsed, maxBytes)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to get console log: %w", err)
			}

			wasTruncated := int64(len(log)) >= maxBytes
			output := formatConsoleLog(log, maxBytes, wasTruncated)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// registerGetParentBuildInfoTool registers the jenkins_get_parent_build_info tool.
func registerGetParentBuildInfoTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "jenkins_get_parent_build_info",
			Description: "Get upstream/parent trigger info for a Jenkins build URL",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			JenkinsURL string `json:"jenkins_url" jsonschema:"The full Jenkins build URL"`
		}) (*mcp.CallToolResult, any, error) {
			if input.JenkinsURL == "" {
				return nil, nil, fmt.Errorf("jenkins_url is required")
			}

			parsed, err := ParseJenkinsURL(input.JenkinsURL)
			if err != nil {
				return nil, nil, fmt.Errorf("invalid Jenkins URL: %w", err)
			}

			cause, err := client.GetUpstreamCause(ctx, parsed)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to get upstream cause: %w", err)
			}

			output := formatUpstreamCause(cause)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

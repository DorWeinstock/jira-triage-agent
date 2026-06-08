package jenkins

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Client interacts with the Jenkins REST API.
type Client struct {
	username   string
	apiToken   string
	httpClient *http.Client
}

// BuildInfo represents metadata about a Jenkins build.
type BuildInfo struct {
	Result      string        `json:"result"`
	Duration    int64         `json:"duration"`
	Timestamp   int64         `json:"timestamp"`
	DisplayName string        `json:"displayName"`
	BuiltOn     string        `json:"builtOn"`
	URL         string        `json:"url"`
	FullName    string        `json:"fullDisplayName"`
	Actions     []BuildAction `json:"actions"`
}

// BuildAction represents a Jenkins build action (causes, parameters, etc.)
type BuildAction struct {
	Class  string       `json:"_class,omitempty"`
	Causes []BuildCause `json:"causes,omitempty"`
}

// BuildCause represents what triggered a build.
type BuildCause struct {
	Class            string `json:"_class,omitempty"`
	ShortDescription string `json:"shortDescription,omitempty"`
	UpstreamProject  string `json:"upstreamProject,omitempty"`
	UpstreamBuild    int    `json:"upstreamBuild,omitempty"`
	UpstreamURL      string `json:"upstreamUrl,omitempty"`
}

// UpstreamCause holds parsed upstream trigger information.
type UpstreamCause struct {
	Project string
	Build   int
	URL     string
}

// ParsedURL holds components extracted from a Jenkins build URL.
type ParsedURL struct {
	BaseURL     string
	JobPath     string
	BuildNumber int
}

// NewClient creates a Jenkins API client.
// If username is empty, requests are made without authentication.
func NewClient(username, apiToken string) *Client {
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{InsecureSkipVerify: true},
	}
	transport.Proxy = http.ProxyFromEnvironment

	return &Client{
		username: username,
		apiToken: apiToken,
		httpClient: &http.Client{
			Timeout:   30 * time.Second,
			Transport: transport,
		},
	}
}

// setAuth adds Basic Auth to the request if credentials are configured.
func (c *Client) setAuth(req *http.Request) {
	if c.username != "" {
		req.SetBasicAuth(c.username, c.apiToken)
	}
}

// GetBuildInfo fetches build metadata from Jenkins.
func (c *Client) GetBuildInfo(ctx context.Context, parsed ParsedURL) (*BuildInfo, error) {
	apiURL := fmt.Sprintf("%s/job/%s/%d/api/json", parsed.BaseURL, parsed.JobPath, parsed.BuildNumber)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, apiURL, nil)
	if err != nil {
		return nil, fmt.Errorf("creating request: %w", err)
	}
	c.setAuth(req)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("executing request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("jenkins API error: status %d", resp.StatusCode)
	}

	var info BuildInfo
	if err := json.NewDecoder(resp.Body).Decode(&info); err != nil {
		return nil, fmt.Errorf("decoding response: %w", err)
	}
	return &info, nil
}

// GetConsoleLog fetches the console output for a build, truncated to the last maxBytes.
func (c *Client) GetConsoleLog(ctx context.Context, parsed ParsedURL, maxBytes int64) (string, error) {
	consoleURL := fmt.Sprintf("%s/job/%s/%d/consoleText", parsed.BaseURL, parsed.JobPath, parsed.BuildNumber)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, consoleURL, nil)
	if err != nil {
		return "", fmt.Errorf("creating request: %w", err)
	}
	c.setAuth(req)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("executing request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("jenkins API error: status %d", resp.StatusCode)
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", fmt.Errorf("reading response body: %w", err)
	}

	// Truncate to last maxBytes (tail)
	if int64(len(body)) > maxBytes {
		body = body[int64(len(body))-maxBytes:]
	}

	// Ensure valid UTF-8 after truncation (byte-slice cut may split multi-byte chars)
	return strings.ToValidUTF8(string(body), ""), nil
}

// GetUpstreamCause extracts upstream trigger information from a build.
// Returns nil if no upstream cause is found (not an error).
func (c *Client) GetUpstreamCause(ctx context.Context, parsed ParsedURL) (*UpstreamCause, error) {
	info, err := c.GetBuildInfo(ctx, parsed)
	if err != nil {
		return nil, fmt.Errorf("fetching build info: %w", err)
	}

	for _, action := range info.Actions {
		if action.Class != "hudson.model.CauseAction" {
			continue
		}
		for _, cause := range action.Causes {
			if strings.Contains(cause.Class, "UpstreamCause") {
				return &UpstreamCause{
					Project: cause.UpstreamProject,
					Build:   cause.UpstreamBuild,
					URL:     cause.UpstreamURL,
				}, nil
			}
		}
	}

	return nil, nil
}

// jenkinsURLRe matches Jenkins build URLs.
// Pattern: http(s)://host/job/path/.../buildNumber
var jenkinsURLRe = regexp.MustCompile(`^(https?://[^/]+)(/job/[^/]+(?:/job/[^/]+)*)/(\d+)/?$`)

// ParseJenkinsURL extracts components from a Jenkins build URL.
func ParseJenkinsURL(rawURL string) (ParsedURL, error) {
	if rawURL == "" {
		return ParsedURL{}, fmt.Errorf("empty URL")
	}

	matches := jenkinsURLRe.FindStringSubmatch(rawURL)
	if matches == nil {
		return ParsedURL{}, fmt.Errorf("invalid Jenkins URL: %q", rawURL)
	}

	baseURL := matches[1]
	// Strip leading /job/ to get the path
	jobPath := strings.TrimPrefix(matches[2], "/job/")
	buildNumber, err := strconv.Atoi(matches[3])
	if err != nil {
		return ParsedURL{}, fmt.Errorf("invalid build number: %w", err)
	}

	return ParsedURL{
		BaseURL:     baseURL,
		JobPath:     jobPath,
		BuildNumber: buildNumber,
	}, nil
}

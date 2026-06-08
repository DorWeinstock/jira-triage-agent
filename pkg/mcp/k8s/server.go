// Package k8s provides Model Context Protocol (MCP) server implementation
// for Kubernetes operations.
package k8s

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
)

const (
	defaultTailLines = 100
	maxTailLines     = 10000
	timeFormat       = "2006-01-02 15:04:05"
	maxManifestBytes = 1 * 1024 * 1024 // 1MB limit for YAML manifests
)

// NewMCPServer creates an MCP server with kubectl-compatible tools.
// The server provides tools for listing pods, getting logs, viewing events, and describing pods.
func NewMCPServer(client *Client) *mcp.Server {
	server := mcp.NewServer(
		&mcp.Implementation{
			Name:    "k8s-mcp",
			Version: "1.0.0",
		},
		nil,
	)

	registerReadTools(server, client)
	registerWriteTools(server, client)

	return server
}

// registerReadTools registers read-only kubectl tools for introspection.
func registerReadTools(server *mcp.Server, client *Client) {
	// Generic tools (Phase 1: coexist with legacy tools)
	registerGetTool(server, client)
	registerDescribeTool(server, client)

	// Legacy tools (kept for backward compatibility during Phase 1)
	registerGetPodsTool(server, client)
	registerGetPodsAllNamespacesTool(server, client)
	registerGetDeploymentsTool(server, client)
	registerGetDeploymentsAllNamespacesTool(server, client)
	registerGetServicesTool(server, client)
	registerGetServicesAllNamespacesTool(server, client)
	registerGetConfigMapsTool(server, client)
	registerGetSecretsTool(server, client)
	registerLogsTool(server, client)
	registerEventsTool(server, client)
	registerDescribePodTool(server, client)
}

// registerWriteTools registers write/modification kubectl tools.
// SECURITY NOTE: These tools perform write operations on the cluster.
// The service account needs appropriate RBAC permissions.
func registerWriteTools(server *mcp.Server, client *Client) {
	registerScaleTool(server, client)
	registerRolloutRestartTool(server, client)
	registerApplyTool(server, client)
	registerDeleteTool(server, client)
}

// registerGetPodsTool registers the kubectl_get_pods tool.
func registerGetPodsTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "kubectl_get_pods",
			Description: "List pods in a namespace with optional label selector",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			Namespace     string  `json:"namespace" jsonschema:"Kubernetes namespace to list pods from"`
			LabelSelector *string `json:"label_selector,omitempty" jsonschema:"Optional label selector to filter pods (e.g., app=myapp)"`
		}) (*mcp.CallToolResult, any, error) {
			if input.Namespace == "" {
				return nil, nil, fmt.Errorf("namespace is required")
			}

			labelSelector := ""
			if input.LabelSelector != nil {
				labelSelector = *input.LabelSelector
			}

			pods, err := client.GetPods(ctx, input.Namespace, labelSelector)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to get pods: %w", err)
			}

			output := formatPodList(pods)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// registerGetPodsAllNamespacesTool registers the kubectl_get_pods_all_namespaces tool.
// This tool is useful for discovering which namespace contains affected resources
// when the Jira ticket doesn't specify a namespace.
func registerGetPodsAllNamespacesTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "kubectl_get_pods_all_namespaces",
			Description: "List pods across all namespaces with optional label selector. Useful for discovering which namespace contains affected resources when namespace is unknown.",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			LabelSelector *string `json:"label_selector,omitempty" jsonschema:"Optional label selector to filter pods (e.g., app=myapp)"`
		}) (*mcp.CallToolResult, any, error) {
			labelSelector := ""
			if input.LabelSelector != nil {
				labelSelector = *input.LabelSelector
			}

			pods, err := client.GetPodsAllNamespaces(ctx, labelSelector)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to get pods across all namespaces: %w", err)
			}

			output := formatPodListWithNamespace(pods)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// registerGetDeploymentsTool registers the kubectl_get_deployments tool.
func registerGetDeploymentsTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "kubectl_get_deployments",
			Description: "List deployments in a namespace with optional label selector",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			Namespace     string  `json:"namespace" jsonschema:"Kubernetes namespace to list deployments from"`
			LabelSelector *string `json:"label_selector,omitempty" jsonschema:"Optional label selector to filter deployments (e.g., app=myapp)"`
		}) (*mcp.CallToolResult, any, error) {
			if input.Namespace == "" {
				return nil, nil, fmt.Errorf("namespace is required")
			}

			labelSelector := ""
			if input.LabelSelector != nil {
				labelSelector = *input.LabelSelector
			}

			deployments, err := client.GetDeployments(ctx, input.Namespace, labelSelector)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to get deployments: %w", err)
			}

			output := formatDeploymentList(deployments)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// registerGetDeploymentsAllNamespacesTool registers the kubectl_get_deployments_all_namespaces tool.
// This tool is useful for discovering which namespace contains affected deployments
// when the Jira ticket doesn't specify a namespace.
func registerGetDeploymentsAllNamespacesTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "kubectl_get_deployments_all_namespaces",
			Description: "List deployments across all namespaces with optional label selector. Useful for discovering which namespace contains affected deployments when namespace is unknown.",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			LabelSelector *string `json:"label_selector,omitempty" jsonschema:"Optional label selector to filter deployments (e.g., app=myapp)"`
		}) (*mcp.CallToolResult, any, error) {
			labelSelector := ""
			if input.LabelSelector != nil {
				labelSelector = *input.LabelSelector
			}

			deployments, err := client.GetDeploymentsAllNamespaces(ctx, labelSelector)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to get deployments across all namespaces: %w", err)
			}

			output := formatDeploymentListWithNamespace(deployments)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// registerGetServicesTool registers the kubectl_get_services tool.
func registerGetServicesTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "kubectl_get_services",
			Description: "List services in a namespace with optional label selector",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			Namespace     string  `json:"namespace" jsonschema:"Kubernetes namespace to list services from"`
			LabelSelector *string `json:"label_selector,omitempty" jsonschema:"Optional label selector to filter services (e.g., app=myapp)"`
		}) (*mcp.CallToolResult, any, error) {
			if input.Namespace == "" {
				return nil, nil, fmt.Errorf("namespace is required")
			}

			labelSelector := ""
			if input.LabelSelector != nil {
				labelSelector = *input.LabelSelector
			}

			services, err := client.GetServices(ctx, input.Namespace, labelSelector)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to get services: %w", err)
			}

			output := formatServiceList(services)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// registerGetServicesAllNamespacesTool registers the kubectl_get_services_all_namespaces tool.
// This tool is useful for discovering which namespace contains affected services
// when the Jira ticket doesn't specify a namespace.
func registerGetServicesAllNamespacesTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "kubectl_get_services_all_namespaces",
			Description: "List services across all namespaces with optional label selector. Useful for discovering which namespace contains affected services when namespace is unknown.",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			LabelSelector *string `json:"label_selector,omitempty" jsonschema:"Optional label selector to filter services (e.g., app=myapp)"`
		}) (*mcp.CallToolResult, any, error) {
			labelSelector := ""
			if input.LabelSelector != nil {
				labelSelector = *input.LabelSelector
			}

			services, err := client.GetServicesAllNamespaces(ctx, labelSelector)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to get services across all namespaces: %w", err)
			}

			output := formatServiceListWithNamespace(services)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// registerGetConfigMapsTool registers the kubectl_get_configmaps tool.
func registerGetConfigMapsTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "kubectl_get_configmaps",
			Description: "List configmaps in a namespace with optional label selector. Returns full configmap data (keys and values).",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			Namespace     string  `json:"namespace" jsonschema:"Kubernetes namespace to list configmaps from"`
			LabelSelector *string `json:"label_selector,omitempty" jsonschema:"Optional label selector to filter configmaps (e.g., app=myapp)"`
		}) (*mcp.CallToolResult, any, error) {
			if input.Namespace == "" {
				return nil, nil, fmt.Errorf("namespace is required")
			}

			labelSelector := ""
			if input.LabelSelector != nil {
				labelSelector = *input.LabelSelector
			}

			configmaps, err := client.GetConfigMaps(ctx, input.Namespace, labelSelector)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to get configmaps: %w", err)
			}

			output := formatConfigMapList(configmaps)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// registerGetSecretsTool registers the kubectl_get_secrets tool.
func registerGetSecretsTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "kubectl_get_secrets",
			Description: "List secrets in a namespace (metadata only - names, types, key names). NEVER returns secret data values.",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			Namespace     string  `json:"namespace" jsonschema:"Kubernetes namespace to list secrets from"`
			LabelSelector *string `json:"label_selector,omitempty" jsonschema:"Optional label selector to filter secrets (e.g., app=myapp)"`
		}) (*mcp.CallToolResult, any, error) {
			if input.Namespace == "" {
				return nil, nil, fmt.Errorf("namespace is required")
			}

			labelSelector := ""
			if input.LabelSelector != nil {
				labelSelector = *input.LabelSelector
			}

			secrets, err := client.GetSecrets(ctx, input.Namespace, labelSelector)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to get secrets: %w", err)
			}

			output := formatSecretList(secrets)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// registerLogsTool registers the kubectl_logs tool.
func registerLogsTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "kubectl_logs",
			Description: "Get logs from a pod, optionally specifying container and tail lines",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			Namespace string  `json:"namespace" jsonschema:"Kubernetes namespace of the pod"`
			Pod       string  `json:"pod" jsonschema:"Name of the pod to get logs from"`
			Container *string `json:"container,omitempty" jsonschema:"Optional container name (required for multi-container pods)"`
			TailLines *int64  `json:"tail_lines,omitempty" jsonschema:"Optional number of lines from the end of logs (default 100)"`
		}) (*mcp.CallToolResult, any, error) {
			if input.Namespace == "" {
				return nil, nil, fmt.Errorf("namespace is required")
			}
			if input.Pod == "" {
				return nil, nil, fmt.Errorf("pod is required")
			}

			tailLines := int64(defaultTailLines)
			if input.TailLines != nil {
				if *input.TailLines <= 0 {
					return nil, nil, fmt.Errorf("tail_lines must be positive")
				}
				if *input.TailLines > maxTailLines {
					return nil, nil, fmt.Errorf("tail_lines cannot exceed %d", maxTailLines)
				}
				tailLines = *input.TailLines
			}

			container := ""
			if input.Container != nil {
				container = *input.Container
			}

			logs, err := client.GetPodLogs(ctx, input.Namespace, input.Pod, container, tailLines)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to get pod logs: %w", err)
			}

			output := fmt.Sprintf("Logs from pod %s/%s (last %d lines):\n\n%s",
				input.Namespace, input.Pod, tailLines, logs)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// registerEventsTool registers the kubectl_events tool.
func registerEventsTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "kubectl_events",
			Description: "Get events in a namespace, optionally filtered by field selector",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			Namespace     string  `json:"namespace" jsonschema:"Kubernetes namespace to get events from"`
			FieldSelector *string `json:"field_selector,omitempty" jsonschema:"Optional field selector to filter events (e.g., involvedObject.name=mypod)"`
		}) (*mcp.CallToolResult, any, error) {
			if input.Namespace == "" {
				return nil, nil, fmt.Errorf("namespace is required")
			}

			fieldSelector := ""
			if input.FieldSelector != nil {
				fieldSelector = *input.FieldSelector
			}

			events, err := client.GetEvents(ctx, input.Namespace, fieldSelector)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to get events: %w", err)
			}

			output := formatEventList(events)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// registerDescribePodTool registers the kubectl_describe_pod tool.
func registerDescribePodTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "kubectl_describe_pod",
			Description: "Describe a pod with detailed information including status, containers, and conditions",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			Namespace string `json:"namespace" jsonschema:"Kubernetes namespace of the pod"`
			Pod       string `json:"pod" jsonschema:"Name of the pod to describe"`
		}) (*mcp.CallToolResult, any, error) {
			if input.Namespace == "" {
				return nil, nil, fmt.Errorf("namespace is required")
			}
			if input.Pod == "" {
				return nil, nil, fmt.Errorf("pod is required")
			}

			pod, err := client.DescribePod(ctx, input.Namespace, input.Pod)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to describe pod: %w", err)
			}

			output := formatPodDescription(pod)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// registerScaleTool registers the kubectl_scale tool.
// RBAC Required: apps/deployments - get, update
func registerScaleTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "kubectl_scale",
			Description: "Scale a deployment to a specified number of replicas. RBAC Required: apps/deployments - get, update",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			Namespace  string `json:"namespace" jsonschema:"Kubernetes namespace of the deployment"`
			Deployment string `json:"deployment" jsonschema:"Name of the deployment to scale"`
			Replicas   int32  `json:"replicas" jsonschema:"Target number of replicas (0 or positive integer)"`
		}) (*mcp.CallToolResult, any, error) {
			if input.Namespace == "" {
				return nil, nil, fmt.Errorf("namespace is required")
			}
			if input.Deployment == "" {
				return nil, nil, fmt.Errorf("deployment is required")
			}
			if input.Replicas < 0 {
				return nil, nil, fmt.Errorf("replicas must be >= 0")
			}

			deploy, err := client.ScaleDeployment(ctx, input.Namespace, input.Deployment, input.Replicas)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to scale deployment: %w", err)
			}

			output := formatScaleResult(deploy, input.Replicas)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// registerRolloutRestartTool registers the kubectl_rollout_restart tool.
// RBAC Required: apps/deployments - get, update
func registerRolloutRestartTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "kubectl_rollout_restart",
			Description: "Trigger a rolling restart of a deployment. RBAC Required: apps/deployments - get, update",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			Namespace  string `json:"namespace" jsonschema:"Kubernetes namespace of the deployment"`
			Deployment string `json:"deployment" jsonschema:"Name of the deployment to restart"`
		}) (*mcp.CallToolResult, any, error) {
			if input.Namespace == "" {
				return nil, nil, fmt.Errorf("namespace is required")
			}
			if input.Deployment == "" {
				return nil, nil, fmt.Errorf("deployment is required")
			}

			deploy, err := client.RolloutRestart(ctx, input.Namespace, input.Deployment)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to restart deployment: %w", err)
			}

			output := formatRolloutRestartResult(deploy)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// registerApplyTool registers the kubectl_apply tool.
// Supports ConfigMap, Secret, Pod, Service and other Kubernetes resources.
func registerApplyTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "kubectl_apply",
			Description: "Apply a YAML manifest to create or update a resource. Supports ConfigMap, Secret, Pod, Service. RBAC Required: depends on resource type",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			Namespace string `json:"namespace" jsonschema:"Default namespace for the resource (used if not specified in manifest)"`
			Manifest  string `json:"manifest" jsonschema:"YAML manifest to apply (must include apiVersion, kind, and metadata.name). Max size: 1MB"`
		}) (*mcp.CallToolResult, any, error) {
			if input.Namespace == "" {
				return nil, nil, fmt.Errorf("namespace is required")
			}
			if input.Manifest == "" {
				return nil, nil, fmt.Errorf("manifest is required")
			}
			// Enforce manifest size limit to prevent memory exhaustion
			if len(input.Manifest) > maxManifestBytes {
				return nil, nil, fmt.Errorf("manifest too large: %d bytes (max %d bytes / 1MB)",
					len(input.Manifest), maxManifestBytes)
			}

			result, err := client.ApplyManifest(ctx, input.Namespace, input.Manifest)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to apply manifest: %w", err)
			}

			output := formatApplyResult(result)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// registerDeleteTool registers the kubectl_delete tool.
// Supported types: pod, configmap, secret, deployment.
func registerDeleteTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "kubectl_delete",
			Description: "Delete a Kubernetes resource. Supported types: pod, configmap, secret, deployment. RBAC Required: depends on resource type",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			Namespace    string `json:"namespace" jsonschema:"Kubernetes namespace of the resource"`
			ResourceType string `json:"resource_type" jsonschema:"Type of resource (pod, configmap, secret, deployment)"`
			Name         string `json:"name" jsonschema:"Name of the resource to delete"`
		}) (*mcp.CallToolResult, any, error) {
			if input.Namespace == "" {
				return nil, nil, fmt.Errorf("namespace is required")
			}
			if input.ResourceType == "" {
				return nil, nil, fmt.Errorf("resource_type is required")
			}
			if input.Name == "" {
				return nil, nil, fmt.Errorf("name is required")
			}

			err := client.DeleteResource(ctx, input.Namespace, input.ResourceType, input.Name)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to delete resource: %w", err)
			}

			output := fmt.Sprintf("Successfully deleted %s/%s in namespace %s", input.ResourceType, input.Name, input.Namespace)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// =============================================================================
// Generic Get/Describe Tools
// =============================================================================

const defaultResourceLimit = 100

// registerGetTool registers the generic kubectl_get tool.
// Handles any K8s resource type via the dynamic client.
func registerGetTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "kubectl_get",
			Description: "Get any Kubernetes resource type. Returns clean YAML with noisy fields stripped. Secret data values are always redacted.",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			ResourceType  string  `json:"resource_type" jsonschema:"Resource type: pods, deployments, services, configmaps, secrets, statefulsets, daemonsets, jobs, nodes, ingresses, etc."`
			Namespace     *string `json:"namespace,omitempty" jsonschema:"Kubernetes namespace. Omit for all namespaces or cluster-scoped resources."`
			Name          *string `json:"name,omitempty" jsonschema:"Specific resource name. Omit for listing."`
			LabelSelector *string `json:"label_selector,omitempty" jsonschema:"Label selector to filter (e.g. app=myapp)"`
			FieldSelector *string `json:"field_selector,omitempty" jsonschema:"Field selector to filter (e.g. status.phase=Running)"`
			Limit         *int64  `json:"limit,omitempty" jsonschema:"Max resources to return (default 100)"`
		}) (*mcp.CallToolResult, any, error) {
			if input.ResourceType == "" {
				return nil, nil, fmt.Errorf("resource_type is required")
			}

			namespace := ""
			if input.Namespace != nil {
				namespace = *input.Namespace
			}
			name := ""
			if input.Name != nil {
				name = *input.Name
			}
			labelSelector := ""
			if input.LabelSelector != nil {
				labelSelector = *input.LabelSelector
			}
			fieldSelector := ""
			if input.FieldSelector != nil {
				fieldSelector = *input.FieldSelector
			}
			limit := int64(defaultResourceLimit)
			if input.Limit != nil && *input.Limit > 0 {
				limit = *input.Limit
			}

			items, err := client.GetResources(ctx, input.ResourceType, namespace, name, labelSelector, fieldSelector, limit)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to get %s: %w", input.ResourceType, err)
			}

			var output string
			if name != "" && len(items) == 1 {
				output = formatResourceYAML(&items[0])
			} else {
				output = formatResourceListYAML(items)
			}

			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// registerDescribeTool registers the generic kubectl_describe tool.
// Returns full resource YAML with noisy fields stripped.
func registerDescribeTool(server *mcp.Server, client *Client) {
	mcp.AddTool(
		server,
		&mcp.Tool{
			Name:        "kubectl_describe",
			Description: "Describe any Kubernetes resource. Returns full resource YAML with noisy fields stripped. Secret data values are always redacted.",
		},
		func(ctx context.Context, _ *mcp.CallToolRequest, input struct {
			ResourceType string  `json:"resource_type" jsonschema:"Resource type: pod, deployment, service, node, etc."`
			Name         string  `json:"name" jsonschema:"Resource name"`
			Namespace    *string `json:"namespace,omitempty" jsonschema:"Kubernetes namespace. Omit for cluster-scoped resources."`
		}) (*mcp.CallToolResult, any, error) {
			if input.ResourceType == "" {
				return nil, nil, fmt.Errorf("resource_type is required")
			}
			if input.Name == "" {
				return nil, nil, fmt.Errorf("name is required")
			}

			namespace := ""
			if input.Namespace != nil {
				namespace = *input.Namespace
			}

			obj, err := client.DescribeResource(ctx, input.ResourceType, input.Name, namespace)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to describe %s/%s: %w", input.ResourceType, input.Name, err)
			}

			output := formatResourceYAML(obj)
			return &mcp.CallToolResult{
				Content: []mcp.Content{&mcp.TextContent{Text: output}},
			}, nil, nil
		},
	)
}

// formatPodList formats a list of pods into a human-readable string.
// Returns "No pods found" if the list is empty.
func formatPodList(pods []corev1.Pod) string {
	if len(pods) == 0 {
		return "No pods found"
	}

	var result strings.Builder
	result.WriteString(fmt.Sprintf("Found %d pod(s):\n\n", len(pods)))

	for _, pod := range pods {
		result.WriteString(fmt.Sprintf("NAME: %s\n", pod.Name))
		result.WriteString(fmt.Sprintf("  NAMESPACE: %s\n", pod.Namespace))
		result.WriteString(fmt.Sprintf("  STATUS: %s\n", pod.Status.Phase))
		if len(pod.Labels) > 0 {
			result.WriteString("  LABELS:\n")
			// Sort labels for deterministic output
			keys := make([]string, 0, len(pod.Labels))
			for k := range pod.Labels {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			for _, k := range keys {
				result.WriteString(fmt.Sprintf("    %s: %s\n", k, pod.Labels[k]))
			}
		}
		result.WriteString("\n")
	}

	return result.String()
}

// formatPodListWithNamespace formats a list of pods with namespace prominently displayed.
// Designed for cross-namespace searches where namespace discovery is the goal.
// Returns "No pods found" if the list is empty.
func formatPodListWithNamespace(pods []corev1.Pod) string {
	if len(pods) == 0 {
		return "No pods found"
	}

	var result strings.Builder
	result.WriteString(fmt.Sprintf("Found %d pod(s) across all namespaces:\n\n", len(pods)))
	result.WriteString("NAMESPACE\tNAME\tSTATUS\tRESTARTS\tAGE\n")
	result.WriteString("---------\t----\t------\t--------\t---\n")

	for _, pod := range pods {
		age := time.Since(pod.CreationTimestamp.Time).Round(time.Second)
		restarts := int32(0)
		for _, cs := range pod.Status.ContainerStatuses {
			restarts += cs.RestartCount
		}
		result.WriteString(fmt.Sprintf("%s\t%s\t%s\t%d\t%s\n",
			pod.Namespace, pod.Name, string(pod.Status.Phase), restarts, formatAge(age)))
	}

	return result.String()
}

// formatAge formats a duration into a human-readable age string (e.g., "5d", "2h", "30m", "45s").
func formatAge(d time.Duration) string {
	if d < time.Minute {
		return fmt.Sprintf("%ds", int(d.Seconds()))
	}
	if d < time.Hour {
		return fmt.Sprintf("%dm", int(d.Minutes()))
	}
	if d < 24*time.Hour {
		return fmt.Sprintf("%dh", int(d.Hours()))
	}
	return fmt.Sprintf("%dd", int(d.Hours()/24))
}

// formatEventList formats a list of Kubernetes events into a human-readable string.
// Returns "No events found" if the list is empty.
func formatEventList(events []corev1.Event) string {
	if len(events) == 0 {
		return "No events found"
	}

	var result strings.Builder
	result.WriteString(fmt.Sprintf("Found %d event(s):\n\n", len(events)))

	for _, event := range events {
		result.WriteString(fmt.Sprintf("TYPE: %s\n", event.Type))
		result.WriteString(fmt.Sprintf("  REASON: %s\n", event.Reason))
		result.WriteString(fmt.Sprintf("  MESSAGE: %s\n", event.Message))
		result.WriteString(fmt.Sprintf("  OBJECT: %s/%s\n", event.InvolvedObject.Kind, event.InvolvedObject.Name))
		if !event.FirstTimestamp.IsZero() {
			result.WriteString(fmt.Sprintf("  FIRST SEEN: %s\n", event.FirstTimestamp.Format(timeFormat)))
		}
		if !event.LastTimestamp.IsZero() {
			result.WriteString(fmt.Sprintf("  LAST SEEN: %s\n", event.LastTimestamp.Format(timeFormat)))
		}
		result.WriteString(fmt.Sprintf("  COUNT: %d\n", event.Count))
		result.WriteString("\n")
	}

	return result.String()
}

// formatPodDescription formats detailed pod information into a human-readable string.
// Includes status, labels, containers, container statuses, and conditions.
func formatPodDescription(pod *corev1.Pod) string {
	var result strings.Builder

	result.WriteString(fmt.Sprintf("Pod: %s/%s\n\n", pod.Namespace, pod.Name))

	result.WriteString(formatPodStatus(pod))
	result.WriteString(formatPodLabels(pod))
	result.WriteString(formatPodContainers(pod))
	result.WriteString(formatContainerStatuses(pod))
	result.WriteString(formatPodConditions(pod))

	return result.String()
}

// formatPodStatus formats the basic pod status information.
func formatPodStatus(pod *corev1.Pod) string {
	var result strings.Builder

	result.WriteString("STATUS:\n")
	result.WriteString(fmt.Sprintf("  Phase: %s\n", pod.Status.Phase))
	result.WriteString(fmt.Sprintf("  Host IP: %s\n", pod.Status.HostIP))
	result.WriteString(fmt.Sprintf("  Pod IP: %s\n", pod.Status.PodIP))
	result.WriteString("\n")

	return result.String()
}

// formatPodLabels formats pod labels into a human-readable string.
func formatPodLabels(pod *corev1.Pod) string {
	if len(pod.Labels) == 0 {
		return ""
	}

	var result strings.Builder
	result.WriteString("LABELS:\n")

	keys := make([]string, 0, len(pod.Labels))
	for k := range pod.Labels {
		keys = append(keys, k)
	}
	sort.Strings(keys)

	for _, k := range keys {
		result.WriteString(fmt.Sprintf("  %s: %s\n", k, pod.Labels[k]))
	}
	result.WriteString("\n")

	return result.String()
}

// formatPodContainers formats container definitions into a human-readable string.
func formatPodContainers(pod *corev1.Pod) string {
	var result strings.Builder

	result.WriteString("CONTAINERS:\n")
	for _, container := range pod.Spec.Containers {
		result.WriteString(fmt.Sprintf("  %s:\n", container.Name))
		result.WriteString(fmt.Sprintf("    Image: %s\n", container.Image))
	}
	result.WriteString("\n")

	return result.String()
}

// formatContainerStatuses formats container runtime status into a human-readable string.
func formatContainerStatuses(pod *corev1.Pod) string {
	if len(pod.Status.ContainerStatuses) == 0 {
		return ""
	}

	var result strings.Builder
	result.WriteString("CONTAINER STATUSES:\n")

	for _, cs := range pod.Status.ContainerStatuses {
		result.WriteString(fmt.Sprintf("  %s:\n", cs.Name))
		result.WriteString(fmt.Sprintf("    Ready: %t\n", cs.Ready))
		result.WriteString(fmt.Sprintf("    Restart Count: %d\n", cs.RestartCount))
		result.WriteString(formatContainerState(cs))
	}
	result.WriteString("\n")

	return result.String()
}

// formatContainerState formats the state of a single container.
func formatContainerState(cs corev1.ContainerStatus) string {
	var result strings.Builder

	if cs.State.Running != nil {
		result.WriteString("    State: Running\n")
		if !cs.State.Running.StartedAt.IsZero() {
			result.WriteString(fmt.Sprintf("      Started: %s\n", cs.State.Running.StartedAt.Format(timeFormat)))
		}
	} else if cs.State.Waiting != nil {
		result.WriteString("    State: Waiting\n")
		result.WriteString(fmt.Sprintf("      Reason: %s\n", cs.State.Waiting.Reason))
		if cs.State.Waiting.Message != "" {
			result.WriteString(fmt.Sprintf("      Message: %s\n", cs.State.Waiting.Message))
		}
	} else if cs.State.Terminated != nil {
		result.WriteString("    State: Terminated\n")
		result.WriteString(fmt.Sprintf("      Reason: %s\n", cs.State.Terminated.Reason))
		result.WriteString(fmt.Sprintf("      Exit Code: %d\n", cs.State.Terminated.ExitCode))
	}

	return result.String()
}

// formatPodConditions formats pod conditions into a human-readable string.
func formatPodConditions(pod *corev1.Pod) string {
	if len(pod.Status.Conditions) == 0 {
		return ""
	}

	var result strings.Builder
	result.WriteString("CONDITIONS:\n")

	for _, cond := range pod.Status.Conditions {
		result.WriteString(fmt.Sprintf("  %s: %s\n", cond.Type, cond.Status))
		if cond.Reason != "" {
			result.WriteString(fmt.Sprintf("    Reason: %s\n", cond.Reason))
		}
		if cond.Message != "" {
			result.WriteString(fmt.Sprintf("    Message: %s\n", cond.Message))
		}
	}

	return result.String()
}

// =============================================================================
// Write Operation Formatters
// =============================================================================

// formatScaleResult formats the result of a scale operation.
func formatScaleResult(deploy *appsv1.Deployment, targetReplicas int32) string {
	var result strings.Builder

	result.WriteString(fmt.Sprintf("Deployment scaled: %s/%s\n\n", deploy.Namespace, deploy.Name))
	result.WriteString(fmt.Sprintf("  Target Replicas: %d\n", targetReplicas))
	result.WriteString(fmt.Sprintf("  Current Replicas: %d\n", deploy.Status.Replicas))
	result.WriteString(fmt.Sprintf("  Ready Replicas: %d\n", deploy.Status.ReadyReplicas))
	result.WriteString(fmt.Sprintf("  Available Replicas: %d\n", deploy.Status.AvailableReplicas))

	return result.String()
}

// formatRolloutRestartResult formats the result of a rollout restart operation.
func formatRolloutRestartResult(deploy *appsv1.Deployment) string {
	var result strings.Builder

	result.WriteString(fmt.Sprintf("Deployment restarted: %s/%s\n\n", deploy.Namespace, deploy.Name))

	restartedAt := ""
	if deploy.Spec.Template.Annotations != nil {
		restartedAt = deploy.Spec.Template.Annotations["kubectl.kubernetes.io/restartedAt"]
	}
	if restartedAt != "" {
		result.WriteString(fmt.Sprintf("  Restart Timestamp: %s\n", restartedAt))
	}

	if deploy.Spec.Replicas != nil {
		result.WriteString(fmt.Sprintf("  Replicas: %d\n", *deploy.Spec.Replicas))
	}
	result.WriteString(fmt.Sprintf("  Ready Replicas: %d\n", deploy.Status.ReadyReplicas))
	result.WriteString("\nNote: Rolling restart initiated. Pods will be recreated gradually.\n")

	return result.String()
}

// formatApplyResult formats the result of an apply operation.
func formatApplyResult(obj *unstructured.Unstructured) string {
	var result strings.Builder

	result.WriteString(fmt.Sprintf("Resource applied: %s/%s\n\n", obj.GetKind(), obj.GetName()))
	result.WriteString(fmt.Sprintf("  API Version: %s\n", obj.GetAPIVersion()))
	result.WriteString(fmt.Sprintf("  Kind: %s\n", obj.GetKind()))
	result.WriteString(fmt.Sprintf("  Namespace: %s\n", obj.GetNamespace()))
	result.WriteString(fmt.Sprintf("  Name: %s\n", obj.GetName()))
	result.WriteString(fmt.Sprintf("  UID: %s\n", obj.GetUID()))
	result.WriteString(fmt.Sprintf("  Resource Version: %s\n", obj.GetResourceVersion()))

	return result.String()
}

// =============================================================================
// Deployment Formatters
// =============================================================================

// formatDeploymentList formats a list of deployments into a human-readable string.
// Returns "No deployments found" if the list is empty.
func formatDeploymentList(deployments []appsv1.Deployment) string {
	if len(deployments) == 0 {
		return "No deployments found"
	}

	var result strings.Builder
	result.WriteString(fmt.Sprintf("Found %d deployment(s):\n\n", len(deployments)))

	for _, deploy := range deployments {
		result.WriteString(fmt.Sprintf("NAME: %s\n", deploy.Name))
		result.WriteString(fmt.Sprintf("  NAMESPACE: %s\n", deploy.Namespace))
		replicas := int32(0)
		if deploy.Spec.Replicas != nil {
			replicas = *deploy.Spec.Replicas
		}
		result.WriteString(fmt.Sprintf("  REPLICAS: %d/%d (ready/desired)\n", deploy.Status.ReadyReplicas, replicas))
		result.WriteString(fmt.Sprintf("  AVAILABLE: %d\n", deploy.Status.AvailableReplicas))
		result.WriteString(fmt.Sprintf("  UP-TO-DATE: %d\n", deploy.Status.UpdatedReplicas))
		age := time.Since(deploy.CreationTimestamp.Time).Round(time.Second)
		result.WriteString(fmt.Sprintf("  AGE: %s\n", formatAge(age)))
		if len(deploy.Labels) > 0 {
			result.WriteString("  LABELS:\n")
			keys := make([]string, 0, len(deploy.Labels))
			for k := range deploy.Labels {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			for _, k := range keys {
				result.WriteString(fmt.Sprintf("    %s: %s\n", k, deploy.Labels[k]))
			}
		}
		result.WriteString(formatReferencedResources(&deploy))
		result.WriteString("\n")
	}

	return result.String()
}

// referencedResources holds the set of resources discovered from a deployment spec.
type referencedResources struct {
	configmaps      map[string]struct{}
	secrets         map[string]struct{}
	pvcs            map[string]struct{}
	serviceAccount  string
}

// extractReferencedResources scans a deployment's pod template spec to discover
// ConfigMaps, Secrets, PVCs, and ServiceAccounts referenced by containers
// (both regular and init), env vars, envFrom, and volumes.
func extractReferencedResources(deploy *appsv1.Deployment) referencedResources {
	refs := referencedResources{
		configmaps: make(map[string]struct{}),
		secrets:    make(map[string]struct{}),
		pvcs:       make(map[string]struct{}),
	}

	spec := &deploy.Spec.Template.Spec

	// Service account
	if spec.ServiceAccountName != "" {
		refs.serviceAccount = spec.ServiceAccountName
	}

	// Scan all containers (regular + init)
	allContainers := append(spec.InitContainers, spec.Containers...)
	for _, container := range allContainers {
		// envFrom references
		for _, envFrom := range container.EnvFrom {
			if envFrom.ConfigMapRef != nil && envFrom.ConfigMapRef.Name != "" {
				refs.configmaps[envFrom.ConfigMapRef.Name] = struct{}{}
			}
			if envFrom.SecretRef != nil && envFrom.SecretRef.Name != "" { // pragma: allowlist secret
				refs.secrets[envFrom.SecretRef.Name] = struct{}{}
			}
		}

		// env[].valueFrom references
		for _, env := range container.Env {
			if env.ValueFrom == nil {
				continue
			}
			if env.ValueFrom.ConfigMapKeyRef != nil && env.ValueFrom.ConfigMapKeyRef.Name != "" {
				refs.configmaps[env.ValueFrom.ConfigMapKeyRef.Name] = struct{}{}
			}
			if env.ValueFrom.SecretKeyRef != nil && env.ValueFrom.SecretKeyRef.Name != "" { // pragma: allowlist secret
				refs.secrets[env.ValueFrom.SecretKeyRef.Name] = struct{}{}
			}
		}
	}

	// Volume references
	for _, vol := range spec.Volumes {
		if vol.ConfigMap != nil && vol.ConfigMap.Name != "" {
			refs.configmaps[vol.ConfigMap.Name] = struct{}{}
		}
		if vol.Secret != nil && vol.Secret.SecretName != "" { // pragma: allowlist secret
			refs.secrets[vol.Secret.SecretName] = struct{}{} // pragma: allowlist secret
		}
		if vol.PersistentVolumeClaim != nil && vol.PersistentVolumeClaim.ClaimName != "" {
			refs.pvcs[vol.PersistentVolumeClaim.ClaimName] = struct{}{}
		}
	}

	return refs
}

// formatReferencedResources formats extracted resource references into a
// human-readable REFERENCED_RESOURCES section. Returns empty string if
// the deployment has no resource references.
func formatReferencedResources(deploy *appsv1.Deployment) string {
	refs := extractReferencedResources(deploy)

	// Check if there's anything to output
	hasRefs := len(refs.configmaps) > 0 || len(refs.secrets) > 0 ||
		len(refs.pvcs) > 0 || refs.serviceAccount != ""
	if !hasRefs {
		return ""
	}

	var result strings.Builder
	result.WriteString("  REFERENCED_RESOURCES:\n")

	if len(refs.configmaps) > 0 {
		names := sortedKeys(refs.configmaps)
		result.WriteString(fmt.Sprintf("    CONFIGMAPS: %s\n", strings.Join(names, ", ")))
	}
	if len(refs.secrets) > 0 {
		names := sortedKeys(refs.secrets)
		result.WriteString(fmt.Sprintf("    SECRETS: %s\n", strings.Join(names, ", ")))
	}
	if refs.serviceAccount != "" {
		result.WriteString(fmt.Sprintf("    SERVICE_ACCOUNT: %s\n", refs.serviceAccount))
	}
	if len(refs.pvcs) > 0 {
		names := sortedKeys(refs.pvcs)
		result.WriteString(fmt.Sprintf("    PVCS: %s\n", strings.Join(names, ", ")))
	}

	return result.String()
}

// sortedKeys returns a sorted slice of keys from a set (map[string]struct{}).
func sortedKeys(m map[string]struct{}) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// formatDeploymentListWithNamespace formats a list of deployments with namespace prominently displayed.
// Designed for cross-namespace searches where namespace discovery is the goal.
// Returns "No deployments found" if the list is empty.
func formatDeploymentListWithNamespace(deployments []appsv1.Deployment) string {
	if len(deployments) == 0 {
		return "No deployments found"
	}

	var result strings.Builder
	result.WriteString(fmt.Sprintf("Found %d deployment(s) across all namespaces:\n\n", len(deployments)))
	result.WriteString("NAMESPACE\tNAME\tREADY\tUP-TO-DATE\tAVAILABLE\tAGE\n")
	result.WriteString("---------\t----\t-----\t----------\t---------\t---\n")

	for _, deploy := range deployments {
		age := time.Since(deploy.CreationTimestamp.Time).Round(time.Second)
		replicas := int32(0)
		if deploy.Spec.Replicas != nil {
			replicas = *deploy.Spec.Replicas
		}
		result.WriteString(fmt.Sprintf("%s\t%s\t%d/%d\t%d\t%d\t%s\n",
			deploy.Namespace, deploy.Name, deploy.Status.ReadyReplicas, replicas,
			deploy.Status.UpdatedReplicas, deploy.Status.AvailableReplicas, formatAge(age)))
	}

	return result.String()
}

// =============================================================================
// Service Formatters
// =============================================================================

// formatServiceList formats a list of services into a human-readable string.
// Returns "No services found" if the list is empty.
func formatServiceList(services []corev1.Service) string {
	if len(services) == 0 {
		return "No services found"
	}

	var result strings.Builder
	result.WriteString(fmt.Sprintf("Found %d service(s):\n\n", len(services)))

	for _, svc := range services {
		result.WriteString(fmt.Sprintf("NAME: %s\n", svc.Name))
		result.WriteString(fmt.Sprintf("  NAMESPACE: %s\n", svc.Namespace))
		result.WriteString(fmt.Sprintf("  TYPE: %s\n", svc.Spec.Type))
		result.WriteString(fmt.Sprintf("  CLUSTER-IP: %s\n", svc.Spec.ClusterIP))
		if len(svc.Spec.ExternalIPs) > 0 {
			result.WriteString(fmt.Sprintf("  EXTERNAL-IPs: %s\n", strings.Join(svc.Spec.ExternalIPs, ",")))
		}
		if len(svc.Status.LoadBalancer.Ingress) > 0 {
			var ips []string
			for _, ingress := range svc.Status.LoadBalancer.Ingress {
				if ingress.IP != "" {
					ips = append(ips, ingress.IP)
				} else if ingress.Hostname != "" {
					ips = append(ips, ingress.Hostname)
				}
			}
			if len(ips) > 0 {
				result.WriteString(fmt.Sprintf("  EXTERNAL-IP: %s\n", strings.Join(ips, ",")))
			}
		}
		if len(svc.Spec.Ports) > 0 {
			result.WriteString("  PORTS:\n")
			for _, port := range svc.Spec.Ports {
				portStr := fmt.Sprintf("%d/%s", port.Port, port.Protocol)
				if port.NodePort != 0 {
					portStr = fmt.Sprintf("%d:%d/%s", port.Port, port.NodePort, port.Protocol)
				}
				if port.Name != "" {
					result.WriteString(fmt.Sprintf("    %s: %s\n", port.Name, portStr))
				} else {
					result.WriteString(fmt.Sprintf("    %s\n", portStr))
				}
			}
		}
		age := time.Since(svc.CreationTimestamp.Time).Round(time.Second)
		result.WriteString(fmt.Sprintf("  AGE: %s\n", formatAge(age)))
		if len(svc.Spec.Selector) > 0 {
			result.WriteString("  SELECTOR:\n")
			keys := make([]string, 0, len(svc.Spec.Selector))
			for k := range svc.Spec.Selector {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			for _, k := range keys {
				result.WriteString(fmt.Sprintf("    %s: %s\n", k, svc.Spec.Selector[k]))
			}
		}
		result.WriteString("\n")
	}

	return result.String()
}

// formatServiceListWithNamespace formats a list of services with namespace prominently displayed.
// Designed for cross-namespace searches where namespace discovery is the goal.
// Returns "No services found" if the list is empty.
func formatServiceListWithNamespace(services []corev1.Service) string {
	if len(services) == 0 {
		return "No services found"
	}

	var result strings.Builder
	result.WriteString(fmt.Sprintf("Found %d service(s) across all namespaces:\n\n", len(services)))
	result.WriteString("NAMESPACE\tNAME\tTYPE\tCLUSTER-IP\tPORTS\tAGE\n")
	result.WriteString("---------\t----\t----\t----------\t-----\t---\n")

	for _, svc := range services {
		age := time.Since(svc.CreationTimestamp.Time).Round(time.Second)
		var ports []string
		for _, port := range svc.Spec.Ports {
			if port.NodePort != 0 {
				ports = append(ports, fmt.Sprintf("%d:%d/%s", port.Port, port.NodePort, port.Protocol))
			} else {
				ports = append(ports, fmt.Sprintf("%d/%s", port.Port, port.Protocol))
			}
		}
		portStr := "<none>"
		if len(ports) > 0 {
			portStr = strings.Join(ports, ",")
		}
		result.WriteString(fmt.Sprintf("%s\t%s\t%s\t%s\t%s\t%s\n",
			svc.Namespace, svc.Name, svc.Spec.Type, svc.Spec.ClusterIP, portStr, formatAge(age)))
	}

	return result.String()
}

// =============================================================================
// ConfigMap Formatters
// =============================================================================

// formatConfigMapList formats a list of configmaps showing full data (keys + values).
// Returns "No configmaps found" if the list is empty.
func formatConfigMapList(configmaps []corev1.ConfigMap) string {
	if len(configmaps) == 0 {
		return "No configmaps found"
	}

	var result strings.Builder
	result.WriteString(fmt.Sprintf("Found %d configmap(s):\n\n", len(configmaps)))

	for _, cm := range configmaps {
		result.WriteString(fmt.Sprintf("NAME: %s\n", cm.Name))
		result.WriteString(fmt.Sprintf("  NAMESPACE: %s\n", cm.Namespace))
		if len(cm.Data) > 0 {
			result.WriteString("  DATA:\n")
			keys := make([]string, 0, len(cm.Data))
			for k := range cm.Data {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			for _, k := range keys {
				result.WriteString(fmt.Sprintf("    %s: %s\n", k, cm.Data[k]))
			}
		} else {
			result.WriteString("  DATA: <empty>\n")
		}
		age := time.Since(cm.CreationTimestamp.Time).Round(time.Second)
		result.WriteString(fmt.Sprintf("  AGE: %s\n", formatAge(age)))
		result.WriteString("\n")
	}

	return result.String()
}

// =============================================================================
// Secret Formatters
// =============================================================================

// formatSecretList formats secrets showing ONLY metadata.
// SECURITY: This MUST never include secret data values (.Data or .StringData).
// Only key names are shown, never the values they contain.
func formatSecretList(secrets []corev1.Secret) string {
	if len(secrets) == 0 {
		return "No secrets found"
	}

	var result strings.Builder
	result.WriteString(fmt.Sprintf("Found %d secret(s):\n\n", len(secrets)))

	for _, secret := range secrets { // pragma: allowlist secret
		result.WriteString(fmt.Sprintf("NAME: %s\n", secret.Name))
		result.WriteString(fmt.Sprintf("  NAMESPACE: %s\n", secret.Namespace))
		result.WriteString(fmt.Sprintf("  TYPE: %s\n", secret.Type))
		if len(secret.Data) > 0 {
			// SECURITY: Only output key names, NEVER values
			keys := make([]string, 0, len(secret.Data))
			for k := range secret.Data {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			result.WriteString(fmt.Sprintf("  DATA KEYS: %s\n", strings.Join(keys, ", ")))
		} else {
			result.WriteString("  DATA KEYS: <none>\n")
		}
		age := time.Since(secret.CreationTimestamp.Time).Round(time.Second)
		result.WriteString(fmt.Sprintf("  AGE: %s\n", formatAge(age)))
		result.WriteString("\n")
	}

	return result.String()
}

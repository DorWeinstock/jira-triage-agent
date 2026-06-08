package k8s

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"regexp"
	"time"
	"unicode/utf8"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/util/yaml"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"

	"golang.org/x/time/rate"
)

// maxLogBytes limits memory usage when reading pod logs.
// 5MB is sufficient for most debugging scenarios.
const maxLogBytes = 5 * 1024 * 1024

// Rate limiting configuration for K8s API
// Default: 20 requests/second with burst of 50 (K8s API is typically faster than external APIs)
const (
	defaultK8sRequestsPerSecond = 20
	defaultK8sBurstSize         = 50
)

// validNameRE matches valid Kubernetes resource names (RFC 1123 DNS subdomain).
// Names must be lowercase alphanumeric characters, '-' or '.', and start/end alphanumerically.
var validNameRE = regexp.MustCompile(`^[a-z0-9]([a-z0-9\-\.]*[a-z0-9])?$`)

// validateName returns an error if the given Kubernetes resource name or namespace
// contains invalid or unsafe characters (e.g., path traversal sequences).
func validateName(field, value string) error {
	if value == "" {
		return fmt.Errorf("%s cannot be empty", field)
	}
	if len(value) > 253 {
		return fmt.Errorf("%s %q exceeds maximum length of 253 characters", field, value)
	}
	if !validNameRE.MatchString(value) {
		return fmt.Errorf("%s %q contains invalid characters; must match [a-z0-9][a-z0-9-.]*[a-z0-9]", field, value)
	}
	return nil
}

type Client struct {
	clientset     kubernetes.Interface
	dynamicClient dynamic.Interface
	limiter       *rate.Limiter
}

func NewClient() (*Client, error) {
	config, err := rest.InClusterConfig()
	if err != nil {
		return nil, fmt.Errorf("getting in-cluster config: %w", err)
	}

	return newClientFromConfig(config)
}

// NewClientFromKubeconfig creates a Client using an external kubeconfig file.
// This enables connecting to remote clusters for multi-cluster support.
func NewClientFromKubeconfig(kubeconfigPath string) (*Client, error) {
	config, err := clientcmd.BuildConfigFromFlags("", kubeconfigPath)
	if err != nil {
		return nil, fmt.Errorf("loading kubeconfig from %s: %w", kubeconfigPath, err)
	}

	return newClientFromConfig(config)
}

// newClientFromConfig creates a Client from a rest.Config.
// Shared logic for both in-cluster and kubeconfig-based initialization.
func newClientFromConfig(config *rest.Config) (*Client, error) {
	clientset, err := kubernetes.NewForConfig(config)
	if err != nil {
		return nil, fmt.Errorf("creating clientset: %w", err)
	}

	dynamicClient, err := dynamic.NewForConfig(config)
	if err != nil {
		return nil, fmt.Errorf("creating dynamic client: %w", err)
	}

	return &Client{
		clientset:     clientset,
		dynamicClient: dynamicClient,
		limiter:       rate.NewLimiter(rate.Limit(defaultK8sRequestsPerSecond), defaultK8sBurstSize),
	}, nil
}

// NewClientWithClientset creates a Client with the provided clientset and no dynamic client.
// Operations that require the dynamic client (ApplyManifest, GetResources, DescribeResource)
// will return an explicit error at call time. This constructor is intended for unit tests
// that only exercise clientset-based methods.
func NewClientWithClientset(clientset kubernetes.Interface) *Client {
	return &Client{
		clientset:     clientset,
		dynamicClient: nil,
		limiter:       rate.NewLimiter(rate.Limit(defaultK8sRequestsPerSecond), defaultK8sBurstSize),
	}
}

// waitForRateLimit blocks until the rate limiter allows the request or context is cancelled.
func (c *Client) waitForRateLimit(ctx context.Context) error {
	if c.limiter == nil {
		return nil // No rate limiting if limiter not set
	}
	if err := c.limiter.Wait(ctx); err != nil {
		return fmt.Errorf("rate limit wait cancelled: %w", err)
	}
	return nil
}

func (c *Client) GetPods(ctx context.Context, namespace string, labelSelector string) ([]corev1.Pod, error) {
	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	if namespace == "" {
		return nil, fmt.Errorf("namespace cannot be empty")
	}

	opts := metav1.ListOptions{}
	if labelSelector != "" {
		opts.LabelSelector = labelSelector
	}

	pods, err := c.clientset.CoreV1().Pods(namespace).List(ctx, opts)
	if err != nil {
		return nil, fmt.Errorf("listing pods: %w", err)
	}

	return pods.Items, nil
}

// GetPodsAllNamespaces lists pods across all namespaces with optional label selector.
// This is useful when the namespace is unknown and needs to be discovered.
func (c *Client) GetPodsAllNamespaces(ctx context.Context, labelSelector string) ([]corev1.Pod, error) {
	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	opts := metav1.ListOptions{}
	if labelSelector != "" {
		opts.LabelSelector = labelSelector
	}

	// Empty string namespace means all namespaces
	pods, err := c.clientset.CoreV1().Pods("").List(ctx, opts)
	if err != nil {
		return nil, fmt.Errorf("listing pods across all namespaces: %w", err)
	}

	return pods.Items, nil
}

func (c *Client) GetPodLogs(ctx context.Context, namespace, podName, container string, tailLines int64) (string, error) {
	if err := c.waitForRateLimit(ctx); err != nil {
		return "", err
	}

	if err := validateName("namespace", namespace); err != nil {
		return "", err
	}
	if err := validateName("pod name", podName); err != nil {
		return "", err
	}
	if tailLines < 0 {
		return "", fmt.Errorf("tailLines must be >= 0")
	}

	opts := &corev1.PodLogOptions{
		TailLines: &tailLines,
	}
	if container != "" {
		opts.Container = container
	}

	req := c.clientset.CoreV1().Pods(namespace).GetLogs(podName, opts)
	stream, err := req.Stream(ctx)
	if err != nil {
		return "", fmt.Errorf("getting log stream: %w", err)
	}
	defer stream.Close()

	// Limit bytes read to prevent unbounded memory usage
	limitedReader := io.LimitReader(stream, maxLogBytes+1)
	buf := new(bytes.Buffer)
	n, err := io.Copy(buf, limitedReader)
	if err != nil {
		return "", fmt.Errorf("reading logs: %w", err)
	}

	result := buf.String()
	if n > maxLogBytes {
		// Truncate at a valid UTF-8 boundary to avoid splitting multi-byte characters.
		truncated := result[:maxLogBytes]
		for len(truncated) > 0 && !utf8.ValidString(truncated) {
			truncated = truncated[:len(truncated)-1]
		}
		result = truncated + "\n...[TRUNCATED: logs exceeded 5MB limit]"
	}

	return result, nil
}

func (c *Client) GetEvents(ctx context.Context, namespace string, fieldSelector string) ([]corev1.Event, error) {
	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	if namespace == "" {
		return nil, fmt.Errorf("namespace cannot be empty")
	}

	opts := metav1.ListOptions{}
	if fieldSelector != "" {
		opts.FieldSelector = fieldSelector
	}

	events, err := c.clientset.CoreV1().Events(namespace).List(ctx, opts)
	if err != nil {
		return nil, fmt.Errorf("listing events: %w", err)
	}

	return events.Items, nil
}

func (c *Client) DescribePod(ctx context.Context, namespace, podName string) (*corev1.Pod, error) {
	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	if err := validateName("namespace", namespace); err != nil {
		return nil, err
	}
	if err := validateName("pod name", podName); err != nil {
		return nil, err
	}

	pod, err := c.clientset.CoreV1().Pods(namespace).Get(ctx, podName, metav1.GetOptions{})
	if err != nil {
		return nil, fmt.Errorf("getting pod: %w", err)
	}
	return pod, nil
}

// =============================================================================
// Write Operations
// =============================================================================
//
// SECURITY NOTE: These operations require appropriate RBAC permissions.
// The service account running this code needs the following ClusterRole rules:
//
//   - apiGroups: ["apps"]
//     resources: ["deployments", "deployments/scale"]
//     verbs: ["get", "patch", "update"]
//
//   - apiGroups: [""]
//     resources: ["configmaps", "secrets", "pods"]
//     verbs: ["get", "create", "update", "patch", "delete"]
//
// Apply caution when using these operations in production environments.
// =============================================================================

// ScaleDeployment scales a deployment to the specified number of replicas.
// Returns the updated deployment spec.
//
// RBAC Required: apps/deployments - get, patch
func (c *Client) ScaleDeployment(ctx context.Context, namespace, deployment string, replicas int32) (*appsv1.Deployment, error) {
	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	if err := validateName("namespace", namespace); err != nil {
		return nil, err
	}
	if err := validateName("deployment name", deployment); err != nil {
		return nil, err
	}
	if replicas < 0 {
		return nil, fmt.Errorf("replicas cannot be negative")
	}

	var updated *appsv1.Deployment
	// Use backoffRetry to handle resource version conflicts
	err := backoffRetry(ctx, func() error {
		// Always get the latest deployment (needed for each retry)
		deploy, getErr := c.clientset.AppsV1().Deployments(namespace).Get(ctx, deployment, metav1.GetOptions{})
		if getErr != nil {
			return fmt.Errorf("getting deployment: %w", getErr)
		}
		deploy.Spec.Replicas = &replicas
		u, updErr := c.clientset.AppsV1().Deployments(namespace).Update(ctx, deploy, metav1.UpdateOptions{})
		if updErr == nil {
			updated = u
		}
		return updErr
	})
	if err != nil {
		return nil, fmt.Errorf("scaling deployment: %w", err)
	}
	return updated, nil
}

// RolloutRestart triggers a rolling restart of a deployment by patching the
// pod template annotation with the current timestamp.
//
// RBAC Required: apps/deployments - get, patch
func (c *Client) RolloutRestart(ctx context.Context, namespace, deployment string) (*appsv1.Deployment, error) {
	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	if err := validateName("namespace", namespace); err != nil {
		return nil, err
	}
	if err := validateName("deployment name", deployment); err != nil {
		return nil, err
	}

	var updated *appsv1.Deployment
	err := backoffRetry(ctx, func() error {
		// Always get the latest deployment for each retry
		deploy, getErr := c.clientset.AppsV1().Deployments(namespace).Get(ctx, deployment, metav1.GetOptions{})
		if getErr != nil {
			return fmt.Errorf("getting deployment: %w", getErr)
		}
		// Patch annotation
		if deploy.Spec.Template.Annotations == nil {
			deploy.Spec.Template.Annotations = make(map[string]string)
		}
		deploy.Spec.Template.Annotations["kubectl.kubernetes.io/restartedAt"] = time.Now().Format(time.RFC3339)
		u, updErr := c.clientset.AppsV1().Deployments(namespace).Update(ctx, deploy, metav1.UpdateOptions{})
		if updErr == nil {
			updated = u
		}
		return updErr
	})
	if err != nil {
		return nil, fmt.Errorf("restarting deployment: %w", err)
	}
	return updated, nil
}

// ApplyManifest applies a YAML manifest to the cluster.
// Supports ConfigMaps and Secrets. Uses server-side apply semantics.
//
// RBAC Required: Depends on resource type in manifest
func (c *Client) ApplyManifest(ctx context.Context, namespace, manifestYAML string) (*unstructured.Unstructured, error) {
	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	if err := validateName("namespace", namespace); err != nil {
		return nil, err
	}
	if manifestYAML == "" {
		return nil, fmt.Errorf("manifest cannot be empty")
	}
	if c.dynamicClient == nil {
		return nil, fmt.Errorf("dynamic client not initialized (required for apply operations)")
	}

	// Parse YAML into unstructured object
	obj := &unstructured.Unstructured{}
	decoder := yaml.NewYAMLOrJSONDecoder(bytes.NewReader([]byte(manifestYAML)), 4096)
	if err := decoder.Decode(obj); err != nil {
		return nil, fmt.Errorf("parsing manifest: %w", err)
	}

	// Validate we have required fields
	gvk := obj.GroupVersionKind()
	if gvk.Kind == "" {
		return nil, fmt.Errorf("manifest missing kind")
	}
	if obj.GetName() == "" {
		return nil, fmt.Errorf("manifest missing metadata.name")
	}

	// Set namespace if not specified in manifest
	if obj.GetNamespace() == "" {
		obj.SetNamespace(namespace)
	}

	// Determine the GVR for the resource using the shared resolveGVR map,
	// which supports all standard K8s resource types (not just the legacy 4).
	info, err := resolveGVR(gvk.Kind)
	if err != nil {
		return nil, fmt.Errorf("unsupported resource type %s: %w", gvk.Kind, err)
	}
	gvr := info.GVR

	// Check if resource exists
	_, err = c.dynamicClient.Resource(gvr).Namespace(namespace).Get(ctx, obj.GetName(), metav1.GetOptions{})
	if err != nil && !errors.IsNotFound(err) {
		return nil, fmt.Errorf("checking existing resource: %w", err)
	}

	var result *unstructured.Unstructured
	if errors.IsNotFound(err) {
		// Create new resource
		result, err = c.dynamicClient.Resource(gvr).Namespace(namespace).Create(ctx, obj, metav1.CreateOptions{})
		if err != nil {
			return nil, fmt.Errorf("creating resource: %w", err)
		}
	} else {
		// Update existing resource (preserve resourceVersion)
		var updateResult *unstructured.Unstructured
		updateErr := backoffRetry(ctx, func() error {
			// Always get the latest version on retry
			existingLatest, getErr := c.dynamicClient.Resource(gvr).Namespace(namespace).Get(ctx, obj.GetName(), metav1.GetOptions{})
			if getErr != nil {
				return fmt.Errorf("fetching existing resource: %w", getErr)
			}
			obj.SetResourceVersion(existingLatest.GetResourceVersion())
			u, updErr := c.dynamicClient.Resource(gvr).Namespace(namespace).Update(ctx, obj, metav1.UpdateOptions{})
			if updErr == nil {
				updateResult = u
			}
			return updErr
		})
		if updateErr != nil {
			return nil, fmt.Errorf("updating resource: %w", updateErr)
		}
		result = updateResult
	}
	return result, nil
}

// DeleteResource deletes a Kubernetes resource by type and name.
// Supported resource types: pod, configmap, secret, deployment
//
// RBAC Required: Depends on resource type
func (c *Client) DeleteResource(ctx context.Context, namespace, resourceType, name string) error {
	if err := c.waitForRateLimit(ctx); err != nil {
		return err
	}

	if err := validateName("namespace", namespace); err != nil {
		return err
	}
	if resourceType == "" {
		return fmt.Errorf("resource type cannot be empty")
	}
	if err := validateName("resource name", name); err != nil {
		return err
	}

	deletePolicy := metav1.DeletePropagationForeground
	deleteOpts := metav1.DeleteOptions{
		PropagationPolicy: &deletePolicy,
	}

	var err error
	switch resourceType {
	case "pod", "pods":
		err = c.clientset.CoreV1().Pods(namespace).Delete(ctx, name, deleteOpts)
	case "configmap", "configmaps", "cm":
		err = c.clientset.CoreV1().ConfigMaps(namespace).Delete(ctx, name, deleteOpts)
	case "secret", "secrets":
		err = c.clientset.CoreV1().Secrets(namespace).Delete(ctx, name, deleteOpts)
	case "deployment", "deployments", "deploy":
		err = c.clientset.AppsV1().Deployments(namespace).Delete(ctx, name, deleteOpts)
	default:
		return fmt.Errorf("unsupported resource type: %s (supported: pod, configmap, secret, deployment)", resourceType)
	}

	if err != nil {
		if errors.IsNotFound(err) {
			return fmt.Errorf("resource %s/%s not found in namespace %s", resourceType, name, namespace)
		}
		return fmt.Errorf("deleting %s/%s: %w", resourceType, name, err)
	}

	return nil
}

// GetDeployments lists deployments in a namespace with optional label selector.
func (c *Client) GetDeployments(ctx context.Context, namespace, labelSelector string) ([]appsv1.Deployment, error) {
	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	if namespace == "" {
		return nil, fmt.Errorf("namespace cannot be empty")
	}

	opts := metav1.ListOptions{}
	if labelSelector != "" {
		opts.LabelSelector = labelSelector
	}

	deployments, err := c.clientset.AppsV1().Deployments(namespace).List(ctx, opts)
	if err != nil {
		return nil, fmt.Errorf("listing deployments: %w", err)
	}

	return deployments.Items, nil
}

// GetDeploymentsAllNamespaces lists deployments across all namespaces with optional label selector.
// This is useful when the namespace is unknown and needs to be discovered.
func (c *Client) GetDeploymentsAllNamespaces(ctx context.Context, labelSelector string) ([]appsv1.Deployment, error) {
	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	opts := metav1.ListOptions{}
	if labelSelector != "" {
		opts.LabelSelector = labelSelector
	}

	// Empty string namespace means all namespaces
	deployments, err := c.clientset.AppsV1().Deployments("").List(ctx, opts)
	if err != nil {
		return nil, fmt.Errorf("listing deployments across all namespaces: %w", err)
	}

	return deployments.Items, nil
}

// GetServices lists services in a namespace with optional label selector.
func (c *Client) GetServices(ctx context.Context, namespace, labelSelector string) ([]corev1.Service, error) {
	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	if namespace == "" {
		return nil, fmt.Errorf("namespace cannot be empty")
	}

	opts := metav1.ListOptions{}
	if labelSelector != "" {
		opts.LabelSelector = labelSelector
	}

	services, err := c.clientset.CoreV1().Services(namespace).List(ctx, opts)
	if err != nil {
		return nil, fmt.Errorf("listing services: %w", err)
	}

	return services.Items, nil
}

// GetServicesAllNamespaces lists services across all namespaces with optional label selector.
// This is useful when the namespace is unknown and needs to be discovered.
func (c *Client) GetServicesAllNamespaces(ctx context.Context, labelSelector string) ([]corev1.Service, error) {
	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	opts := metav1.ListOptions{}
	if labelSelector != "" {
		opts.LabelSelector = labelSelector
	}

	// Empty string namespace means all namespaces
	services, err := c.clientset.CoreV1().Services("").List(ctx, opts)
	if err != nil {
		return nil, fmt.Errorf("listing services across all namespaces: %w", err)
	}

	return services.Items, nil
}

// GetConfigMaps lists configmaps in a namespace with optional label selector.
func (c *Client) GetConfigMaps(ctx context.Context, namespace, labelSelector string) ([]corev1.ConfigMap, error) {
	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	if namespace == "" {
		return nil, fmt.Errorf("namespace cannot be empty")
	}

	opts := metav1.ListOptions{}
	if labelSelector != "" {
		opts.LabelSelector = labelSelector
	}

	configmaps, err := c.clientset.CoreV1().ConfigMaps(namespace).List(ctx, opts)
	if err != nil {
		return nil, fmt.Errorf("listing configmaps: %w", err)
	}

	return configmaps.Items, nil
}

// GetSecrets lists secrets in a namespace with optional label selector.
// SECURITY: Returns Secret objects but formatters MUST only expose metadata, NEVER data values.
func (c *Client) GetSecrets(ctx context.Context, namespace, labelSelector string) ([]corev1.Secret, error) {
	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	if namespace == "" {
		return nil, fmt.Errorf("namespace cannot be empty")
	}

	opts := metav1.ListOptions{}
	if labelSelector != "" {
		opts.LabelSelector = labelSelector
	}

	secrets, err := c.clientset.CoreV1().Secrets(namespace).List(ctx, opts)
	if err != nil {
		return nil, fmt.Errorf("listing secrets: %w", err)
	}

	return secrets.Items, nil
}

// GetResources lists or gets K8s resources using the dynamic client.
// If name is provided, returns a single resource. Otherwise lists resources.
// For cluster-scoped resources, namespace is ignored.
func (c *Client) GetResources(
	ctx context.Context,
	resourceType, namespace, name, labelSelector, fieldSelector string,
	limit int64,
) ([]unstructured.Unstructured, error) {
	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}
	if c.dynamicClient == nil {
		return nil, fmt.Errorf("dynamic client not initialized")
	}

	info, err := resolveGVR(resourceType)
	if err != nil {
		return nil, err
	}

	// Single resource by name
	if name != "" {
		ns := namespace
		if !info.Namespaced {
			ns = ""
		}
		var resource dynamic.ResourceInterface
		if info.Namespaced && ns != "" {
			resource = c.dynamicClient.Resource(info.GVR).Namespace(ns)
		} else {
			resource = c.dynamicClient.Resource(info.GVR)
		}
		obj, err := resource.Get(ctx, name, metav1.GetOptions{})
		if err != nil {
			return nil, fmt.Errorf("getting %s/%s: %w", resourceType, name, err)
		}
		return []unstructured.Unstructured{*obj}, nil
	}

	// List resources
	opts := metav1.ListOptions{}
	if labelSelector != "" {
		opts.LabelSelector = labelSelector
	}
	if fieldSelector != "" {
		opts.FieldSelector = fieldSelector
	}
	if limit > 0 {
		opts.Limit = limit
	}

	var resource dynamic.ResourceInterface
	if info.Namespaced && namespace != "" {
		resource = c.dynamicClient.Resource(info.GVR).Namespace(namespace)
	} else {
		resource = c.dynamicClient.Resource(info.GVR)
	}

	list, err := resource.List(ctx, opts)
	if err != nil {
		return nil, fmt.Errorf("listing %s: %w", resourceType, err)
	}

	return list.Items, nil
}

// DescribeResource gets a single K8s resource with full detail using the dynamic client.
// For cluster-scoped resources, namespace is ignored.
func (c *Client) DescribeResource(
	ctx context.Context,
	resourceType, name, namespace string,
) (*unstructured.Unstructured, error) {
	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}
	if c.dynamicClient == nil {
		return nil, fmt.Errorf("dynamic client not initialized")
	}
	if name == "" {
		return nil, fmt.Errorf("resource name is required")
	}

	info, err := resolveGVR(resourceType)
	if err != nil {
		return nil, err
	}

	var resource dynamic.ResourceInterface
	if info.Namespaced && namespace != "" {
		resource = c.dynamicClient.Resource(info.GVR).Namespace(namespace)
	} else {
		resource = c.dynamicClient.Resource(info.GVR)
	}

	obj, err := resource.Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		return nil, fmt.Errorf("describing %s/%s: %w", resourceType, name, err)
	}

	return obj, nil
}

// Exponential backoff/retry helper for update/patch conflicts
const (
	maxBackoffRetries = 5
	minBackoffDelay   = 50 * time.Millisecond
	maxBackoffDelay   = 1 * time.Second
)

// backoffRetry retries the given operation on resource version conflicts with exponential backoff.
// Cancellation via context is respected.
func backoffRetry(ctx context.Context, op func() error) error {
	delay := minBackoffDelay
	for i := 0; i < maxBackoffRetries; i++ {
		err := op()
		if err == nil {
			return nil
		}
		if errors.IsConflict(err) {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(delay):
			}
			if delay < maxBackoffDelay {
				delay *= 2
				if delay > maxBackoffDelay {
					delay = maxBackoffDelay
				}
			}
			continue
		}
		return err
	}
	// Last attempt
	return op()
}

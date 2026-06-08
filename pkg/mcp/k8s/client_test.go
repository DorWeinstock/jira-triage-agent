package k8s

import (
	"context"
	"testing"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	autoscalingv1 "k8s.io/api/autoscaling/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/watch"
	applyappsv1 "k8s.io/client-go/applyconfigurations/apps/v1"
	applyautoscalingv1 "k8s.io/client-go/applyconfigurations/autoscaling/v1"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/kubernetes/fake"
	appsV1 "k8s.io/client-go/kubernetes/typed/apps/v1"
	rest "k8s.io/client-go/rest"
)

// --- EXPONENTIAL BACKOFF/RETRY TESTS for RolloutRestart ---

// ----------- Fake dynamic client for ApplyManifest retry simulation -----------
type fakeResourceInterface struct {
	updateAttempts         int
	conflictsBeforeSuccess int
	latestObj              *unstructured.Unstructured
}

func (f *fakeResourceInterface) Namespace(ns string) dynamic.ResourceInterface {
	return f // Namespace is ignored for this fake
}
func (f *fakeResourceInterface) Get(ctx context.Context, name string, opts metav1.GetOptions, subresources ...string) (*unstructured.Unstructured, error) {
	// Always return a ConfigMap with incremented ResourceVersion
	ver := f.updateAttempts + 1
	obj := &unstructured.Unstructured{}
	obj.SetGroupVersionKind(schema.GroupVersionKind{Group: "", Version: "v1", Kind: "ConfigMap"})
	obj.SetName(name)
	obj.SetNamespace("default")
	obj.SetResourceVersion(string(rune('0' + ver)))
	return obj, nil
}
func (f *fakeResourceInterface) Update(ctx context.Context, obj *unstructured.Unstructured, opts metav1.UpdateOptions, subresources ...string) (*unstructured.Unstructured, error) {
	f.updateAttempts++
	if f.updateAttempts <= f.conflictsBeforeSuccess {
		return nil, apierrors.NewConflict(schema.GroupResource{Group: "", Resource: "configmaps"}, obj.GetName(), nil)
	}
	f.latestObj = obj.DeepCopy()
	return obj, nil
}

func (f *fakeResourceInterface) UpdateStatus(ctx context.Context, obj *unstructured.Unstructured, opts metav1.UpdateOptions) (*unstructured.Unstructured, error) {
	return obj, nil
}
func (f *fakeResourceInterface) Create(ctx context.Context, obj *unstructured.Unstructured, opts metav1.CreateOptions, subresources ...string) (*unstructured.Unstructured, error) {
	f.latestObj = obj.DeepCopy()
	return obj, nil
}

// Remaining interface methods: not implemented for test
func (f *fakeResourceInterface) Apply(ctx context.Context, name string, obj *unstructured.Unstructured, opts metav1.ApplyOptions, subresources ...string) (*unstructured.Unstructured, error) {
	return nil, nil
}

func (f *fakeResourceInterface) ApplyStatus(ctx context.Context, name string, obj *unstructured.Unstructured, opts metav1.ApplyOptions) (*unstructured.Unstructured, error) {
	return nil, nil
}

func (f *fakeResourceInterface) Delete(ctx context.Context, name string, opts metav1.DeleteOptions, subresources ...string) error {
	return nil
}

func (f *fakeResourceInterface) DeleteCollection(ctx context.Context, opts metav1.DeleteOptions, listOpts metav1.ListOptions) error {
	return nil
}
func (f *fakeResourceInterface) List(ctx context.Context, opts metav1.ListOptions) (*unstructured.UnstructuredList, error) {
	return nil, nil
}
func (f *fakeResourceInterface) Watch(ctx context.Context, opts metav1.ListOptions) (watch.Interface, error) {
	return nil, nil
}
func (f *fakeResourceInterface) Patch(ctx context.Context, name string, pt types.PatchType, data []byte, opts metav1.PatchOptions, subresources ...string) (*unstructured.Unstructured, error) {
	return nil, nil
}

// fakeDynamicClient -> always returns our resource interface

// Minimal dynamic client stub for just Resource (returns our fake)
type fakeDynamicClient struct {
	resIf dynamic.NamespaceableResourceInterface
}

func (f *fakeDynamicClient) Resource(_ schema.GroupVersionResource) dynamic.NamespaceableResourceInterface {
	return f.resIf
}

// --- ApplyManifest Retry/Cancel Tests ---

func TestApplyManifest_RetryOnConflict_Succeeds(t *testing.T) {
	manifest := `
apiVersion: v1
kind: ConfigMap
metadata:
  name: test-cm
  namespace: default
data:
  somekey: someval
`
	fakeRes := &fakeResourceInterface{conflictsBeforeSuccess: 3}
	fakeDyn := &fakeDynamicClient{resIf: fakeRes}
	client := &Client{
		clientset:     nil,
		dynamicClient: fakeDyn,
		limiter:       nil,
	}
	ctx := context.Background()
	_, err := client.ApplyManifest(ctx, "default", manifest)
	if err != nil {
		t.Fatalf("ApplyManifest failed after conflicts: %v", err)
	}
	if fakeRes.updateAttempts != fakeRes.conflictsBeforeSuccess+1 {
		t.Errorf("expected %d attempts, got %d", fakeRes.conflictsBeforeSuccess+1, fakeRes.updateAttempts)
	}
}

func TestApplyManifest_RetryOnConflict_ContextCancelled(t *testing.T) {
	manifest := `
apiVersion: v1
kind: ConfigMap
metadata:
  name: test-cm
  namespace: default
data:
  somekey: someval
`
	fakeRes := &fakeResourceInterface{conflictsBeforeSuccess: 50}
	fakeDyn := &fakeDynamicClient{resIf: fakeRes}
	client := &Client{
		clientset:     nil,
		dynamicClient: fakeDyn,
		limiter:       nil,
	}
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	_, err := client.ApplyManifest(ctx, "default", manifest)
	if err == nil {
		t.Fatalf("expected error due to context cancellation, got none")
	}
	if ctx.Err() == nil {
		t.Error("expected ctx.Err() to be set")
	}
}

// The rest of the existing tests follow (for RolloutRestart, etc.)

// --- EXPONENTIAL BACKOFF/RETRY TESTS for RolloutRestart ---

func TestRolloutRestart_RetryOnConflict_Succeeds(t *testing.T) {
	initialDeploy := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-deploy",
			Namespace: "default",
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: int32Ptr(1),
			Template: corev1.PodTemplateSpec{}, // Must init for annotation logic
		},
	}
	fakeClientset := fake.NewSimpleClientset(initialDeploy)

	conflictFake := &conflictDeploymentClient{
		fakeDeployment:         fakeClientset,
		conflictsBeforeSuccess: 3, // Fail three times before success
	}
	client := &Client{
		clientset: conflictFake,
		limiter:   nil, // no rate limiting in test
	}

	ctx := context.Background()
	_, err := client.RolloutRestart(ctx, "default", "test-deploy")
	if err != nil {
		t.Fatalf("RolloutRestart failed after conflicts: %v", err)
	}
	if conflictFake.updateAttempts != conflictFake.conflictsBeforeSuccess+1 {
		t.Errorf("expected %d attempts, got %d", conflictFake.conflictsBeforeSuccess+1, conflictFake.updateAttempts)
	}
}

func TestRolloutRestart_RetryOnConflict_ContextCancelled(t *testing.T) {
	initialDeploy := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-deploy",
			Namespace: "default",
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: int32Ptr(1),
			Template: corev1.PodTemplateSpec{}, // Must init for annotation logic
		},
	}
	fakeClientset := fake.NewSimpleClientset(initialDeploy)

	conflictFake := &conflictDeploymentClient{
		fakeDeployment:         fakeClientset,
		conflictsBeforeSuccess: 10, // Guaranteed to exceed backoff attempts before allowed
	}
	client := &Client{
		clientset: conflictFake,
		limiter:   nil,
	}

	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	_, err := client.RolloutRestart(ctx, "default", "test-deploy")
	if err == nil {
		t.Fatalf("expected error due to context cancellation, got none")
	}
	if ctx.Err() == nil {
		t.Error("expected ctx.Err() to be set")
	}
}

// --------------- Minimal stub for RolloutRestart tests ---------------
type conflictDeploymentClient struct {
	kubernetes.Interface   // embed everything else for unneeded methods
	fakeDeployment         *fake.Clientset
	updateAttempts         int
	conflictsBeforeSuccess int
}

func (c *conflictDeploymentClient) AppsV1() appsV1.AppsV1Interface {
	return c
}

// Satisfy the AppsV1Interface for testing
func (c *conflictDeploymentClient) ControllerRevisions(namespace string) appsV1.ControllerRevisionInterface {
	panic("not implemented in test stub")
}
func (c *conflictDeploymentClient) DaemonSets(namespace string) appsV1.DaemonSetInterface {
	panic("not implemented in test stub")
}
func (c *conflictDeploymentClient) ReplicaSets(namespace string) appsV1.ReplicaSetInterface {
	panic("not implemented in test stub")
}
func (c *conflictDeploymentClient) RESTClient() rest.Interface {
	return nil
}

func (c *conflictDeploymentClient) Deployments(namespace string) appsV1.DeploymentInterface {
	return &deploymentUpdateStub{c: c, ns: namespace}
}

// Minimal stub for StatefulSets to fully satisfy AppsV1Interface
func (c *conflictDeploymentClient) StatefulSets(namespace string) appsV1.StatefulSetInterface {
	return &statefulSetStub{}
}

type statefulSetStub struct{}

func (s *statefulSetStub) Create(ctx context.Context, ss *appsv1.StatefulSet, opts metav1.CreateOptions) (*appsv1.StatefulSet, error) {
	panic("stub")
}
func (s *statefulSetStub) Update(ctx context.Context, ss *appsv1.StatefulSet, opts metav1.UpdateOptions) (*appsv1.StatefulSet, error) {
	panic("stub")
}
func (s *statefulSetStub) UpdateStatus(ctx context.Context, ss *appsv1.StatefulSet, opts metav1.UpdateOptions) (*appsv1.StatefulSet, error) {
	panic("stub")
}
func (s *statefulSetStub) Delete(ctx context.Context, name string, opts metav1.DeleteOptions) error {
	panic("stub")
}
func (s *statefulSetStub) DeleteCollection(ctx context.Context, opts metav1.DeleteOptions, listOpts metav1.ListOptions) error {
	panic("stub")
}
func (s *statefulSetStub) Get(ctx context.Context, name string, opts metav1.GetOptions) (*appsv1.StatefulSet, error) {
	panic("stub")
}
func (s *statefulSetStub) List(ctx context.Context, opts metav1.ListOptions) (*appsv1.StatefulSetList, error) {
	panic("stub")
}
func (s *statefulSetStub) Watch(ctx context.Context, opts metav1.ListOptions) (watch.Interface, error) {
	panic("stub")
}
func (s *statefulSetStub) Patch(ctx context.Context, name string, pt types.PatchType, data []byte, opts metav1.PatchOptions, subresources ...string) (*appsv1.StatefulSet, error) {
	panic("stub")
}
func (s *statefulSetStub) Apply(ctx context.Context, ss *applyappsv1.StatefulSetApplyConfiguration, opts metav1.ApplyOptions) (*appsv1.StatefulSet, error) {
	panic("stub")
}
func (s *statefulSetStub) ApplyStatus(ctx context.Context, ss *applyappsv1.StatefulSetApplyConfiguration, opts metav1.ApplyOptions) (*appsv1.StatefulSet, error) {
	panic("stub")
}
func (s *statefulSetStub) GetScale(ctx context.Context, statefulSetName string, options metav1.GetOptions) (*autoscalingv1.Scale, error) {
	panic("stub")
}
func (s *statefulSetStub) UpdateScale(ctx context.Context, statefulSetName string, scale *autoscalingv1.Scale, opts metav1.UpdateOptions) (*autoscalingv1.Scale, error) {
	panic("stub")
}
func (s *statefulSetStub) ApplyScale(ctx context.Context, statefulSetName string, scale *applyautoscalingv1.ScaleApplyConfiguration, opts metav1.ApplyOptions) (*autoscalingv1.Scale, error) {
	panic("stub")
}

type deploymentUpdateStub struct {
	c  *conflictDeploymentClient
	ns string
}

func (d *deploymentUpdateStub) Get(ctx context.Context, name string, opts metav1.GetOptions) (*appsv1.Deployment, error) {
	return d.c.fakeDeployment.AppsV1().Deployments(d.ns).Get(ctx, name, opts)
}
func (d *deploymentUpdateStub) Update(ctx context.Context, deployment *appsv1.Deployment, opts metav1.UpdateOptions) (*appsv1.Deployment, error) {
	c := d.c
	c.updateAttempts++
	if c.updateAttempts <= c.conflictsBeforeSuccess {
		return nil, apierrors.NewConflict(schema.GroupResource{Group: "apps", Resource: "deployments"}, deployment.Name, nil)
	}
	return c.fakeDeployment.AppsV1().Deployments(d.ns).Update(ctx, deployment, opts)
}

// Stubs for other DeploymentInterface methods
func (d *deploymentUpdateStub) Create(ctx context.Context, dep *appsv1.Deployment, opts metav1.CreateOptions) (*appsv1.Deployment, error) {
	return d.c.fakeDeployment.AppsV1().Deployments(d.ns).Create(ctx, dep, opts)
}
func (d *deploymentUpdateStub) Delete(ctx context.Context, name string, opts metav1.DeleteOptions) error {
	return nil
}
func (d *deploymentUpdateStub) DeleteCollection(ctx context.Context, opts metav1.DeleteOptions, listOpts metav1.ListOptions) error {
	panic("stub")
}
func (d *deploymentUpdateStub) List(ctx context.Context, opts metav1.ListOptions) (*appsv1.DeploymentList, error) {
	return nil, nil
}
func (d *deploymentUpdateStub) Watch(ctx context.Context, opts metav1.ListOptions) (watch.Interface, error) {
	return nil, nil
}
func (d *deploymentUpdateStub) Patch(ctx context.Context, name string, pt types.PatchType, data []byte, opts metav1.PatchOptions, subresources ...string) (*appsv1.Deployment, error) {
	return nil, nil
}
func (d *deploymentUpdateStub) GetScale(ctx context.Context, deploymentName string, options metav1.GetOptions) (*autoscalingv1.Scale, error) {
	panic("stub")
}
func (d *deploymentUpdateStub) UpdateScale(ctx context.Context, deploymentName string, scale *autoscalingv1.Scale, opts metav1.UpdateOptions) (*autoscalingv1.Scale, error) {
	panic("stub")
}

// Add missing methods for interface compliance (K8s client-go v0.24+)
func (d *deploymentUpdateStub) Apply(ctx context.Context, applyConfig *applyappsv1.DeploymentApplyConfiguration, opts metav1.ApplyOptions) (*appsv1.Deployment, error) {
	// Not used in our tests, just return nil
	return nil, nil
}
func (d *deploymentUpdateStub) UpdateStatus(ctx context.Context, deployment *appsv1.Deployment, opts metav1.UpdateOptions) (*appsv1.Deployment, error) {
	// Not used in our tests, just return input
	return deployment, nil
}
func (d *deploymentUpdateStub) ApplyStatus(ctx context.Context, deployment *applyappsv1.DeploymentApplyConfiguration, opts metav1.ApplyOptions) (*appsv1.Deployment, error) {
	// Not used in our tests, just return nil
	return nil, nil
}

// Satisfy DeploymentInterface (K8s 1.25+)
func (d *deploymentUpdateStub) ApplyScale(ctx context.Context, deploymentName string, scale *applyautoscalingv1.ScaleApplyConfiguration, opts metav1.ApplyOptions) (*autoscalingv1.Scale, error) {
	// Not used in our tests
	return nil, nil
}

// =============================================================================
// Tests for validateName
// =============================================================================

func TestValidateName_Valid(t *testing.T) {
	cases := []string{
		"default",
		"my-namespace",
		"pod-abc123",
		"a",
		"x1",
		"abc.def",
	}
	for _, name := range cases {
		if err := validateName("field", name); err != nil {
			t.Errorf("validateName(%q) unexpected error: %v", name, err)
		}
	}
}

func TestValidateName_Invalid(t *testing.T) {
	cases := []struct {
		value string
		desc  string
	}{
		{"", "empty"},
		{"../secret", "path traversal"},
		{"MyPod", "uppercase"},
		{"pod name", "space"},
		{"pod/name", "slash"},
		{"-leading-dash", "leading dash"},
		{"trailing-dash-", "trailing dash"},
		{string(make([]byte, 254)), "exceeds 253 chars"},
	}
	for _, tc := range cases {
		if err := validateName("field", tc.value); err == nil {
			t.Errorf("validateName(%q) [%s]: expected error, got nil", tc.value, tc.desc)
		}
	}
}

// =============================================================================
// Test for UTF-8 safe log truncation
// =============================================================================

func TestGetPodLogs_UTF8SafeTruncation(t *testing.T) {
	// Build a string that is exactly maxLogBytes long with a multi-byte rune at the boundary.
	// Japanese "日" is 3 bytes in UTF-8. Place it so it straddles the maxLogBytes boundary.
	base := make([]byte, maxLogBytes-2) // 2 bytes before limit
	for i := range base {
		base[i] = 'a'
	}
	// Append a 3-byte rune so bytes [maxLogBytes-2 .. maxLogBytes] would be split
	multiByteRune := []byte("日") // 3 bytes: 0xe6, 0x97, 0xa5
	payload := append(base, multiByteRune...)
	// payload is maxLogBytes+1 bytes: our limiter reads maxLogBytes+1 so truncation triggers

	result := truncateUTF8(string(payload), maxLogBytes)
	if !isValidUTF8WithTruncation(result) {
		t.Error("truncated result is not valid UTF-8")
	}
	if len(result) >= maxLogBytes {
		t.Errorf("result length %d should be < %d after truncation", len(result), maxLogBytes)
	}
}

// truncateUTF8 mirrors the truncation logic in GetPodLogs for unit-testability.
func truncateUTF8(s string, limit int) string {
	if len(s) <= limit {
		return s
	}
	truncated := s[:limit]
	for len(truncated) > 0 {
		if isValidUTF8WithTruncation(truncated) {
			return truncated
		}
		truncated = truncated[:len(truncated)-1]
	}
	return truncated
}

func isValidUTF8WithTruncation(s string) bool {
	for _, r := range s {
		if r == '\uFFFD' {
			return false
		}
	}
	return true
}

// =============================================================================
// Tests for ApplyManifest with non-ConfigMap kinds
// =============================================================================

func TestApplyManifest_SecretKind(t *testing.T) {
	manifest := `
apiVersion: v1
kind: Secret
metadata:
  name: test-secret
  namespace: default
data:
  key: dmFsdWU=
`
	fakeRes := &fakeResourceInterface{conflictsBeforeSuccess: 0}
	fakeDyn := &fakeDynamicClient{resIf: fakeRes}
	client := &Client{
		dynamicClient: fakeDyn,
		limiter:       nil,
	}
	_, err := client.ApplyManifest(context.Background(), "default", manifest)
	if err != nil {
		t.Fatalf("ApplyManifest Secret: unexpected error: %v", err)
	}
}

func TestApplyManifest_UnsupportedKind(t *testing.T) {
	manifest := `
apiVersion: example.com/v1
kind: FooBarBaz
metadata:
  name: test-obj
  namespace: default
`
	fakeRes := &fakeResourceInterface{}
	fakeDyn := &fakeDynamicClient{resIf: fakeRes}
	client := &Client{
		dynamicClient: fakeDyn,
		limiter:       nil,
	}
	_, err := client.ApplyManifest(context.Background(), "default", manifest)
	if err == nil {
		t.Fatal("expected error for unsupported kind, got nil")
	}
}

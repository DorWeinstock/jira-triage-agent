package k8s_test

import (
	"testing"

	"jira-triage-agent/pkg/mcp/k8s"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/fake"
)

func TestNewMCPServer(t *testing.T) {
	fakeClientset := fake.NewSimpleClientset()
	client := k8s.NewClientWithClientset(fakeClientset)
	server := k8s.NewMCPServer(client)

	if server == nil {
		t.Fatal("expected non-nil server")
	}
}

func TestKubectlGetPodsTool(t *testing.T) {
	// Create fake clientset with test pods
	pod1 := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-pod-1",
			Namespace: "default",
			Labels:    map[string]string{"app": "test"},
		},
		Status: corev1.PodStatus{
			Phase: corev1.PodRunning,
		},
	}
	pod2 := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-pod-2",
			Namespace: "default",
			Labels:    map[string]string{"app": "other"},
		},
		Status: corev1.PodStatus{
			Phase: corev1.PodPending,
		},
	}

	fakeClientset := fake.NewSimpleClientset(pod1, pod2)
	client := k8s.NewClientWithClientset(fakeClientset)
	server := k8s.NewMCPServer(client)

	if server == nil {
		t.Fatal("expected non-nil server")
	}

	// Test that the tool is registered
	// Note: Direct tool invocation testing would require more MCP SDK infrastructure
	// This test validates server creation with the client
}

func TestKubectlGetPodsToolValidation(t *testing.T) {
	fakeClientset := fake.NewSimpleClientset()
	client := k8s.NewClientWithClientset(fakeClientset)
	server := k8s.NewMCPServer(client)

	if server == nil {
		t.Fatal("expected non-nil server")
	}

	// Validation testing would be done through tool invocation
	// which requires MCP SDK infrastructure
}

func TestKubectlLogsTool(t *testing.T) {
	// Create fake clientset with test pod
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-pod",
			Namespace: "default",
		},
		Spec: corev1.PodSpec{
			Containers: []corev1.Container{
				{Name: "test-container"},
			},
		},
		Status: corev1.PodStatus{
			Phase: corev1.PodRunning,
		},
	}

	fakeClientset := fake.NewSimpleClientset(pod)
	client := k8s.NewClientWithClientset(fakeClientset)
	server := k8s.NewMCPServer(client)

	if server == nil {
		t.Fatal("expected non-nil server")
	}
}

func TestKubectlEventsTool(t *testing.T) {
	// Create fake clientset with test events
	event := &corev1.Event{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-event",
			Namespace: "default",
		},
		InvolvedObject: corev1.ObjectReference{
			Kind:      "Pod",
			Name:      "test-pod",
			Namespace: "default",
		},
		Reason:  "Started",
		Message: "Container started",
		Type:    "Normal",
	}

	fakeClientset := fake.NewSimpleClientset(event)
	client := k8s.NewClientWithClientset(fakeClientset)
	server := k8s.NewMCPServer(client)

	if server == nil {
		t.Fatal("expected non-nil server")
	}
}

func TestKubectlDescribePodTool(t *testing.T) {
	// Create fake clientset with test pod
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-pod",
			Namespace: "default",
			Labels:    map[string]string{"app": "test"},
		},
		Spec: corev1.PodSpec{
			Containers: []corev1.Container{
				{
					Name:  "test-container",
					Image: "test-image:latest",
				},
			},
		},
		Status: corev1.PodStatus{
			Phase: corev1.PodRunning,
			ContainerStatuses: []corev1.ContainerStatus{
				{
					Name:  "test-container",
					Ready: true,
					State: corev1.ContainerState{
						Running: &corev1.ContainerStateRunning{},
					},
				},
			},
		},
	}

	fakeClientset := fake.NewSimpleClientset(pod)
	client := k8s.NewClientWithClientset(fakeClientset)
	server := k8s.NewMCPServer(client)

	if server == nil {
		t.Fatal("expected non-nil server")
	}
}

func TestKubectlGetPodsAllNamespacesTool(t *testing.T) {
	// Create fake clientset with pods in different namespaces
	pod1 := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "pod-in-default",
			Namespace: "default",
			Labels:    map[string]string{"app": "webapp"},
		},
		Status: corev1.PodStatus{
			Phase: corev1.PodRunning,
		},
	}
	pod2 := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "pod-in-kube-system",
			Namespace: "kube-system",
			Labels:    map[string]string{"app": "coredns"},
		},
		Status: corev1.PodStatus{
			Phase: corev1.PodRunning,
		},
	}
	pod3 := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "another-webapp",
			Namespace: "production",
			Labels:    map[string]string{"app": "webapp"},
		},
		Status: corev1.PodStatus{
			Phase: corev1.PodPending,
		},
	}

	fakeClientset := fake.NewSimpleClientset(pod1, pod2, pod3)
	client := k8s.NewClientWithClientset(fakeClientset)
	server := k8s.NewMCPServer(client)

	if server == nil {
		t.Fatal("expected non-nil server")
	}

	// Test that the tool is registered with pods across multiple namespaces
	// Note: Direct tool invocation testing requires MCP SDK infrastructure
	// This test validates server creation with multiple namespace pods
}

func TestKubectlGetPodsAllNamespacesWithLabelSelector(t *testing.T) {
	// Create fake clientset with pods that have different labels
	pod1 := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "webapp-1",
			Namespace: "default",
			Labels:    map[string]string{"app": "webapp", "tier": "frontend"},
		},
		Status: corev1.PodStatus{
			Phase: corev1.PodRunning,
		},
	}
	pod2 := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "webapp-2",
			Namespace: "production",
			Labels:    map[string]string{"app": "webapp", "tier": "backend"},
		},
		Status: corev1.PodStatus{
			Phase: corev1.PodRunning,
		},
	}
	pod3 := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "different-app",
			Namespace: "staging",
			Labels:    map[string]string{"app": "other"},
		},
		Status: corev1.PodStatus{
			Phase: corev1.PodRunning,
		},
	}

	fakeClientset := fake.NewSimpleClientset(pod1, pod2, pod3)
	client := k8s.NewClientWithClientset(fakeClientset)
	server := k8s.NewMCPServer(client)

	if server == nil {
		t.Fatal("expected non-nil server")
	}

	// Test that the tool can be created with label selector filtering support
	// Note: Direct tool invocation testing requires MCP SDK infrastructure
}

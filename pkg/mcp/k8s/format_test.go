package k8s

import (
	"strings"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func TestFormatPodList_EmptyList(t *testing.T) {
	pods := []corev1.Pod{}
	result := formatPodList(pods)

	if !strings.Contains(result, "No pods found") {
		t.Errorf("expected 'No pods found' in output, got: %s", result)
	}
}

func TestFormatPodList_SinglePod(t *testing.T) {
	pods := []corev1.Pod{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "test-pod",
				Namespace: "default",
				Labels: map[string]string{
					"app": "test",
				},
			},
			Status: corev1.PodStatus{
				Phase: corev1.PodRunning,
			},
		},
	}

	result := formatPodList(pods)

	// Verify output contains expected information
	if !strings.Contains(result, "Found 1 pod(s)") {
		t.Errorf("expected '1 pod(s)' in output")
	}
	if !strings.Contains(result, "NAME: test-pod") {
		t.Errorf("expected pod name in output")
	}
	if !strings.Contains(result, "NAMESPACE: default") {
		t.Errorf("expected namespace in output")
	}
	if !strings.Contains(result, "STATUS: Running") {
		t.Errorf("expected status in output")
	}
	if !strings.Contains(result, "app: test") {
		t.Errorf("expected label in output")
	}
}

func TestFormatPodList_WithSortedLabels(t *testing.T) {
	pods := []corev1.Pod{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "test-pod",
				Namespace: "default",
				Labels: map[string]string{
					"zebra":  "last",
					"apple":  "first",
					"banana": "second",
				},
			},
			Status: corev1.PodStatus{
				Phase: corev1.PodRunning,
			},
		},
	}

	result := formatPodList(pods)

	// Verify labels are sorted alphabetically (apple before banana before zebra)
	appleIndex := strings.Index(result, "apple: first")
	bananaIndex := strings.Index(result, "banana: second")
	zebraIndex := strings.Index(result, "zebra: last")

	if appleIndex == -1 || bananaIndex == -1 || zebraIndex == -1 {
		t.Errorf("not all labels found in output")
	}

	if appleIndex > bananaIndex {
		t.Errorf("labels not sorted: apple should appear before banana")
	}
	if bananaIndex > zebraIndex {
		t.Errorf("labels not sorted: banana should appear before zebra")
	}
}

func TestFormatPodList_MultiplePodsWithLabels(t *testing.T) {
	pods := []corev1.Pod{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "test-pod-1",
				Namespace: "default",
				Labels: map[string]string{
					"app":     "test",
					"version": "v1",
					"env":     "prod",
				},
			},
			Status: corev1.PodStatus{
				Phase: corev1.PodRunning,
			},
		},
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "test-pod-2",
				Namespace: "default",
				Labels: map[string]string{
					"app": "other",
				},
			},
			Status: corev1.PodStatus{
				Phase: corev1.PodPending,
			},
		},
	}

	result := formatPodList(pods)

	// Verify output contains both pods
	if !strings.Contains(result, "Found 2 pod(s)") {
		t.Errorf("expected '2 pod(s)' in output")
	}
	if !strings.Contains(result, "test-pod-1") {
		t.Errorf("expected first pod in output")
	}
	if !strings.Contains(result, "test-pod-2") {
		t.Errorf("expected second pod in output")
	}

	// Verify labels are sorted for first pod (app, env, version alphabetically)
	appIndex := strings.Index(result, "app: test")
	envIndex := strings.Index(result, "env: prod")
	versionIndex := strings.Index(result, "version: v1")

	if appIndex > envIndex || envIndex > versionIndex {
		t.Errorf("labels not sorted alphabetically for first pod")
	}
}

func TestFormatPodList_NoLabels(t *testing.T) {
	pods := []corev1.Pod{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "test-pod",
				Namespace: "default",
				// No labels
			},
			Status: corev1.PodStatus{
				Phase: corev1.PodRunning,
			},
		},
	}

	result := formatPodList(pods)

	// Should not contain LABELS section
	if strings.Contains(result, "LABELS:") {
		t.Errorf("expected no LABELS section for pod without labels")
	}
	if !strings.Contains(result, "NAME: test-pod") {
		t.Errorf("expected pod name in output")
	}
}

func TestFormatEventList_EmptyList(t *testing.T) {
	events := []corev1.Event{}
	result := formatEventList(events)

	if !strings.Contains(result, "No events found") {
		t.Errorf("expected 'No events found' in output, got: %s", result)
	}
}

func TestFormatEventList_WithEvents(t *testing.T) {
	now := metav1.NewTime(time.Date(2025, 1, 15, 10, 30, 45, 0, time.UTC))
	events := []corev1.Event{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "test-event",
				Namespace: "default",
			},
			InvolvedObject: corev1.ObjectReference{
				Kind:      "Pod",
				Name:      "test-pod",
				Namespace: "default",
			},
			Reason:         "Started",
			Message:        "Container started",
			Type:           "Normal",
			FirstTimestamp: now,
			LastTimestamp:  now,
			Count:          1,
		},
	}

	result := formatEventList(events)

	// Verify output contains expected information
	if !strings.Contains(result, "Found 1 event(s)") {
		t.Errorf("expected '1 event(s)' in output")
	}
	if !strings.Contains(result, "TYPE: Normal") {
		t.Errorf("expected event type in output")
	}
	if !strings.Contains(result, "REASON: Started") {
		t.Errorf("expected event reason in output")
	}
	if !strings.Contains(result, "MESSAGE: Container started") {
		t.Errorf("expected event message in output")
	}
	if !strings.Contains(result, "OBJECT: Pod/test-pod") {
		t.Errorf("expected involved object in output")
	}
	if !strings.Contains(result, "2025-01-15 10:30:45") {
		t.Errorf("expected formatted timestamp in output")
	}
	if !strings.Contains(result, "COUNT: 1") {
		t.Errorf("expected event count in output")
	}
}

func TestFormatEventList_ZeroTimestamps(t *testing.T) {
	events := []corev1.Event{
		{
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
			// FirstTimestamp and LastTimestamp are zero
			Count: 1,
		},
	}

	result := formatEventList(events)

	// Should not contain timestamp fields when they are zero
	if strings.Contains(result, "FIRST SEEN:") {
		t.Errorf("expected no FIRST SEEN for zero timestamp")
	}
	if strings.Contains(result, "LAST SEEN:") {
		t.Errorf("expected no LAST SEEN for zero timestamp")
	}
	// Should still contain other fields
	if !strings.Contains(result, "REASON: Started") {
		t.Errorf("expected event reason in output")
	}
}

func TestFormatEventList_TimeFormatConsistency(t *testing.T) {
	now := metav1.NewTime(time.Date(2025, 1, 15, 10, 30, 45, 0, time.UTC))
	events := []corev1.Event{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "test-event",
				Namespace: "default",
			},
			InvolvedObject: corev1.ObjectReference{
				Kind: "Pod",
				Name: "test-pod",
			},
			Reason:         "Started",
			Message:        "Container started",
			Type:           "Normal",
			FirstTimestamp: now,
			LastTimestamp:  now,
			Count:          1,
		},
	}

	result := formatEventList(events)

	// Verify timestamp format matches timeFormat constant
	expectedTime := "2025-01-15 10:30:45"
	if !strings.Contains(result, expectedTime) {
		t.Errorf("expected timestamp format %s in output", expectedTime)
	}
}

func TestFormatPodDescription_BasicPod(t *testing.T) {
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-pod",
			Namespace: "default",
			Labels: map[string]string{
				"app":     "test",
				"version": "v1",
			},
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
			Phase:  corev1.PodRunning,
			HostIP: "192.168.1.1",
			PodIP:  "10.0.0.1",
		},
	}

	result := formatPodDescription(pod)

	// Verify output contains expected sections
	if !strings.Contains(result, "Pod: default/test-pod") {
		t.Errorf("expected pod header in output")
	}
	if !strings.Contains(result, "Phase: Running") {
		t.Errorf("expected phase in output")
	}
	if !strings.Contains(result, "Host IP: 192.168.1.1") {
		t.Errorf("expected host IP in output")
	}
	if !strings.Contains(result, "Pod IP: 10.0.0.1") {
		t.Errorf("expected pod IP in output")
	}
	if !strings.Contains(result, "LABELS:") {
		t.Errorf("expected LABELS section")
	}
	if !strings.Contains(result, "app: test") {
		t.Errorf("expected label in output")
	}
	if !strings.Contains(result, "CONTAINERS:") {
		t.Errorf("expected CONTAINERS section")
	}
	if !strings.Contains(result, "test-container:") {
		t.Errorf("expected container name in output")
	}
	if !strings.Contains(result, "Image: test-image:latest") {
		t.Errorf("expected container image in output")
	}
}

func TestFormatPodDescription_SortedLabels(t *testing.T) {
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-pod",
			Namespace: "default",
			Labels: map[string]string{
				"zebra":   "last",
				"apple":   "first",
				"banana":  "second",
				"charlie": "third",
			},
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
		},
	}

	result := formatPodDescription(pod)

	// Verify labels are sorted alphabetically: apple, banana, charlie, zebra
	appleIndex := strings.Index(result, "apple: first")
	bananaIndex := strings.Index(result, "banana: second")
	charlieIndex := strings.Index(result, "charlie: third")
	zebraIndex := strings.Index(result, "zebra: last")

	if appleIndex == -1 || bananaIndex == -1 || charlieIndex == -1 || zebraIndex == -1 {
		t.Errorf("not all labels found in output")
	}

	if appleIndex > bananaIndex || bananaIndex > charlieIndex || charlieIndex > zebraIndex {
		t.Errorf("labels not sorted alphabetically")
	}
}

func TestFormatPodDescription_NoLabels(t *testing.T) {
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-pod",
			Namespace: "default",
			// No labels
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
		},
	}

	result := formatPodDescription(pod)

	// Should not contain LABELS section when no labels
	if strings.Contains(result, "LABELS:") {
		t.Errorf("expected no LABELS section for pod without labels")
	}
	// Should still contain other sections
	if !strings.Contains(result, "CONTAINERS:") {
		t.Errorf("expected CONTAINERS section")
	}
}

func TestFormatPodDescription_RunningContainer(t *testing.T) {
	now := metav1.NewTime(time.Date(2025, 1, 15, 10, 30, 45, 0, time.UTC))
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-pod",
			Namespace: "default",
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
						Running: &corev1.ContainerStateRunning{
							StartedAt: now,
						},
					},
					RestartCount: 0,
				},
			},
		},
	}

	result := formatPodDescription(pod)

	// Verify container status output
	if !strings.Contains(result, "CONTAINER STATUSES:") {
		t.Errorf("expected CONTAINER STATUSES section")
	}
	if !strings.Contains(result, "Ready: true") {
		t.Errorf("expected ready status")
	}
	if !strings.Contains(result, "State: Running") {
		t.Errorf("expected running state")
	}
	if !strings.Contains(result, "Started: 2025-01-15 10:30:45") {
		t.Errorf("expected formatted start time")
	}
	if !strings.Contains(result, "Restart Count: 0") {
		t.Errorf("expected restart count")
	}
}

func TestFormatPodDescription_RunningContainerWithZeroTime(t *testing.T) {
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-pod",
			Namespace: "default",
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
						Running: &corev1.ContainerStateRunning{
							// StartedAt is zero value (not set)
						},
					},
					RestartCount: 0,
				},
			},
		},
	}

	result := formatPodDescription(pod)

	// Should not contain "Started:" when timestamp is zero
	if strings.Contains(result, "Started:") {
		t.Errorf("expected no Started field for zero timestamp")
	}
	// Should still contain other fields
	if !strings.Contains(result, "State: Running") {
		t.Errorf("expected running state")
	}
}

func TestFormatPodDescription_WaitingContainer(t *testing.T) {
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-pod",
			Namespace: "default",
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
			Phase: corev1.PodPending,
			ContainerStatuses: []corev1.ContainerStatus{
				{
					Name:  "test-container",
					Ready: false,
					State: corev1.ContainerState{
						Waiting: &corev1.ContainerStateWaiting{
							Reason:  "ImagePullBackOff",
							Message: "Failed to pull image",
						},
					},
					RestartCount: 0,
				},
			},
		},
	}

	result := formatPodDescription(pod)

	// Verify waiting state output
	if !strings.Contains(result, "State: Waiting") {
		t.Errorf("expected waiting state")
	}
	if !strings.Contains(result, "Reason: ImagePullBackOff") {
		t.Errorf("expected waiting reason")
	}
	if !strings.Contains(result, "Message: Failed to pull image") {
		t.Errorf("expected waiting message")
	}
}

func TestFormatPodDescription_WaitingContainerEmptyMessage(t *testing.T) {
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-pod",
			Namespace: "default",
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
			Phase: corev1.PodPending,
			ContainerStatuses: []corev1.ContainerStatus{
				{
					Name:  "test-container",
					Ready: false,
					State: corev1.ContainerState{
						Waiting: &corev1.ContainerStateWaiting{
							Reason: "ImagePullBackOff",
							// Message is empty
						},
					},
					RestartCount: 0,
				},
			},
		},
	}

	result := formatPodDescription(pod)

	// Should not panic or show empty Message line
	if !strings.Contains(result, "State: Waiting") {
		t.Errorf("expected waiting state")
	}
	if !strings.Contains(result, "Reason: ImagePullBackOff") {
		t.Errorf("expected waiting reason")
	}
	// Message line should not appear when empty
	lines := strings.Split(result, "\n")
	for _, line := range lines {
		if strings.HasPrefix(strings.TrimSpace(line), "Message:") && !strings.Contains(line, ":") {
			t.Errorf("unexpected empty Message line")
		}
	}
}

func TestFormatPodDescription_TerminatedContainer(t *testing.T) {
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-pod",
			Namespace: "default",
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
			Phase: corev1.PodFailed,
			ContainerStatuses: []corev1.ContainerStatus{
				{
					Name:  "test-container",
					Ready: false,
					State: corev1.ContainerState{
						Terminated: &corev1.ContainerStateTerminated{
							Reason:   "Error",
							ExitCode: 1,
						},
					},
					RestartCount: 2,
				},
			},
		},
	}

	result := formatPodDescription(pod)

	// Verify terminated state output
	if !strings.Contains(result, "State: Terminated") {
		t.Errorf("expected terminated state")
	}
	if !strings.Contains(result, "Reason: Error") {
		t.Errorf("expected termination reason")
	}
	if !strings.Contains(result, "Exit Code: 1") {
		t.Errorf("expected exit code")
	}
	if !strings.Contains(result, "Restart Count: 2") {
		t.Errorf("expected restart count")
	}
}

func TestFormatPodDescription_WithConditions(t *testing.T) {
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-pod",
			Namespace: "default",
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
			Conditions: []corev1.PodCondition{
				{
					Type:    corev1.PodReady,
					Status:  corev1.ConditionTrue,
					Reason:  "Ready",
					Message: "Pod is ready",
				},
				{
					Type:    corev1.PodScheduled,
					Status:  corev1.ConditionTrue,
					Reason:  "Scheduled",
					Message: "Pod scheduled to node",
				},
			},
		},
	}

	result := formatPodDescription(pod)

	// Verify conditions output
	if !strings.Contains(result, "CONDITIONS:") {
		t.Errorf("expected CONDITIONS section")
	}
	if !strings.Contains(result, "Ready: True") {
		t.Errorf("expected Ready condition")
	}
	if !strings.Contains(result, "Reason: Ready") {
		t.Errorf("expected condition reason")
	}
	if !strings.Contains(result, "Message: Pod is ready") {
		t.Errorf("expected condition message")
	}
}

func TestFormatPodDescription_ConditionsWithEmptyMessage(t *testing.T) {
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-pod",
			Namespace: "default",
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
			Conditions: []corev1.PodCondition{
				{
					Type:   corev1.PodScheduled,
					Status: corev1.ConditionTrue,
					Reason: "Scheduled",
					// Message is empty
				},
			},
		},
	}

	result := formatPodDescription(pod)

	// Should not panic with empty message
	if !strings.Contains(result, "Scheduled: True") {
		t.Errorf("expected Scheduled condition")
	}
	if !strings.Contains(result, "Reason: Scheduled") {
		t.Errorf("expected condition reason")
	}
	// Message line should not appear when empty
	lines := strings.Split(result, "\n")
	for _, line := range lines {
		if strings.HasPrefix(strings.TrimSpace(line), "Message:") && !strings.Contains(line, ":") {
			t.Errorf("unexpected empty Message line")
		}
	}
}

func TestFormatPodDescription_MultipleContainers(t *testing.T) {
	now := metav1.NewTime(time.Now())
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-pod",
			Namespace: "default",
		},
		Spec: corev1.PodSpec{
			Containers: []corev1.Container{
				{
					Name:  "container-1",
					Image: "image-1:latest",
				},
				{
					Name:  "container-2",
					Image: "image-2:latest",
				},
			},
		},
		Status: corev1.PodStatus{
			Phase: corev1.PodRunning,
			ContainerStatuses: []corev1.ContainerStatus{
				{
					Name:  "container-1",
					Ready: true,
					State: corev1.ContainerState{
						Running: &corev1.ContainerStateRunning{
							StartedAt: now,
						},
					},
					RestartCount: 0,
				},
				{
					Name:  "container-2",
					Ready: false,
					State: corev1.ContainerState{
						Waiting: &corev1.ContainerStateWaiting{
							Reason: "CrashLoopBackOff",
						},
					},
					RestartCount: 5,
				},
			},
		},
	}

	result := formatPodDescription(pod)

	// Verify both containers are in output
	if !strings.Contains(result, "container-1:") {
		t.Errorf("expected first container in output")
	}
	if !strings.Contains(result, "container-2:") {
		t.Errorf("expected second container in output")
	}
	if !strings.Contains(result, "Image: image-1:latest") {
		t.Errorf("expected first container image")
	}
	if !strings.Contains(result, "Image: image-2:latest") {
		t.Errorf("expected second container image")
	}

	// Verify different states
	if !strings.Contains(result, "State: Running") {
		t.Errorf("expected running state for container-1")
	}
	if !strings.Contains(result, "State: Waiting") {
		t.Errorf("expected waiting state for container-2")
	}
	if !strings.Contains(result, "CrashLoopBackOff") {
		t.Errorf("expected crash reason for container-2")
	}
}

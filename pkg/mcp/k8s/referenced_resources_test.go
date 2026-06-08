package k8s

import (
	"strings"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func TestFormatDeploymentList_ReferencedResources_EnvFrom(t *testing.T) {
	deployments := []appsv1.Deployment{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "notification-service",
				Namespace: "production",
			},
			Spec: appsv1.DeploymentSpec{
				Replicas: int32Ptr(2),
				Template: corev1.PodTemplateSpec{
					Spec: corev1.PodSpec{
						Containers: []corev1.Container{
							{
								Name:  "app",
								Image: "notification:v1",
								EnvFrom: []corev1.EnvFromSource{
									{
										ConfigMapRef: &corev1.ConfigMapEnvSource{
											LocalObjectReference: corev1.LocalObjectReference{
												Name: "notification-config",
											},
										},
									},
									{
										SecretRef: &corev1.SecretEnvSource{
											LocalObjectReference: corev1.LocalObjectReference{
												Name: "db-credentials",
											},
										},
									},
								},
							},
						},
					},
				},
			},
		},
	}

	result := formatDeploymentList(deployments)

	if !strings.Contains(result, "REFERENCED_RESOURCES:") {
		t.Errorf("expected REFERENCED_RESOURCES section in output, got:\n%s", result)
	}
	if !strings.Contains(result, "CONFIGMAPS: notification-config") {
		t.Errorf("expected configmap from envFrom in output, got:\n%s", result)
	}
	if !strings.Contains(result, "SECRETS: db-credentials") {
		t.Errorf("expected secret from envFrom in output, got:\n%s", result)
	}
}

func TestFormatDeploymentList_ReferencedResources_EnvValueFrom(t *testing.T) {
	deployments := []appsv1.Deployment{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "api-server",
				Namespace: "production",
			},
			Spec: appsv1.DeploymentSpec{
				Replicas: int32Ptr(1),
				Template: corev1.PodTemplateSpec{
					Spec: corev1.PodSpec{
						Containers: []corev1.Container{
							{
								Name:  "api",
								Image: "api:v2",
								Env: []corev1.EnvVar{
									{
										Name: "DB_HOST",
										ValueFrom: &corev1.EnvVarSource{
											ConfigMapKeyRef: &corev1.ConfigMapKeySelector{
												LocalObjectReference: corev1.LocalObjectReference{
													Name: "app-settings",
												},
												Key: "db_host",
											},
										},
									},
									{
										Name: "DB_PASSWORD",
										ValueFrom: &corev1.EnvVarSource{
											SecretKeyRef: &corev1.SecretKeySelector{
												LocalObjectReference: corev1.LocalObjectReference{
													Name: "tls-cert",
												},
												Key: "password",
											},
										},
									},
									{
										// Plain env var, no valueFrom
										Name:  "LOG_LEVEL",
										Value: "info",
									},
								},
							},
						},
					},
				},
			},
		},
	}

	result := formatDeploymentList(deployments)

	if !strings.Contains(result, "CONFIGMAPS: app-settings") {
		t.Errorf("expected configmap from env.valueFrom in output, got:\n%s", result)
	}
	if !strings.Contains(result, "SECRETS: tls-cert") {
		t.Errorf("expected secret from env.valueFrom in output, got:\n%s", result)
	}
}

func TestFormatDeploymentList_ReferencedResources_Volumes(t *testing.T) {
	deployments := []appsv1.Deployment{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "worker",
				Namespace: "production",
			},
			Spec: appsv1.DeploymentSpec{
				Replicas: int32Ptr(1),
				Template: corev1.PodTemplateSpec{
					Spec: corev1.PodSpec{
						Containers: []corev1.Container{
							{
								Name:  "worker",
								Image: "worker:v1",
							},
						},
						Volumes: []corev1.Volume{
							{
								Name: "config-vol",
								VolumeSource: corev1.VolumeSource{
									ConfigMap: &corev1.ConfigMapVolumeSource{
										LocalObjectReference: corev1.LocalObjectReference{
											Name: "worker-config",
										},
									},
								},
							},
							{
								Name: "secret-vol",
								VolumeSource: corev1.VolumeSource{
									Secret: &corev1.SecretVolumeSource{
										SecretName: "worker-secret",
									},
								},
							},
							{
								Name: "data-vol",
								VolumeSource: corev1.VolumeSource{
									PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{
										ClaimName: "data-volume",
									},
								},
							},
						},
					},
				},
			},
		},
	}

	result := formatDeploymentList(deployments)

	if !strings.Contains(result, "CONFIGMAPS: worker-config") {
		t.Errorf("expected configmap from volume in output, got:\n%s", result)
	}
	if !strings.Contains(result, "SECRETS: worker-secret") {
		t.Errorf("expected secret from volume in output, got:\n%s", result)
	}
	if !strings.Contains(result, "PVCS: data-volume") {
		t.Errorf("expected PVC from volume in output, got:\n%s", result)
	}
}

func TestFormatDeploymentList_ReferencedResources_ServiceAccount(t *testing.T) {
	deployments := []appsv1.Deployment{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "auth-service",
				Namespace: "production",
			},
			Spec: appsv1.DeploymentSpec{
				Replicas: int32Ptr(1),
				Template: corev1.PodTemplateSpec{
					Spec: corev1.PodSpec{
						ServiceAccountName: "auth-sa",
						Containers: []corev1.Container{
							{
								Name:  "auth",
								Image: "auth:v1",
							},
						},
					},
				},
			},
		},
	}

	result := formatDeploymentList(deployments)

	if !strings.Contains(result, "SERVICE_ACCOUNT: auth-sa") {
		t.Errorf("expected service account in output, got:\n%s", result)
	}
}

func TestFormatDeploymentList_ReferencedResources_Dedup(t *testing.T) {
	// Same configmap referenced in envFrom AND volume should appear only once
	deployments := []appsv1.Deployment{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "dedup-test",
				Namespace: "production",
			},
			Spec: appsv1.DeploymentSpec{
				Replicas: int32Ptr(1),
				Template: corev1.PodTemplateSpec{
					Spec: corev1.PodSpec{
						Containers: []corev1.Container{
							{
								Name:  "app",
								Image: "app:v1",
								EnvFrom: []corev1.EnvFromSource{
									{
										ConfigMapRef: &corev1.ConfigMapEnvSource{
											LocalObjectReference: corev1.LocalObjectReference{
												Name: "shared-config",
											},
										},
									},
								},
							},
						},
						Volumes: []corev1.Volume{
							{
								Name: "config-vol",
								VolumeSource: corev1.VolumeSource{
									ConfigMap: &corev1.ConfigMapVolumeSource{
										LocalObjectReference: corev1.LocalObjectReference{
											Name: "shared-config",
										},
									},
								},
							},
						},
					},
				},
			},
		},
	}

	result := formatDeploymentList(deployments)

	// Count occurrences of "shared-config" in the CONFIGMAPS line
	lines := strings.Split(result, "\n")
	for _, line := range lines {
		if strings.Contains(line, "CONFIGMAPS:") {
			count := strings.Count(line, "shared-config")
			if count != 1 {
				t.Errorf("expected shared-config to appear once in CONFIGMAPS, got %d times: %s", count, line)
			}
		}
	}
}

func TestFormatDeploymentList_ReferencedResources_EmptyDeployment(t *testing.T) {
	// Deployment with no references should not have REFERENCED_RESOURCES section
	deployments := []appsv1.Deployment{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "bare-deployment",
				Namespace: "production",
			},
			Spec: appsv1.DeploymentSpec{
				Replicas: int32Ptr(1),
				Template: corev1.PodTemplateSpec{
					Spec: corev1.PodSpec{
						Containers: []corev1.Container{
							{
								Name:  "app",
								Image: "app:v1",
							},
						},
					},
				},
			},
		},
	}

	result := formatDeploymentList(deployments)

	if strings.Contains(result, "REFERENCED_RESOURCES:") {
		t.Errorf("expected no REFERENCED_RESOURCES for bare deployment, got:\n%s", result)
	}
}

func TestFormatDeploymentList_ReferencedResources_InitContainers(t *testing.T) {
	// Init containers should also be scanned
	deployments := []appsv1.Deployment{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "with-init",
				Namespace: "production",
			},
			Spec: appsv1.DeploymentSpec{
				Replicas: int32Ptr(1),
				Template: corev1.PodTemplateSpec{
					Spec: corev1.PodSpec{
						InitContainers: []corev1.Container{
							{
								Name:  "init-db",
								Image: "init:v1",
								EnvFrom: []corev1.EnvFromSource{
									{
										SecretRef: &corev1.SecretEnvSource{
											LocalObjectReference: corev1.LocalObjectReference{
												Name: "init-secret",
											},
										},
									},
								},
							},
						},
						Containers: []corev1.Container{
							{
								Name:  "app",
								Image: "app:v1",
								EnvFrom: []corev1.EnvFromSource{
									{
										ConfigMapRef: &corev1.ConfigMapEnvSource{
											LocalObjectReference: corev1.LocalObjectReference{
												Name: "app-config",
											},
										},
									},
								},
							},
						},
					},
				},
			},
		},
	}

	result := formatDeploymentList(deployments)

	if !strings.Contains(result, "SECRETS: init-secret") {
		t.Errorf("expected secret from init container in output, got:\n%s", result)
	}
	if !strings.Contains(result, "CONFIGMAPS: app-config") {
		t.Errorf("expected configmap from regular container in output, got:\n%s", result)
	}
}

func TestFormatDeploymentList_ReferencedResources_AllTypes(t *testing.T) {
	// Full deployment with all reference types
	deployments := []appsv1.Deployment{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "full-service",
				Namespace: "production",
			},
			Spec: appsv1.DeploymentSpec{
				Replicas: int32Ptr(3),
				Template: corev1.PodTemplateSpec{
					Spec: corev1.PodSpec{
						ServiceAccountName: "full-sa",
						Containers: []corev1.Container{
							{
								Name:  "app",
								Image: "app:v1",
								EnvFrom: []corev1.EnvFromSource{
									{
										ConfigMapRef: &corev1.ConfigMapEnvSource{
											LocalObjectReference: corev1.LocalObjectReference{
												Name: "notification-config",
											},
										},
									},
									{
										SecretRef: &corev1.SecretEnvSource{
											LocalObjectReference: corev1.LocalObjectReference{
												Name: "db-credentials",
											},
										},
									},
								},
								Env: []corev1.EnvVar{
									{
										Name: "EXTRA_CONFIG",
										ValueFrom: &corev1.EnvVarSource{
											ConfigMapKeyRef: &corev1.ConfigMapKeySelector{
												LocalObjectReference: corev1.LocalObjectReference{
													Name: "app-settings",
												},
												Key: "extra",
											},
										},
									},
								},
							},
						},
						Volumes: []corev1.Volume{
							{
								Name: "data",
								VolumeSource: corev1.VolumeSource{
									PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{
										ClaimName: "data-volume",
									},
								},
							},
							{
								Name: "tls",
								VolumeSource: corev1.VolumeSource{
									Secret: &corev1.SecretVolumeSource{
										SecretName: "tls-cert",
									},
								},
							},
						},
					},
				},
			},
		},
	}

	result := formatDeploymentList(deployments)

	// Verify all sections present
	if !strings.Contains(result, "REFERENCED_RESOURCES:") {
		t.Fatalf("missing REFERENCED_RESOURCES section")
	}

	// ConfigMaps: notification-config and app-settings (sorted)
	if !strings.Contains(result, "app-settings") || !strings.Contains(result, "notification-config") {
		t.Errorf("expected both configmaps in output, got:\n%s", result)
	}

	// Secrets: db-credentials and tls-cert (sorted)
	if !strings.Contains(result, "db-credentials") || !strings.Contains(result, "tls-cert") {
		t.Errorf("expected both secrets in output, got:\n%s", result)
	}

	// PVCs
	if !strings.Contains(result, "PVCS: data-volume") {
		t.Errorf("expected PVC in output, got:\n%s", result)
	}

	// Service Account
	if !strings.Contains(result, "SERVICE_ACCOUNT: full-sa") {
		t.Errorf("expected service account in output, got:\n%s", result)
	}
}

// Helper function to create int32 pointers
func int32Ptr(i int32) *int32 {
	return &i
}

package k8s

import (
	"strings"
	"testing"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
)

// =============================================================================
// GVR Resolution Tests
// =============================================================================

func TestResolveGVR_PluralNames(t *testing.T) {
	cases := []struct {
		input    string
		wantGVR  schema.GroupVersionResource
		wantNS   bool
	}{
		{"pods", schema.GroupVersionResource{Group: "", Version: "v1", Resource: "pods"}, true},
		{"deployments", schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "deployments"}, true},
		{"services", schema.GroupVersionResource{Group: "", Version: "v1", Resource: "services"}, true},
		{"configmaps", schema.GroupVersionResource{Group: "", Version: "v1", Resource: "configmaps"}, true},
		{"secrets", schema.GroupVersionResource{Group: "", Version: "v1", Resource: "secrets"}, true},
		{"statefulsets", schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "statefulsets"}, true},
		{"daemonsets", schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "daemonsets"}, true},
		{"jobs", schema.GroupVersionResource{Group: "batch", Version: "v1", Resource: "jobs"}, true},
		{"ingresses", schema.GroupVersionResource{Group: "networking.k8s.io", Version: "v1", Resource: "ingresses"}, true},
		{"nodes", schema.GroupVersionResource{Group: "", Version: "v1", Resource: "nodes"}, false},
		{"namespaces", schema.GroupVersionResource{Group: "", Version: "v1", Resource: "namespaces"}, false},
	}

	for _, tc := range cases {
		t.Run(tc.input, func(t *testing.T) {
			info, err := resolveGVR(tc.input)
			if err != nil {
				t.Fatalf("resolveGVR(%q) returned error: %v", tc.input, err)
			}
			if info.GVR != tc.wantGVR {
				t.Errorf("resolveGVR(%q).GVR = %v, want %v", tc.input, info.GVR, tc.wantGVR)
			}
			if info.Namespaced != tc.wantNS {
				t.Errorf("resolveGVR(%q).Namespaced = %v, want %v", tc.input, info.Namespaced, tc.wantNS)
			}
		})
	}
}

func TestResolveGVR_ShortNames(t *testing.T) {
	cases := []struct {
		input   string
		wantRes string
	}{
		{"svc", "services"},
		{"cm", "configmaps"},
		{"deploy", "deployments"},
		{"sts", "statefulsets"},
		{"ds", "daemonsets"},
		{"rs", "replicasets"},
		{"ing", "ingresses"},
		{"hpa", "horizontalpodautoscalers"},
		{"pvc", "persistentvolumeclaims"},
		{"pv", "persistentvolumes"},
		{"ns", "namespaces"},
		{"sa", "serviceaccounts"},
		{"ep", "endpoints"},
		{"sc", "storageclasses"},
		{"cj", "cronjobs"},
		{"netpol", "networkpolicies"},
	}

	for _, tc := range cases {
		t.Run(tc.input, func(t *testing.T) {
			info, err := resolveGVR(tc.input)
			if err != nil {
				t.Fatalf("resolveGVR(%q) returned error: %v", tc.input, err)
			}
			if info.GVR.Resource != tc.wantRes {
				t.Errorf("resolveGVR(%q).Resource = %q, want %q", tc.input, info.GVR.Resource, tc.wantRes)
			}
		})
	}
}

func TestResolveGVR_UnknownType(t *testing.T) {
	_, err := resolveGVR("foobar")
	if err == nil {
		t.Fatal("resolveGVR(\"foobar\") should return error for unknown type")
	}
	if !strings.Contains(err.Error(), "foobar") {
		t.Errorf("error should mention the unknown type, got: %v", err)
	}
}

func TestResolveGVR_ClusterScoped(t *testing.T) {
	clusterScoped := []string{"nodes", "node", "namespaces", "namespace", "ns",
		"persistentvolumes", "persistentvolume", "pvs", "pv",
		"storageclasses", "storageclass", "sc"}

	for _, name := range clusterScoped {
		t.Run(name, func(t *testing.T) {
			info, err := resolveGVR(name)
			if err != nil {
				t.Fatalf("resolveGVR(%q) returned error: %v", name, err)
			}
			if info.Namespaced {
				t.Errorf("resolveGVR(%q).Namespaced = true, want false (cluster-scoped)", name)
			}
		})
	}
}

// =============================================================================
// Sanitize Resource Tests
// =============================================================================

func TestSanitizeResource_StripsManagedFields(t *testing.T) {
	obj := map[string]interface{}{
		"apiVersion": "v1",
		"kind":       "Pod",
		"metadata": map[string]interface{}{
			"name":          "test-pod",
			"namespace":     "default",
			"managedFields": []interface{}{"some", "data"},
			"generation":    int64(3),
			"resourceVersion": "12345",
			"uid":           "abc-def-123",
			"annotations": map[string]interface{}{
				"kubectl.kubernetes.io/last-applied-configuration": `{"big":"json"}`,
				"app.kubernetes.io/name": "myapp",
			},
		},
	}

	result := sanitizeResource(obj)

	meta, ok := result["metadata"].(map[string]interface{})
	if !ok {
		t.Fatal("metadata should be a map")
	}

	if _, exists := meta["managedFields"]; exists {
		t.Error("managedFields should be stripped")
	}
	if _, exists := meta["generation"]; exists {
		t.Error("generation should be stripped")
	}
	if _, exists := meta["resourceVersion"]; exists {
		t.Error("resourceVersion should be stripped")
	}
	if _, exists := meta["uid"]; exists {
		t.Error("uid should be stripped")
	}

	annotations, ok := meta["annotations"].(map[string]interface{})
	if !ok {
		t.Fatal("annotations should be a map")
	}
	if _, exists := annotations["kubectl.kubernetes.io/last-applied-configuration"]; exists {
		t.Error("last-applied-configuration annotation should be stripped")
	}
	if _, exists := annotations["app.kubernetes.io/name"]; !exists {
		t.Error("non-noisy annotations should be preserved")
	}
}

func TestSanitizeResource_RedactsSecretData(t *testing.T) {
	obj := map[string]interface{}{
		"apiVersion": "v1",
		"kind":       "Secret",
		"metadata": map[string]interface{}{
			"name":      "db-creds",
			"namespace": "production",
		},
		"type": "Opaque",
		"data": map[string]interface{}{
			"username": "YWRtaW4=",
			"password": "c3VwZXItc2VjcmV0",
		},
		"stringData": map[string]interface{}{
			"extra": "plaintext-secret",
		},
	}

	result := sanitizeResource(obj)

	// Data keys preserved but values redacted
	data, ok := result["data"].(map[string]interface{})
	if !ok {
		t.Fatal("data should be a map")
	}
	if data["username"] != "<REDACTED>" {
		t.Errorf("username should be <REDACTED>, got: %v", data["username"])
	}
	if data["password"] != "<REDACTED>" {
		t.Errorf("password should be <REDACTED>, got: %v", data["password"])
	}

	// stringData removed entirely
	if _, exists := result["stringData"]; exists {
		t.Error("stringData should be removed entirely")
	}
}

func TestSanitizeResource_NonSecretPassesThrough(t *testing.T) {
	obj := map[string]interface{}{
		"apiVersion": "v1",
		"kind":       "ConfigMap",
		"metadata": map[string]interface{}{
			"name":      "app-config",
			"namespace": "default",
		},
		"data": map[string]interface{}{
			"DATABASE_URL": "postgres://db:5432/mydb",
		},
	}

	result := sanitizeResource(obj)

	data, ok := result["data"].(map[string]interface{})
	if !ok {
		t.Fatal("data should be a map")
	}
	if data["DATABASE_URL"] != "postgres://db:5432/mydb" {
		t.Errorf("ConfigMap data should be preserved, got: %v", data["DATABASE_URL"])
	}
}

func TestSanitizeResource_EmptyAnnotationsRemoved(t *testing.T) {
	obj := map[string]interface{}{
		"apiVersion": "v1",
		"kind":       "Pod",
		"metadata": map[string]interface{}{
			"name": "test",
			"annotations": map[string]interface{}{
				"kubectl.kubernetes.io/last-applied-configuration": `{"data":"value"}`,
			},
		},
	}

	result := sanitizeResource(obj)
	meta := result["metadata"].(map[string]interface{})

	// If the only annotation was last-applied-configuration, annotations map should be removed
	if anns, exists := meta["annotations"]; exists {
		annMap, ok := anns.(map[string]interface{})
		if ok && len(annMap) == 0 {
			t.Error("empty annotations map should be removed")
		}
	}
}

// =============================================================================
// YAML Formatting Tests
// =============================================================================

func TestFormatResourceYAML_BasicOutput(t *testing.T) {
	item := &unstructured.Unstructured{
		Object: map[string]interface{}{
			"apiVersion": "v1",
			"kind":       "Pod",
			"metadata": map[string]interface{}{
				"name":      "test-pod",
				"namespace": "default",
			},
			"spec": map[string]interface{}{
				"containers": []interface{}{
					map[string]interface{}{
						"name":  "app",
						"image": "nginx:latest",
					},
				},
			},
		},
	}

	result := formatResourceYAML(item)

	if !strings.Contains(result, "kind: Pod") {
		t.Error("expected 'kind: Pod' in output")
	}
	if !strings.Contains(result, "name: test-pod") {
		t.Error("expected 'name: test-pod' in output")
	}
	if !strings.Contains(result, "image: nginx:latest") {
		t.Error("expected container image in output")
	}
	// Should not contain noisy fields (they should be stripped by sanitize)
	if strings.Contains(result, "managedFields") {
		t.Error("managedFields should not appear in output")
	}
}

func TestFormatResourceListYAML_MultipleItems(t *testing.T) {
	items := []unstructured.Unstructured{
		{Object: map[string]interface{}{
			"apiVersion": "v1",
			"kind":       "Pod",
			"metadata":   map[string]interface{}{"name": "pod-a", "namespace": "default"},
		}},
		{Object: map[string]interface{}{
			"apiVersion": "v1",
			"kind":       "Pod",
			"metadata":   map[string]interface{}{"name": "pod-b", "namespace": "default"},
		}},
	}

	result := formatResourceListYAML(items)

	if !strings.Contains(result, "pod-a") {
		t.Error("expected pod-a in output")
	}
	if !strings.Contains(result, "pod-b") {
		t.Error("expected pod-b in output")
	}
	// Should contain separator or list header
	if !strings.Contains(result, "---") {
		t.Error("expected YAML document separator (---) between items")
	}
}

func TestFormatResourceListYAML_EmptyList(t *testing.T) {
	items := []unstructured.Unstructured{}
	result := formatResourceListYAML(items)

	if result == "" {
		t.Error("empty list should return a message, not empty string")
	}
}

func TestFormatResourceYAML_SecretDataRedacted(t *testing.T) {
	item := &unstructured.Unstructured{
		Object: map[string]interface{}{
			"apiVersion": "v1",
			"kind":       "Secret",
			"metadata": map[string]interface{}{
				"name":      "my-secret",
				"namespace": "default",
			},
			"type": "Opaque",
			"data": map[string]interface{}{
				"password": "c3VwZXItc2VjcmV0LXBhc3N3b3Jk",
			},
		},
	}

	result := formatResourceYAML(item)

	if strings.Contains(result, "c3VwZXItc2VjcmV0LXBhc3N3b3Jk") {
		t.Error("SECURITY VIOLATION: secret data value found in YAML output")
	}
	if !strings.Contains(result, "<REDACTED>") {
		t.Error("expected <REDACTED> placeholder for secret data")
	}
	if !strings.Contains(result, "password") {
		t.Error("expected secret key name 'password' to be preserved")
	}
}

func TestFormatResourceListYAML_SecretsRedacted(t *testing.T) {
	items := []unstructured.Unstructured{
		{Object: map[string]interface{}{
			"apiVersion": "v1",
			"kind":       "Secret",
			"metadata":   map[string]interface{}{"name": "secret-1", "namespace": "default"},
			"data": map[string]interface{}{
				"api-key": "c2stbGl2ZS1hYmNkZWY=",
			},
		}},
	}

	result := formatResourceListYAML(items)

	if strings.Contains(result, "c2stbGl2ZS1hYmNkZWY=") {
		t.Error("SECURITY VIOLATION: secret data value found in list YAML output")
	}
	if !strings.Contains(result, "<REDACTED>") {
		t.Error("expected <REDACTED> in list output for secrets")
	}
}

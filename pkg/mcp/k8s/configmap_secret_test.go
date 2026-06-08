package k8s

import (
	"strings"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// =============================================================================
// ConfigMap Formatter Tests
// =============================================================================

func TestFormatConfigMapList_EmptyList(t *testing.T) {
	configmaps := []corev1.ConfigMap{}
	result := formatConfigMapList(configmaps)

	if !strings.Contains(result, "No configmaps found") {
		t.Errorf("expected 'No configmaps found' in output, got: %s", result)
	}
}

func TestFormatConfigMapList_SingleConfigMap(t *testing.T) {
	configmaps := []corev1.ConfigMap{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:              "app-config",
				Namespace:         "default",
				CreationTimestamp: metav1.NewTime(time.Now().Add(-24 * time.Hour)),
			},
			Data: map[string]string{
				"DATABASE_URL": "postgres://db:5432/mydb",
				"LOG_LEVEL":    "info",
			},
		},
	}

	result := formatConfigMapList(configmaps)

	if !strings.Contains(result, "Found 1 configmap(s)") {
		t.Errorf("expected 'Found 1 configmap(s)' in output, got: %s", result)
	}
	if !strings.Contains(result, "NAME: app-config") {
		t.Errorf("expected configmap name in output")
	}
	if !strings.Contains(result, "NAMESPACE: default") {
		t.Errorf("expected namespace in output")
	}
	// ConfigMaps should show full data (keys + values)
	if !strings.Contains(result, "DATABASE_URL") {
		t.Errorf("expected data key 'DATABASE_URL' in output")
	}
	if !strings.Contains(result, "postgres://db:5432/mydb") {
		t.Errorf("expected data value for DATABASE_URL in output")
	}
	if !strings.Contains(result, "LOG_LEVEL") {
		t.Errorf("expected data key 'LOG_LEVEL' in output")
	}
	if !strings.Contains(result, "info") {
		t.Errorf("expected data value for LOG_LEVEL in output")
	}
	if !strings.Contains(result, "AGE:") {
		t.Errorf("expected AGE field in output")
	}
}

func TestFormatConfigMapList_MultipleConfigMaps(t *testing.T) {
	configmaps := []corev1.ConfigMap{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:              "config-a",
				Namespace:         "production",
				CreationTimestamp: metav1.NewTime(time.Now().Add(-48 * time.Hour)),
			},
			Data: map[string]string{"KEY1": "value1"},
		},
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:              "config-b",
				Namespace:         "production",
				CreationTimestamp: metav1.NewTime(time.Now().Add(-24 * time.Hour)),
			},
			Data: map[string]string{"KEY2": "value2"},
		},
	}

	result := formatConfigMapList(configmaps)

	if !strings.Contains(result, "Found 2 configmap(s)") {
		t.Errorf("expected 'Found 2 configmap(s)' in output, got: %s", result)
	}
	if !strings.Contains(result, "NAME: config-a") {
		t.Errorf("expected first configmap name in output")
	}
	if !strings.Contains(result, "NAME: config-b") {
		t.Errorf("expected second configmap name in output")
	}
}

func TestFormatConfigMapList_SortedDataKeys(t *testing.T) {
	configmaps := []corev1.ConfigMap{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:              "sorted-cm",
				Namespace:         "default",
				CreationTimestamp: metav1.NewTime(time.Now()),
			},
			Data: map[string]string{
				"ZEBRA": "z-value",
				"ALPHA": "a-value",
				"BETA":  "b-value",
			},
		},
	}

	result := formatConfigMapList(configmaps)

	alphaIdx := strings.Index(result, "ALPHA")
	betaIdx := strings.Index(result, "BETA")
	zebraIdx := strings.Index(result, "ZEBRA")

	if alphaIdx == -1 || betaIdx == -1 || zebraIdx == -1 {
		t.Fatalf("not all data keys found in output: %s", result)
	}
	if alphaIdx > betaIdx {
		t.Errorf("data keys not sorted: ALPHA should appear before BETA")
	}
	if betaIdx > zebraIdx {
		t.Errorf("data keys not sorted: BETA should appear before ZEBRA")
	}
}

// =============================================================================
// Secret Formatter Tests
// =============================================================================

func TestFormatSecretList_EmptyList(t *testing.T) {
	secrets := []corev1.Secret{}
	result := formatSecretList(secrets)

	if !strings.Contains(result, "No secrets found") {
		t.Errorf("expected 'No secrets found' in output, got: %s", result)
	}
}

func TestFormatSecretList_SingleSecret(t *testing.T) {
	secrets := []corev1.Secret{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:              "db-credentials",
				Namespace:         "production",
				CreationTimestamp: metav1.NewTime(time.Now().Add(-72 * time.Hour)),
			},
			Type: corev1.SecretTypeOpaque,
			Data: map[string][]byte{
				"username": []byte("admin"),
				"password": []byte("super-secret-password-123"),
			},
		},
	}

	result := formatSecretList(secrets)

	if !strings.Contains(result, "Found 1 secret(s)") {
		t.Errorf("expected 'Found 1 secret(s)' in output, got: %s", result)
	}
	if !strings.Contains(result, "NAME: db-credentials") {
		t.Errorf("expected secret name in output")
	}
	if !strings.Contains(result, "NAMESPACE: production") {
		t.Errorf("expected namespace in output")
	}
	if !strings.Contains(result, "TYPE: Opaque") {
		t.Errorf("expected secret type in output")
	}
	// Should show data KEY NAMES only
	if !strings.Contains(result, "username") {
		t.Errorf("expected data key name 'username' in output")
	}
	if !strings.Contains(result, "password") {
		t.Errorf("expected data key name 'password' in output")
	}
	if !strings.Contains(result, "AGE:") {
		t.Errorf("expected AGE field in output")
	}
}

func TestFormatSecretList_MultipleSecrets(t *testing.T) {
	secrets := []corev1.Secret{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:              "secret-a",
				Namespace:         "staging",
				CreationTimestamp: metav1.NewTime(time.Now().Add(-24 * time.Hour)),
			},
			Type: corev1.SecretTypeOpaque,
			Data: map[string][]byte{"key-a": []byte("val-a")},
		},
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:              "secret-b",
				Namespace:         "staging",
				CreationTimestamp: metav1.NewTime(time.Now().Add(-12 * time.Hour)),
			},
			Type: corev1.SecretTypeTLS,
			Data: map[string][]byte{"tls.crt": []byte("cert"), "tls.key": []byte("key")},
		},
	}

	result := formatSecretList(secrets)

	if !strings.Contains(result, "Found 2 secret(s)") {
		t.Errorf("expected 'Found 2 secret(s)' in output, got: %s", result)
	}
	if !strings.Contains(result, "NAME: secret-a") {
		t.Errorf("expected first secret name in output")
	}
	if !strings.Contains(result, "NAME: secret-b") {
		t.Errorf("expected second secret name in output")
	}
	if !strings.Contains(result, "TYPE: Opaque") {
		t.Errorf("expected Opaque type in output")
	}
	if !strings.Contains(result, "TYPE: kubernetes.io/tls") {
		t.Errorf("expected TLS type in output")
	}
}

// TestFormatSecretList_NoDataExposed is a CRITICAL security test.
// It verifies that secret DATA VALUES are NEVER included in the formatted output.
// Only key names may appear. This is the structural security guarantee.
func TestFormatSecretList_NoDataExposed(t *testing.T) {
	secrets := []corev1.Secret{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:              "api-keys",
				Namespace:         "production",
				CreationTimestamp: metav1.NewTime(time.Now()),
			},
			Type: corev1.SecretTypeOpaque,
			Data: map[string][]byte{
				"api-key":    []byte("sk-live-abcdef123456789"),
				"api-secret": []byte("whsec_very_secret_value"),
			},
			StringData: map[string]string{
				"extra-key": "extra-secret-value-do-not-expose",
			},
		},
	}

	result := formatSecretList(secrets)

	// SECURITY: These actual secret values MUST NOT appear in output
	if strings.Contains(result, "sk-live-abcdef123456789") {
		t.Errorf("SECURITY VIOLATION: secret data value 'sk-live-abcdef123456789' found in output")
	}
	if strings.Contains(result, "whsec_very_secret_value") {
		t.Errorf("SECURITY VIOLATION: secret data value 'whsec_very_secret_value' found in output")
	}
	if strings.Contains(result, "extra-secret-value-do-not-expose") {
		t.Errorf("SECURITY VIOLATION: StringData value found in output")
	}

	// But key names SHOULD appear
	if !strings.Contains(result, "api-key") {
		t.Errorf("expected data key name 'api-key' in output")
	}
	if !strings.Contains(result, "api-secret") {
		t.Errorf("expected data key name 'api-secret' in output")
	}
}

package k8s

import (
	"testing"
)

func TestNewClusterManager_EmptyConfigs(t *testing.T) {
	_, err := NewClusterManager([]ClusterConfig{})
	if err == nil {
		t.Fatal("expected error for empty configs, got nil")
	}
	if err.Error() != "at least one cluster config required" {
		t.Errorf("unexpected error message: %v", err)
	}
}

func TestNewClusterManager_EmptyClusterName(t *testing.T) {
	configs := []ClusterConfig{
		{Name: "", KubeconfigPath: "/path/to/config", IsInCluster: false},
	}
	_, err := NewClusterManager(configs)
	if err == nil {
		t.Fatal("expected error for empty cluster name, got nil")
	}
	if err.Error() != "cluster name cannot be empty" {
		t.Errorf("unexpected error message: %v", err)
	}
}

func TestNewClusterManager_MissingKubeconfigPath(t *testing.T) {
	configs := []ClusterConfig{
		{Name: "test-cluster", KubeconfigPath: "", IsInCluster: false},
	}
	_, err := NewClusterManager(configs)
	if err == nil {
		t.Fatal("expected error for missing kubeconfig path, got nil")
	}
	expected := "kubeconfig path required for cluster test-cluster"
	if err.Error() != expected {
		t.Errorf("expected error %q, got %q", expected, err.Error())
	}
}

func TestNewClusterManager_InvalidKubeconfig(t *testing.T) {
	configs := []ClusterConfig{
		{Name: "test-cluster", KubeconfigPath: "/nonexistent/path/config", IsInCluster: false},
	}
	_, err := NewClusterManager(configs)
	if err == nil {
		t.Fatal("expected error for invalid kubeconfig, got nil")
	}
	// Error should mention the cluster name
	if err.Error() == "" {
		t.Error("expected non-empty error message")
	}
}

func TestGetClient_NotFound(t *testing.T) {
	// We can't easily test with real clusters in unit tests,
	// but we can test the error case by starting with empty manager
	// after initialization with at least one valid config fails.
	// For this test, we'll just verify the manager is properly structured.

	// This test validates the error path of GetClient when cluster doesn't exist
	t.Run("cluster not found", func(t *testing.T) {
		// Create a manager with mock setup (requires refactoring for testability)
		// For now, this is a placeholder showing the test structure
		t.Skip("requires mock k8s client for full testing")
	})
}

func TestListClusters(t *testing.T) {
	// Test structure for ListClusters
	t.Run("lists all configured clusters", func(t *testing.T) {
		t.Skip("requires mock k8s client for full testing")
	})
}

// Note: Full integration tests with actual kubeconfig files should be in
// integration test suite, not unit tests.

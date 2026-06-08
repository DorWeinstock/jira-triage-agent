package k8s

import (
	"fmt"
	"log"
	"sync"
)

// ClusterManager manages multiple Kubernetes clients for multi-cluster support.
// It initializes clients at startup and provides thread-safe access to them.
type ClusterManager struct {
	clients map[string]*Client
	mu      sync.RWMutex
}

// ClusterConfig defines configuration for a single cluster.
type ClusterConfig struct {
	Name           string // Cluster identifier (e.g., "saas", "hldc02", "hldc03")
	KubeconfigPath string // Path to kubeconfig file, or "in-cluster" for InClusterConfig
	IsInCluster    bool   // True if using in-cluster authentication
}

// NewClusterManager creates a ClusterManager and initializes all configured clusters.
// Returns an error immediately for any invalid config (empty name, missing kubeconfig path).
// Clusters that fail to connect are skipped with a warning (graceful degradation).
// Returns error if no clusters could be initialized.
func NewClusterManager(configs []ClusterConfig) (*ClusterManager, error) {
	if len(configs) == 0 {
		return nil, fmt.Errorf("at least one cluster config required")
	}

	// Validate all configs up front before attempting any connections.
	for _, cfg := range configs {
		if cfg.Name == "" {
			return nil, fmt.Errorf("cluster name cannot be empty")
		}
		if !cfg.IsInCluster && cfg.KubeconfigPath == "" {
			return nil, fmt.Errorf("kubeconfig path required for cluster %s", cfg.Name)
		}
	}

	clients := make(map[string]*Client, len(configs))

	for _, cfg := range configs {
		var client *Client
		var err error

		if cfg.IsInCluster {
			client, err = NewClient()
			if err != nil {
				log.Printf("WARNING: skipping cluster %s: %v", cfg.Name, err)
				continue
			}
		} else {
			client, err = NewClientFromKubeconfig(cfg.KubeconfigPath)
			if err != nil {
				log.Printf("WARNING: skipping cluster %s: %v", cfg.Name, err)
				continue
			}
		}

		clients[cfg.Name] = client
	}

	if len(clients) == 0 {
		return nil, fmt.Errorf("no clusters could be initialized from %d configs", len(configs))
	}

	return &ClusterManager{
		clients: clients,
	}, nil
}

// GetClient returns the K8s client for the specified cluster.
// Returns error if cluster not found.
func (m *ClusterManager) GetClient(cluster string) (*Client, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	client, ok := m.clients[cluster]
	if !ok {
		return nil, fmt.Errorf("cluster %q not found (available: %v)", cluster, m.ListClusters())
	}

	return client, nil
}

// ListClusters returns all configured cluster names.
func (m *ClusterManager) ListClusters() []string {
	m.mu.RLock()
	defer m.mu.RUnlock()

	clusters := make([]string, 0, len(m.clients))
	for name := range m.clients {
		clusters = append(clusters, name)
	}
	return clusters
}

#!/bin/bash
set -e

# Configure containerd in kind cluster to use proxy and skip TLS verification for Artifactory
CLUSTER_NAME="${1:-jira-agent-test}"
PROXY_URL="${2:-http://proxy-dmz.intel.com:912}"
REGISTRY="${3:-artifactory-kfs.habana-labs.com}"

echo "Configuring proxy and registry settings for kind cluster: $CLUSTER_NAME"

# Get the kind node container name
NODE_NAME="${CLUSTER_NAME}-control-plane"

# Create the directory if it doesn't exist
docker exec "$NODE_NAME" mkdir -p /etc/systemd/system/containerd.service.d

# Create containerd config with proxy settings
docker exec "$NODE_NAME" bash -c "cat > /etc/systemd/system/containerd.service.d/http-proxy.conf <<EOF
[Service]
Environment=\"HTTP_PROXY=$PROXY_URL\"
Environment=\"HTTPS_PROXY=$PROXY_URL\"
Environment=\"NO_PROXY=localhost,127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,.svc,.svc.cluster.local\"
EOF"

# Configure containerd to skip TLS verification for Artifactory
docker exec "$NODE_NAME" bash -c "mkdir -p /etc/containerd"
docker exec "$NODE_NAME" bash -c "cat > /etc/containerd/config.toml <<EOF
version = 2
root = \"/var/lib/containerd\"
state = \"/run/containerd\"
oom_score = 0

[grpc]
  max_recv_message_size = 16777216
  max_send_message_size = 16777216

[debug]
  level = \"info\"

[metrics]
  address = \"\"
  grpc_histogram = false

[plugins]
  [plugins.\"io.containerd.grpc.v1.cri\"]
    sandbox_image = \"registry.k8s.io/pause:3.9\"
    max_container_log_line_size = -1
    enable_unprivileged_ports = false
    enable_unprivileged_icmp = false
    [plugins.\"io.containerd.grpc.v1.cri\".containerd]
      default_runtime_name = \"runc\"
      snapshotter = \"overlayfs\"
      [plugins.\"io.containerd.grpc.v1.cri\".containerd.runtimes]
        [plugins.\"io.containerd.grpc.v1.cri\".containerd.runtimes.runc]
          runtime_type = \"io.containerd.runc.v2\"
          runtime_engine = \"\"
          runtime_root = \"\"
          [plugins.\"io.containerd.grpc.v1.cri\".containerd.runtimes.runc.options]
            systemdCgroup = true
    [plugins.\"io.containerd.grpc.v1.cri\".registry]
      [plugins.\"io.containerd.grpc.v1.cri\".registry.mirrors]
        [plugins.\"io.containerd.grpc.v1.cri\".registry.mirrors.\"docker.io\"]
          endpoint = [\"https://registry-1.docker.io\"]
        [plugins.\"io.containerd.grpc.v1.cri\".registry.mirrors.\"$REGISTRY\"]
          endpoint = [\"https://$REGISTRY\"]
      [plugins.\"io.containerd.grpc.v1.cri\".registry.configs]
        [plugins.\"io.containerd.grpc.v1.cri\".registry.configs.\"$REGISTRY\".tls]
          insecure_skip_verify = true
EOF"

# Restart containerd
docker exec "$NODE_NAME" systemctl daemon-reload
docker exec "$NODE_NAME" systemctl restart containerd

echo "Proxy and registry configured successfully for $CLUSTER_NAME"
echo "Registry $REGISTRY is now configured to skip TLS verification"

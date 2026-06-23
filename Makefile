# Default target
.DEFAULT_GOAL := help

.PHONY: help
help: ## Display this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

# Container registry (override per-image with JIRA_AGENT_REGISTRY / AGENT_REGISTRY)
REGISTRY ?= artifactory-kfs.habana-labs.com/docker-developers/users/dweinsto

JIRA_AGENT_REGISTRY ?= $(REGISTRY)
JIRA_AGENT_IMAGE_NAME = jira-triage-agent
JIRA_AGENT_VERSION ?= 1.0.0
JIRA_AGENT_IMAGE = $(JIRA_AGENT_REGISTRY)/$(JIRA_AGENT_IMAGE_NAME):$(JIRA_AGENT_VERSION)

AGENT_REGISTRY ?= $(REGISTRY)
AGENT_IMAGE_NAME = jira-triage-langgraph
AGENT_VERSION ?= 1.0.0
AGENT_IMAGE = $(AGENT_REGISTRY)/$(AGENT_IMAGE_NAME):$(AGENT_VERSION)

# Unique tag for cache-busting registry pushes (Artifactory caches :latest).
# := ensures the timestamp is evaluated once so push and set-image use the same tag.
IMAGE_TAG := $(shell date +%Y%m%d%H%M%S)

# Kubernetes contexts
REMOTE_KUBECONFIG ?= ~/.kube/config-sched
REMOTE_KUBE_CONTEXT ?= test-sched
KIND_CLUSTER ?= kagent-test
KUBE_CONTEXT ?= kind-$(KIND_CLUSTER)
NAMESPACE ?= jira-k8s-agent

# Kubectl shorthands (always include namespace)
KUBECTL        = kubectl --context $(KUBE_CONTEXT) -n $(NAMESPACE)
KUBECTL_REMOTE = KUBECONFIG=$(REMOTE_KUBECONFIG) kubectl --context $(REMOTE_KUBE_CONTEXT) -n $(NAMESPACE)

# ---------------------------------------------------------------------------
# Docker macros
# ---------------------------------------------------------------------------

# $(call docker-build,image-tag,registry,image-name,build-context,dockerfile)
#   Builds and tags :latest.  Pass "-f Dockerfile .." or "." as build-context.
define docker-build
	docker build -t $(1) $(if $(5),-f $(5)) $(4)
	docker tag $(1) $(2)/$(3):latest
endef

# $(call docker-push,source-image,registry,image-name,tag)
#   Tags the source image with the given tag and pushes.
define docker-push
	docker tag $(1) $(2)/$(3):$(4)
	docker push $(2)/$(3):$(4)
endef

# ---------------------------------------------------------------------------
##@ Build
# ---------------------------------------------------------------------------
.PHONY: build build-jira-agent-docker build-langgraph-docker build-all

build: ## Build jira-agent Go binary
	go build -o bin/jira-agent ./cmd/jira-agent

build-jira-agent-docker: ## Build jira-agent Docker image
	$(call docker-build,$(JIRA_AGENT_IMAGE),$(JIRA_AGENT_REGISTRY),$(JIRA_AGENT_IMAGE_NAME),.,Dockerfile)

build-langgraph-docker: ## Build LangGraph agent Docker image
	cd langgraph-agent && $(call docker-build,$(AGENT_IMAGE),$(AGENT_REGISTRY),$(AGENT_IMAGE_NAME),.)

build-all: build-jira-agent-docker build-langgraph-docker ## Build all Docker images

# ---------------------------------------------------------------------------
##@ Kind Cluster
# ---------------------------------------------------------------------------
.PHONY: kind-load-jira-agent kind-load-langgraph kind-load-all

kind-load-jira-agent: build-jira-agent-docker ## Load jira-agent image into kind cluster
	kind load docker-image $(JIRA_AGENT_IMAGE) --name $(KIND_CLUSTER)

kind-load-langgraph: build-langgraph-docker ## Load LangGraph agent image into kind cluster
	kind load docker-image $(AGENT_IMAGE) --name $(KIND_CLUSTER)

kind-load-all: kind-load-jira-agent kind-load-langgraph ## Load all images into kind cluster

# ---------------------------------------------------------------------------
##@ Push to Registry
# ---------------------------------------------------------------------------
.PHONY: push-jira-agent push-langgraph push-all

push-jira-agent: build-jira-agent-docker ## Push jira-agent image to registry
	$(call docker-push,$(JIRA_AGENT_IMAGE),$(JIRA_AGENT_REGISTRY),$(JIRA_AGENT_IMAGE_NAME),$(IMAGE_TAG))
	docker push $(JIRA_AGENT_REGISTRY)/$(JIRA_AGENT_IMAGE_NAME):latest

push-langgraph: build-langgraph-docker ## Push LangGraph agent image to registry
	$(call docker-push,$(AGENT_IMAGE),$(AGENT_REGISTRY),$(AGENT_IMAGE_NAME),$(IMAGE_TAG))
	docker push $(AGENT_REGISTRY)/$(AGENT_IMAGE_NAME):latest

push-all: push-jira-agent push-langgraph ## Push all images to registry

# ---------------------------------------------------------------------------
##@ Remote Deployment
# ---------------------------------------------------------------------------
.PHONY: deploy-remote

deploy-remote: push-all ## Build, push, and deploy to remote cluster
	$(KUBECTL_REMOTE) set image deployment/jira-agent \
		jira-agent=$(JIRA_AGENT_REGISTRY)/$(JIRA_AGENT_IMAGE_NAME):$(IMAGE_TAG)
	$(KUBECTL_REMOTE) set image deployment/langgraph-agent \
		langgraph-agent=$(AGENT_REGISTRY)/$(AGENT_IMAGE_NAME):$(IMAGE_TAG)
	$(KUBECTL_REMOTE) rollout status deployment/jira-agent --timeout=120s
	$(KUBECTL_REMOTE) rollout status deployment/langgraph-agent --timeout=120s
	@echo "Deployed with tag: $(IMAGE_TAG)"

# ---------------------------------------------------------------------------
##@ Quick Rebuild
# ---------------------------------------------------------------------------
.PHONY: redeploy redeploy-clean

redeploy: kind-load-all ## Rebuild images, load to kind, and restart deployments
	$(KUBECTL) apply -k deploy/base/
	kubectl config set-context --current --namespace=$(NAMESPACE)
	$(KUBECTL) rollout restart deployment/jira-agent || true
	$(KUBECTL) rollout restart deployment/langgraph-agent || true
	$(KUBECTL) rollout status deployment/jira-agent --timeout=120s || true
	$(KUBECTL) rollout status deployment/langgraph-agent --timeout=120s || true

redeploy-clean: ## Clean slate: delete namespace and redeploy
	$(KUBECTL) delete namespace $(NAMESPACE) --ignore-not-found=true
	$(MAKE) build-all
	$(MAKE) kind-load-all
	$(KUBECTL) apply -k deploy/base/
	kubectl config set-context --current --namespace=$(NAMESPACE)
	@echo ""
	@echo "NOTE: Recreate secrets manually:"
	@echo "  make recreate-secret"

# ---------------------------------------------------------------------------
##@ Deployment
# ---------------------------------------------------------------------------
.PHONY: deploy-all undeploy-all

deploy-all: ## Deploy all services to Kubernetes
	$(KUBECTL) apply -k deploy/base/

undeploy-all: ## Remove all deployments from Kubernetes
	$(KUBECTL) delete namespace $(NAMESPACE) --ignore-not-found=true

# ---------------------------------------------------------------------------
##@ Testing
# ---------------------------------------------------------------------------
.PHONY: test-go test-integration test-smoke test

test-go: ## Run Go tests
	go test -v ./...

test-integration: ## Run integration tests
	pytest tests/integration/ -v

test-smoke: ## Run smoke tests (quick validation)
	pytest tests/smoke/ -v

test: test-go test-integration ## Run all tests

# ---------------------------------------------------------------------------
##@ Logs & Status
# ---------------------------------------------------------------------------
.PHONY: logs-jira-agent logs-langgraph status logs-jira-agent-remote logs-langgraph-remote status-remote

logs-jira-agent: ## View jira-agent logs
	$(KUBECTL) logs -l app=jira-agent --tail=100 -f

logs-langgraph: ## View LangGraph agent logs
	$(KUBECTL) logs -l app=langgraph-agent --tail=100 -f

status: ## Check deployment status
	$(KUBECTL) get pods
	$(KUBECTL) get svc

logs-jira-agent-remote: ## View jira-agent logs (remote cluster)
	$(KUBECTL_REMOTE) logs -l app=jira-agent --tail=100 -f

logs-langgraph-remote: ## View LangGraph agent logs (remote cluster)
	$(KUBECTL_REMOTE) logs -l app=langgraph-agent --tail=100 -f

status-remote: ## Check deployment status (remote cluster)
	$(KUBECTL_REMOTE) get pods
	$(KUBECTL_REMOTE) get svc

# ---------------------------------------------------------------------------
##@ Port Forwarding
# ---------------------------------------------------------------------------
.PHONY: port-forward-jira-agent port-forward-langgraph

port-forward-jira-agent: ## Port forward jira-agent to localhost:8080
	$(KUBECTL) port-forward svc/jira-agent 8080:8080

port-forward-langgraph: ## Port forward LangGraph agent to localhost:8000
	$(KUBECTL) port-forward svc/langgraph-agent-service 8000:8000

# ---------------------------------------------------------------------------
##@ Cluster Management
# ---------------------------------------------------------------------------
.PHONY: create-cluster delete-cluster

create-cluster: ## Create kind cluster
	@bash hack/scripts/quick-start.sh

delete-cluster: ## Delete kind cluster
	kind delete cluster --name $(KIND_CLUSTER)

# ---------------------------------------------------------------------------
##@ Utilities
# ---------------------------------------------------------------------------
.PHONY: clean run-local

clean: ## Clean up build artifacts
	rm -rf bin/

run-local: ## Run jira-agent locally
	go run ./cmd/jira-agent

# ---------------------------------------------------------------------------
##@ Secrets Management
# ---------------------------------------------------------------------------
.PHONY: recreate-secret

recreate-secret: ## Recreate jira-k8s-agent-secret (interactive)
	@$(KUBECTL) delete secret jira-k8s-agent-secret --ignore-not-found=true
	@read -p "Enter Jira URL (e.g., https://jira.example.com): " JIRA_URL && \
	read -p "Enter Jira email: " JIRA_EMAIL && \
	read -sp "Enter Jira API token: " JIRA_TOKEN && \
	echo "" && \
	read -p "Enter Jenkins username (blank to skip): " JENKINS_USER && \
	JENKINS_ARGS="" && \
	if [ -n "$$JENKINS_USER" ]; then \
		read -sp "Enter Jenkins API token: " JENKINS_TOKEN && \
		echo "" && \
		JENKINS_ARGS="--from-literal=jenkins-username=$$JENKINS_USER --from-literal=jenkins-api-token=$$JENKINS_TOKEN"; \
	fi && \
	$(KUBECTL) create secret generic jira-k8s-agent-secret \
		--from-literal=jira-url="$$JIRA_URL" \
		--from-literal=jira-email="$$JIRA_EMAIL" \
		--from-literal=jira-api-token="$$JIRA_TOKEN" \
		$$JENKINS_ARGS && \
	echo "" && \
	echo "Secret created. Restarting deployments..." && \
	$(KUBECTL) rollout restart deployment/jira-agent

# ---------------------------------------------------------------------------
##@ Development
# ---------------------------------------------------------------------------
.PHONY: traces trace traces-errors

traces: ## Fetch recent LangSmith traces (use LIMIT=N to change count, default 5)
	langsmith-fetch traces ./traces --limit $(or $(LIMIT),5) --include-metadata

trace: ## Fetch a specific trace (use ID=<trace-id>)
	@if [ -z "$(ID)" ]; then echo "Usage: make trace ID=<trace-id>"; exit 1; fi
	langsmith-fetch trace $(ID)

traces-errors: ## Fetch recent failed traces
	langsmith-fetch traces ./traces --limit $(or $(LIMIT),10) --include-metadata --error

# LangGraph Agent - Remediation Test Makefile
# Simplified for POC - focuses on essential test scenarios

.PHONY: help

# Agent cluster (where jira-agent + langgraph-agent run)
# Override with: make test-hitl-1 KUBECONFIG=~/.kube/other KUBE_CONTEXT=other
KUBECONFIG ?= ~/.kube/config-sched
export KUBECONFIG
KUBE_CONTEXT ?= test-sched
NAMESPACE ?= jira-k8s-agent
PROD_NAMESPACE ?= production
JIRA_PROJECT ?= GAUDISW
JIRA_ISSUE_TYPE ?= Bug
JIRA_COMPONENT ?= DevOps_K8S

# Target cluster for test fixtures (where broken deployments are created)
# Override with: make test-hitl-1 TARGET_KUBECONFIG=~/.kube/config-other TARGET_KUBE_CONTEXT=other TARGET_CLUSTER=other
TARGET_KUBECONFIG ?= ~/.kube/config-hldc03
TARGET_KUBE_CONTEXT ?= hldc03
TARGET_CLUSTER ?= hldc03

# Shorthand for kubectl on target cluster (where test fixtures live)
TARGET_KUBECTL = KUBECONFIG=$(TARGET_KUBECONFIG) kubectl --context $(TARGET_KUBE_CONTEXT)
# Shorthand for kubectl on agent cluster (where jira-agent + langgraph-agent run)
AGENT_KUBECTL = KUBECONFIG=$(KUBECONFIG) kubectl --context $(KUBE_CONTEXT)

help:
	@echo "LangGraph Agent - Remediation Tests"
	@echo "===================================="
	@echo ""
	@echo "Core Tests:"
	@echo "  make test-remediation-1        - Test: Scale deployment (0 replicas)"
	@echo "  make test-remediation-2        - Test: ConfigMap from historical knowledge"
	@echo "  make test-concurrent-dispatch  - Test: Concurrent dispatch of 2 tickets"
	@echo ""
	@echo "HITL Approval Tests (requires HITL_ENABLED=true):"
	@echo "  make test-hitl-1               - Test 1 with Jira comment approval"
	@echo "  make test-hitl-2               - Test 2 with Jira comment approval"
	@echo "  make test-hitl-5               - Test 5: Jenkins enrichment (0 replicas + Jenkins URL)"
	@echo ""
	@echo "Graph Visualization:"
	@echo "  make visualize-graph           - Print Mermaid diagram of supervisor graph"
	@echo "  make visualize-graph-png       - Save supervisor graph as PNG (graph.png)"
	@echo ""
	@echo "Manual Operations:"
	@echo "  make cleanup-all               - Clean up all test resources"
	@echo ""

# ============================================================================
# GRAPH VISUALIZATION
# ============================================================================

.PHONY: visualize-graph visualize-graph-png

visualize-graph:
	@python -c "from src.supervisor import get_graph_for_studio; print(get_graph_for_studio().get_graph().draw_mermaid())"

visualize-graph-png:
	@python -c "from src.supervisor import get_graph_for_studio; open('graph.png','wb').write(get_graph_for_studio().get_graph().draw_mermaid_png())"
	@echo "Saved to graph.png"

# ============================================================================
# SHARED FUNCTIONS
# ============================================================================

define get_jira_creds
	$(eval JIRA_SERVER := $(shell $(AGENT_KUBECTL) get secret jira-k8s-agent-secret -n $(NAMESPACE) -o jsonpath='{.data.jira-url}' | base64 -d))
	$(eval JIRA_EMAIL := $(shell $(AGENT_KUBECTL) get secret jira-k8s-agent-secret -n $(NAMESPACE) -o jsonpath='{.data.jira-email}' | base64 -d))
	$(eval JIRA_TOKEN := $(shell $(AGENT_KUBECTL) get secret jira-k8s-agent-secret -n $(NAMESPACE) -o jsonpath='{.data.jira-api-token}' | base64 -d))
endef

define eval_cluster_tags
$(eval CLUSTER_TAG := $(if $(TARGET_CLUSTER), ($(TARGET_CLUSTER) g3 node),))
$(eval G3_TAG := $(if $(TARGET_CLUSTER), g3 node,))
endef

# ============================================================================
# TEST 1: Deployment with 0 replicas
# ============================================================================

.PHONY: test-remediation-1-setup test-remediation-1-create-ticket test-remediation-1 test-remediation-1-verify test-remediation-1-cleanup

test-remediation-1-setup:
	@echo "Setting up order-service deployment with 0 replicas on target cluster..."
	@$(TARGET_KUBECTL) create namespace $(PROD_NAMESPACE) --dry-run=client -o yaml | $(TARGET_KUBECTL) apply -f -
	@$(TARGET_KUBECTL) apply -f ../tests/fixtures/k8s-manifests/order-service.yaml
	@$(TARGET_KUBECTL) get deployment order-service -n $(PROD_NAMESPACE)

test-remediation-1-create-ticket:
	@echo "Creating Jira ticket..."
	$(call get_jira_creds)
	$(call eval_cluster_tags)
	@TICKET=$$(curl -s -k -X POST "$(JIRA_SERVER)/rest/api/2/issue" \
		-H "Content-Type: application/json" \
		-u "$(JIRA_EMAIL):$(JIRA_TOKEN)" \
		-d '{"fields": {"project": {"key": "$(JIRA_PROJECT)"}, "summary": "order-service is down in production namespace - no pods running $(G3_TAG)$(CLUSTER_TAG)", "description": "The order-service deployment in the production namespace has no running pods. Service is completely unavailable. Please investigate and restore service.$(if $(TARGET_CLUSTER), Cluster: $(TARGET_CLUSTER). Namespace: production.)", "issuetype": {"name": "$(JIRA_ISSUE_TYPE)"}, "components": [{"name": "$(JIRA_COMPONENT)"}], "labels": ["ai-investigate"], "assignee": {"name": "anoah"}, "customfield_24301": [{"name": "Unknown"}], "customfield_20821": {"value": "No"}}}' | jq -r '.key') && \
	echo "Created ticket: $$TICKET" && \
	echo "$$TICKET" > /tmp/test1-ticket.txt

test-remediation-1: test-remediation-1-cleanup test-remediation-1-setup test-remediation-1-create-ticket
	@TICKET=$$(cat /tmp/test1-ticket.txt) && \
	echo "" && \
	echo "Running Test 1: $$TICKET" && \
	echo "Before state:" && \
	$(TARGET_KUBECTL) get deployment order-service -n $(PROD_NAMESPACE) && \
	echo "" && \
	echo "Waiting 5m for investigation (3m poller + ~2m investigation)..." && \
	sleep 300 && \
	echo "" && \
	$(MAKE) test-remediation-1-verify && \
	echo "" && \
	echo "Keeping resources for inspection (Ctrl+C to keep indefinitely)..." && \
	sleep 180 && \
	$(MAKE) test-remediation-1-cleanup

test-remediation-1-verify:
	@echo "Verifying Test 1 Results:"
	@$(TARGET_KUBECTL) get deployment order-service -n $(PROD_NAMESPACE)
	@$(TARGET_KUBECTL) get pods -n $(PROD_NAMESPACE) -l app=order-service

test-remediation-1-cleanup:
	@echo "Cleaning up test 1..."
	@$(TARGET_KUBECTL) delete deployment order-service -n $(PROD_NAMESPACE) --ignore-not-found
	@$(TARGET_KUBECTL) delete service order-service -n $(PROD_NAMESPACE) --ignore-not-found

# ============================================================================
# TEST 2: Missing ConfigMap with historical knowledge
# ============================================================================

.PHONY: test-remediation-2-create-prior-ticket test-remediation-2-deploy test-remediation-2-create-new-ticket test-remediation-2 test-remediation-2-verify test-remediation-2-cleanup

test-remediation-2-create-prior-ticket:
	@echo "Creating prior resolved ticket with solution..."
	$(call get_jira_creds)
	$(call eval_cluster_tags)
	@PRIOR_TICKET=$$(curl -s -k -X POST "$(JIRA_SERVER)/rest/api/2/issue" \
		-H "Content-Type: application/json" \
		-u "$(JIRA_EMAIL):$(JIRA_TOKEN)" \
		-d '{"fields": {"project": {"key": "$(JIRA_PROJECT)"}, "summary": "payment-service pods failing in production namespace - CreateContainerConfigError $(G3_TAG)$(CLUSTER_TAG)", "description": "The payment-service deployment pods in the production namespace are failing with CreateContainerConfigError. The pods cannot start because they reference a ConfigMap named payment-config that does not exist in the production namespace.$(if $(TARGET_CLUSTER), Cluster: $(TARGET_CLUSTER). Namespace: production.)", "issuetype": {"name": "$(JIRA_ISSUE_TYPE)"}, "components": [{"name": "$(JIRA_COMPONENT)"}], "labels": ["ai-agent-investigated"], "assignee": {"name": "anoah"}, "customfield_24301": [{"name": "Unknown"}], "customfield_20821": {"value": "No"}}}' | jq -r '.key') && \
	echo "Created prior ticket: $$PRIOR_TICKET" && \
	curl -s -k -X POST "$(JIRA_SERVER)/rest/api/2/issue/$$PRIOR_TICKET/comment" \
		-H "Content-Type: application/json" \
		-u "$(JIRA_EMAIL):$(JIRA_TOKEN)" \
		-d '{"body": "Fixed by creating the payment-config ConfigMap with these values:\n\nkubectl create configmap payment-config -n production --from-literal=DATABASE_URL=postgres://payments-db.production:5432/payments --from-literal=REDIS_HOST=redis.production:6379\n\nThe pods recovered after the ConfigMap was created."}' > /dev/null && \
	echo "Added resolution comment" && \
	TRANSITION_ID=$$(curl -s -k "$(JIRA_SERVER)/rest/api/2/issue/$$PRIOR_TICKET/transitions" \
		-u "$(JIRA_EMAIL):$(JIRA_TOKEN)" | jq -r '.transitions[] | select(.name | test("Done|Resolve|Close"; "i")) | .id' | head -1) && \
	if [ -n "$$TRANSITION_ID" ]; then \
		curl -s -k -X POST "$(JIRA_SERVER)/rest/api/2/issue/$$PRIOR_TICKET/transitions" \
			-H "Content-Type: application/json" \
			-u "$(JIRA_EMAIL):$(JIRA_TOKEN)" \
			-d "{\"transition\": {\"id\": \"$$TRANSITION_ID\"}, \"fields\": {\"resolution\": {\"name\": \"Done\"}}}" > /dev/null && \
		echo "Resolved ticket: $$PRIOR_TICKET"; \
	fi && \
	sleep 10 && \
	echo "$$PRIOR_TICKET" > /tmp/test2-prior-ticket.txt

test-remediation-2-deploy:
	@echo "Deploying payment-service (missing ConfigMap)..."
	@$(TARGET_KUBECTL) delete configmap payment-config -n $(PROD_NAMESPACE) --ignore-not-found
	@$(TARGET_KUBECTL) delete deployment payment-service -n $(PROD_NAMESPACE) --ignore-not-found
	@sleep 2
	@$(TARGET_KUBECTL) apply -f ../tests/fixtures/k8s-manifests/payment-service.yaml
	@sleep 5
	@$(TARGET_KUBECTL) get pods -n $(PROD_NAMESPACE) -l app=payment-service

test-remediation-2-create-new-ticket:
	@echo "Creating NEW ticket for current issue..."
	$(call get_jira_creds)
	$(call eval_cluster_tags)
	@TICKET=$$(curl -s -k -X POST "$(JIRA_SERVER)/rest/api/2/issue" \
		-H "Content-Type: application/json" \
		-u "$(JIRA_EMAIL):$(JIRA_TOKEN)" \
		-d '{"fields": {"project": {"key": "$(JIRA_PROJECT)"}, "summary": "payment-service pods failing in production namespace - CreateContainerConfigError $(G3_TAG)$(CLUSTER_TAG)", "description": "The payment-service deployment pods in the production namespace are failing to start with CreateContainerConfigError. Please investigate and fix.$(if $(TARGET_CLUSTER), Cluster: $(TARGET_CLUSTER). Namespace: production.)", "issuetype": {"name": "$(JIRA_ISSUE_TYPE)"}, "components": [{"name": "$(JIRA_COMPONENT)"}], "labels": ["ai-investigate"], "assignee": {"name": "anoah"}, "customfield_24301": [{"name": "Unknown"}], "customfield_20821": {"value": "No"}}}' | jq -r '.key') && \
	echo "Created ticket: $$TICKET" && \
	echo "$$TICKET" > /tmp/test2-ticket.txt

test-remediation-2: test-remediation-2-cleanup test-remediation-2-create-prior-ticket test-remediation-2-deploy test-remediation-2-create-new-ticket
	@TICKET=$$(cat /tmp/test2-ticket.txt) && \
	PRIOR=$$(cat /tmp/test2-prior-ticket.txt 2>/dev/null || echo "unknown") && \
	echo "" && \
	echo "Running Test 2: $$TICKET (should find solution from $$PRIOR)" && \
	echo "Before state:" && \
	$(TARGET_KUBECTL) get pods -n $(PROD_NAMESPACE) -l app=payment-service && \
	$(TARGET_KUBECTL) get configmap payment-config -n $(PROD_NAMESPACE) 2>/dev/null || echo "ConfigMap payment-config: NOT FOUND" && \
	echo "" && \
	echo "Waiting 5m for investigation (3m poller + ~2m investigation)..." && \
	sleep 300 && \
	echo "" && \
	$(MAKE) test-remediation-2-verify && \
	echo "" && \
	echo "Keeping resources for inspection (Ctrl+C to keep indefinitely)..." && \
	sleep 180 && \
	$(MAKE) test-remediation-2-cleanup

test-remediation-2-verify:
	@echo "Verifying Test 2 Results:"
	@$(TARGET_KUBECTL) get configmap payment-config -n $(PROD_NAMESPACE) -o yaml 2>/dev/null || echo "ConfigMap payment-config: NOT FOUND"
	@$(TARGET_KUBECTL) get pods -n $(PROD_NAMESPACE) -l app=payment-service

test-remediation-2-cleanup:
	@echo "Cleaning up test 2..."
	@$(TARGET_KUBECTL) delete deployment payment-service -n $(PROD_NAMESPACE) --ignore-not-found
	@$(TARGET_KUBECTL) delete configmap payment-config -n $(PROD_NAMESPACE) --ignore-not-found

# ============================================================================
# CONCURRENT DISPATCH TEST
# ============================================================================

.PHONY: test-concurrent-dispatch-setup test-concurrent-dispatch-create-tickets test-concurrent-dispatch test-concurrent-dispatch-verify test-concurrent-dispatch-cleanup

test-concurrent-dispatch-cleanup:
	@echo "Cleaning up concurrent dispatch test..."
	@$(AGENT_KUBECTL) delete deployment order-service -n $(PROD_NAMESPACE) --ignore-not-found
	@$(AGENT_KUBECTL) delete service order-service -n $(PROD_NAMESPACE) --ignore-not-found
	@$(AGENT_KUBECTL) delete deployment inventory-service -n $(PROD_NAMESPACE) --ignore-not-found
	@$(AGENT_KUBECTL) delete service inventory-service -n $(PROD_NAMESPACE) --ignore-not-found
	@rm -f /tmp/concurrent-ticket1.txt /tmp/concurrent-ticket2.txt 2>/dev/null || true

test-concurrent-dispatch-setup:
	@echo "Setting up 2 deployments with 0 replicas..."
	@$(AGENT_KUBECTL) apply -f ../tests/fixtures/k8s-manifests/order-service.yaml
	@$(AGENT_KUBECTL) apply -f ../tests/fixtures/k8s-manifests/inventory-service.yaml
	@$(AGENT_KUBECTL) get deployment order-service inventory-service -n $(PROD_NAMESPACE)

test-concurrent-dispatch-create-tickets:
	@echo "Creating 2 Jira tickets..."
	$(call get_jira_creds)
	@TICKET1=$$(curl -s -k -X POST "$(JIRA_SERVER)/rest/api/2/issue" \
		-H "Content-Type: application/json" \
		-u "$(JIRA_EMAIL):$(JIRA_TOKEN)" \
		-d '{"fields": {"project": {"key": "$(JIRA_PROJECT)"}, "summary": "order-service is down in production - no pods running", "description": "The order-service deployment in production namespace has no running pods. Service is completely unavailable. Please investigate and restore service.", "issuetype": {"name": "$(JIRA_ISSUE_TYPE)"}, "components": [{"name": "$(JIRA_COMPONENT)"}], "labels": ["ai-investigate"], "assignee": {"name": "anoah"}, "customfield_24301": [{"name": "Unknown"}], "customfield_20821": {"value": "No"}}}' | jq -r '.key') && \
	TICKET2=$$(curl -s -k -X POST "$(JIRA_SERVER)/rest/api/2/issue" \
		-H "Content-Type: application/json" \
		-u "$(JIRA_EMAIL):$(JIRA_TOKEN)" \
		-d '{"fields": {"project": {"key": "$(JIRA_PROJECT)"}, "summary": "inventory-service is down in production - no pods running", "description": "The inventory-service deployment in production namespace has no running pods. Service is completely unavailable. Please investigate and restore service.", "issuetype": {"name": "$(JIRA_ISSUE_TYPE)"}, "components": [{"name": "$(JIRA_COMPONENT)"}], "labels": ["ai-investigate"], "assignee": {"name": "anoah"}, "customfield_24301": [{"name": "Unknown"}], "customfield_20821": {"value": "No"}}}' | jq -r '.key') && \
	echo "Created ticket 1: $$TICKET1" && \
	echo "Created ticket 2: $$TICKET2" && \
	echo "$$TICKET1" > /tmp/concurrent-ticket1.txt && \
	echo "$$TICKET2" > /tmp/concurrent-ticket2.txt

test-concurrent-dispatch: test-concurrent-dispatch-cleanup test-concurrent-dispatch-setup test-concurrent-dispatch-create-tickets
	@TICKET1=$$(cat /tmp/concurrent-ticket1.txt) && \
	TICKET2=$$(cat /tmp/concurrent-ticket2.txt) && \
	$(call get_jira_creds) && \
	echo "" && \
	echo "Running Concurrent Dispatch Test" && \
	echo "Ticket 1: $$TICKET1" && \
	echo "Ticket 2: $$TICKET2" && \
	echo "" && \
	echo "Waiting for Go poller to dispatch both tickets (3m poll interval)..." && \
	echo "Checking for concurrent dispatch in jira-agent logs..." && \
	for i in $$(seq 1 48); do \
		LOGS=$$($(AGENT_KUBECTL) logs -l app=jira-agent -n $(NAMESPACE) --tail=20 2>/dev/null) && \
		if echo "$$LOGS" | grep -q "dispatching tickets concurrently"; then \
			echo "" && \
			echo "Concurrent dispatch detected!" && \
			echo "$$LOGS" | grep -E "dispatching tickets concurrently" && \
			break; \
		fi && \
		printf "\r  Waiting... [%02d/48]" "$$i" && \
		sleep 5; \
	done && \
	echo "" && \
	echo "Waiting 3m for investigations to complete..." && \
	sleep 180 && \
	echo "" && \
	echo "Checking for HITL approval..." && \
	STATUS1=$$($(AGENT_KUBECTL) exec -n $(NAMESPACE) deployment/langgraph-agent -- \
		curl -s "http://localhost:8000/investigate/$$TICKET1/pending" 2>/dev/null | jq -r '.status // "not_found"' 2>/dev/null || echo "not_found") && \
	STATUS2=$$($(AGENT_KUBECTL) exec -n $(NAMESPACE) deployment/langgraph-agent -- \
		curl -s "http://localhost:8000/investigate/$$TICKET2/pending" 2>/dev/null | jq -r '.status // "not_found"' 2>/dev/null || echo "not_found") && \
	echo "Ticket 1 status: $$STATUS1" && \
	echo "Ticket 2 status: $$STATUS2" && \
	if [ "$$STATUS1" = "pending_approval" ] || [ "$$STATUS2" = "pending_approval" ]; then \
		echo "" && \
		echo "HITL approval needed. Add 'approve' comment in Jira:" && \
		if [ "$$STATUS1" = "pending_approval" ]; then echo "  - $(JIRA_SERVER)/browse/$$TICKET1"; fi && \
		if [ "$$STATUS2" = "pending_approval" ]; then echo "  - $(JIRA_SERVER)/browse/$$TICKET2"; fi && \
		echo "" && \
		echo "Waiting 5m for approvals..." && \
		for i in $$(seq 1 30); do \
			S1=$$($(AGENT_KUBECTL) exec -n $(NAMESPACE) deployment/langgraph-agent -- \
				curl -s "http://localhost:8000/investigate/$$TICKET1/pending" 2>/dev/null | jq -r '.status // "unknown"' 2>/dev/null || echo "unknown") && \
			S2=$$($(AGENT_KUBECTL) exec -n $(NAMESPACE) deployment/langgraph-agent -- \
				curl -s "http://localhost:8000/investigate/$$TICKET2/pending" 2>/dev/null | jq -r '.status // "unknown"' 2>/dev/null || echo "unknown") && \
			if [ "$$S1" != "pending_approval" ] && [ "$$S2" != "pending_approval" ]; then \
				echo "" && \
				echo "Both workflows completed!" && \
				break; \
			fi && \
			printf "\r  Waiting... [%02d/30] T1:$$S1 T2:$$S2" "$$i" && \
			sleep 10; \
		done; \
	fi && \
	echo "" && \
	$(MAKE) test-concurrent-dispatch-verify

test-concurrent-dispatch-verify:
	@TICKET1=$$(cat /tmp/concurrent-ticket1.txt 2>/dev/null || echo "unknown") && \
	TICKET2=$$(cat /tmp/concurrent-ticket2.txt 2>/dev/null || echo "unknown") && \
	echo "" && \
	echo "Concurrent Dispatch Test Results" && \
	echo "Ticket 1: $$TICKET1" && \
	echo "Ticket 2: $$TICKET2" && \
	echo "" && \
	$(AGENT_KUBECTL) get deployment order-service inventory-service -n $(PROD_NAMESPACE) && \
	$(AGENT_KUBECTL) get pods -n $(PROD_NAMESPACE) -l 'app in (order-service, inventory-service)'

# ============================================================================
# HITL TESTS (Jira Comment Approval)
# ============================================================================

.PHONY: test-hitl-1 test-hitl-2 test-hitl-3 test-hitl-5

test-hitl-1: test-remediation-1-cleanup test-remediation-1-setup test-remediation-1-create-ticket
	$(call get_jira_creds)
	@TICKET=$$(cat /tmp/test1-ticket.txt) && \
	echo "" && \
	echo "HITL Test 1: $$TICKET" && \
	echo "Waiting for HITL checkpoint (up to 20 minutes)..." && \
	STATUS="not_found" && \
	for i in $$(seq 1 120); do \
		STATUS=$$($(AGENT_KUBECTL) exec -n $(NAMESPACE) deployment/langgraph-agent -- \
			curl -s "http://localhost:8000/investigate/$$TICKET/pending" 2>/dev/null | jq -r '.status // "not_found"' 2>/dev/null || echo "not_found") && \
		if [ "$$STATUS" = "pending_approval" ]; then break; fi && \
		if [ "$$STATUS" = "completed" ]; then break; fi && \
		printf "\r  Waiting... [%03d/120]" "$$i" && \
		sleep 10; \
	done && \
	echo "" && \
	if [ "$$STATUS" = "pending_approval" ]; then \
		echo "" && \
		echo "HITL checkpoint reached. Add 'approve' comment to:" && \
		echo "  $(JIRA_SERVER)/browse/$$TICKET" && \
		echo "" && \
		echo "Waiting for approval (20 minutes)..." && \
		for i in $$(seq 1 120); do \
			POLL_STATUS=$$($(AGENT_KUBECTL) exec -n $(NAMESPACE) deployment/langgraph-agent -- \
				curl -s "http://localhost:8000/investigate/$$TICKET/pending" 2>/dev/null | jq -r '.status // "unknown"' 2>/dev/null || echo "unknown") && \
			if [ "$$POLL_STATUS" = "completed" ]; then \
				echo "" && \
				echo "Workflow completed!" && \
				break; \
			fi && \
			printf "\r  Waiting... [%03d/120]" "$$i" && \
			sleep 10; \
		done; \
	fi && \
	echo "" && \
	$(MAKE) test-remediation-1-verify

test-hitl-2: test-remediation-2-cleanup test-remediation-2-create-prior-ticket test-remediation-2-deploy test-remediation-2-create-new-ticket
	$(call get_jira_creds)
	@TICKET=$$(cat /tmp/test2-ticket.txt) && \
	echo "" && \
	echo "HITL Test 2: $$TICKET" && \
	echo "Waiting for HITL checkpoint (up to 6 minutes)..." && \
	STATUS="not_found" && \
	for i in $$(seq 1 72); do \
		RESPONSE=$$($(AGENT_KUBECTL) exec -n $(NAMESPACE) deployment/langgraph-agent -- \
			curl -s "http://localhost:8000/investigate/$$TICKET/pending" 2>/dev/null) && \
		STATUS=$$(echo "$$RESPONSE" | jq -r '.status // "not_found"' 2>/dev/null || echo "not_found") && \
		if [ "$$STATUS" = "pending_approval" ]; then break; fi && \
		if [ "$$STATUS" = "completed" ]; then break; fi && \
		printf "\r  Waiting... [%02d/72]" "$$i" && \
		sleep 5; \
	done && \
	echo "" && \
	if [ "$$STATUS" = "pending_approval" ]; then \
		echo "" && \
		echo "HITL checkpoint reached. Add 'approve' comment to:" && \
		echo "  $(JIRA_SERVER)/browse/$$TICKET" && \
		echo "" && \
		echo "Waiting for approval (10 minutes)..." && \
		for i in $$(seq 1 60); do \
			POLL_STATUS=$$($(AGENT_KUBECTL) exec -n $(NAMESPACE) deployment/langgraph-agent -- \
				curl -s "http://localhost:8000/investigate/$$TICKET/pending" 2>/dev/null | jq -r '.status // "unknown"' 2>/dev/null || echo "unknown") && \
			if [ "$$POLL_STATUS" = "completed" ]; then \
				echo "" && \
				echo "Workflow completed!" && \
				break; \
			fi && \
			printf "\r  Waiting... [%02d/60]" "$$i" && \
			sleep 10; \
		done; \
	fi && \
	echo "" && \
	$(MAKE) test-remediation-2-verify

# ============================================================================
# TEST 3: Multi-step remediation (missing ConfigMap + 0 replicas)
# Exercises the multi-step remediation plan feature: the agent must generate
# a plan with 2+ steps (create ConfigMap AND scale deployment) in one attempt.
# ============================================================================

.PHONY: test-hitl-3-setup test-hitl-3-create-prior-ticket test-hitl-3-deploy test-hitl-3-create-ticket test-hitl-3 test-hitl-3-verify test-hitl-3-cleanup

test-hitl-3-cleanup:
	@echo "Cleaning up test 3..."
	@$(TARGET_KUBECTL) delete deployment notification-service -n $(PROD_NAMESPACE) --ignore-not-found
	@$(TARGET_KUBECTL) delete service notification-service -n $(PROD_NAMESPACE) --ignore-not-found
	@$(TARGET_KUBECTL) delete configmap notification-config -n $(PROD_NAMESPACE) --ignore-not-found

test-hitl-3-create-prior-ticket:
	@echo "Creating prior resolved ticket with multi-step solution..."
	$(call get_jira_creds)
	$(call eval_cluster_tags)
	@PRIOR_TICKET=$$(curl -s -k -X POST "$(JIRA_SERVER)/rest/api/2/issue" \
		-H "Content-Type: application/json" \
		-u "$(JIRA_EMAIL):$(JIRA_TOKEN)" \
		-d '{"fields": {"project": {"key": "$(JIRA_PROJECT)"}, "summary": "notification-service is down in production - missing config and 0 replicas $(G3_TAG)$(CLUSTER_TAG)", "description": "The notification-service deployment in the production namespace has 0 replicas and is missing the notification-config ConfigMap. Pods cannot start due to both issues.$(if $(TARGET_CLUSTER), Cluster: $(TARGET_CLUSTER). Namespace: production.)", "issuetype": {"name": "$(JIRA_ISSUE_TYPE)"}, "components": [{"name": "$(JIRA_COMPONENT)"}], "labels": ["ai-agent-investigated"], "assignee": {"name": "anoah"}, "customfield_24301": [{"name": "Unknown"}], "customfield_20821": {"value": "No"}}}' | jq -r '.key') && \
	echo "Created prior ticket: $$PRIOR_TICKET" && \
	curl -s -k -X POST "$(JIRA_SERVER)/rest/api/2/issue/$$PRIOR_TICKET/comment" \
		-H "Content-Type: application/json" \
		-u "$(JIRA_EMAIL):$(JIRA_TOKEN)" \
		-d '{"body": "Fixed by performing two steps:\n\n1. Created the notification-config ConfigMap:\nkubectl create configmap notification-config -n production --from-literal=SMTP_HOST=smtp.production.internal:587 --from-literal=SMTP_PORT=587 --from-literal=SENDER_EMAIL=alerts@example.com\n\n2. Scaled the deployment from 0 to 1 replica:\nkubectl scale deployment notification-service -n production --replicas=1\n\nBoth steps were required. The ConfigMap alone would not fix it because replicas were 0. Scaling alone would not fix it because the pods need the ConfigMap to start."}' > /dev/null && \
	echo "Added multi-step resolution comment" && \
	TRANSITION_ID=$$(curl -s -k "$(JIRA_SERVER)/rest/api/2/issue/$$PRIOR_TICKET/transitions" \
		-u "$(JIRA_EMAIL):$(JIRA_TOKEN)" | jq -r '.transitions[] | select(.name | test("Done|Resolve|Close"; "i")) | .id' | head -1) && \
	if [ -n "$$TRANSITION_ID" ]; then \
		curl -s -k -X POST "$(JIRA_SERVER)/rest/api/2/issue/$$PRIOR_TICKET/transitions" \
			-H "Content-Type: application/json" \
			-u "$(JIRA_EMAIL):$(JIRA_TOKEN)" \
			-d "{\"transition\": {\"id\": \"$$TRANSITION_ID\"}, \"fields\": {\"resolution\": {\"name\": \"Done\"}}}" > /dev/null && \
		echo "Resolved ticket: $$PRIOR_TICKET"; \
	fi && \
	sleep 10 && \
	echo "$$PRIOR_TICKET" > /tmp/test3-prior-ticket.txt

test-hitl-3-setup:
	@echo "Setting up notification-service (0 replicas + missing ConfigMap) on target cluster..."
	@$(TARGET_KUBECTL) create namespace $(PROD_NAMESPACE) --dry-run=client -o yaml | $(TARGET_KUBECTL) apply -f -
	@$(TARGET_KUBECTL) delete configmap notification-config -n $(PROD_NAMESPACE) --ignore-not-found
	@$(TARGET_KUBECTL) apply -f ../tests/fixtures/k8s-manifests/notification-service.yaml
	@$(TARGET_KUBECTL) get deployment notification-service -n $(PROD_NAMESPACE)

test-hitl-3-create-ticket:
	@echo "Creating ticket describing BOTH problems..."
	$(call get_jira_creds)
	$(call eval_cluster_tags)
	@TICKET=$$(curl -s -k -X POST "$(JIRA_SERVER)/rest/api/2/issue" \
		-H "Content-Type: application/json" \
		-u "$(JIRA_EMAIL):$(JIRA_TOKEN)" \
		-d '{"fields": {"project": {"key": "$(JIRA_PROJECT)"}, "summary": "notification-service is down in production - missing config and 0 replicas $(G3_TAG)$(CLUSTER_TAG)", "description": "The notification-service deployment in the production namespace is completely down. It has 0 replicas and the pods reference a ConfigMap named notification-config that does not exist. Both issues need to be fixed for the service to come back up. The ConfigMap should contain SMTP_HOST, SMTP_PORT, and SENDER_EMAIL values. Please investigate and restore service.$(if $(TARGET_CLUSTER), Cluster: $(TARGET_CLUSTER). Namespace: production.)", "issuetype": {"name": "$(JIRA_ISSUE_TYPE)"}, "components": [{"name": "$(JIRA_COMPONENT)"}], "labels": ["ai-investigate"], "assignee": {"name": "anoah"}, "customfield_24301": [{"name": "Unknown"}], "customfield_20821": {"value": "No"}}}' | jq -r '.key') && \
	echo "Created ticket: $$TICKET" && \
	echo "$$TICKET" > /tmp/test3-ticket.txt

test-hitl-3: test-hitl-3-cleanup test-hitl-3-create-prior-ticket test-hitl-3-setup test-hitl-3-create-ticket
	$(call get_jira_creds)
	@TICKET=$$(cat /tmp/test3-ticket.txt) && \
	PRIOR=$$(cat /tmp/test3-prior-ticket.txt 2>/dev/null || echo "unknown") && \
	echo "" && \
	echo "HITL Test 3 (Multi-Step): $$TICKET (should find solution from $$PRIOR)" && \
	echo "Expected: agent generates 2-step plan (create ConfigMap + scale deployment)" && \
	echo "" && \
	echo "Before state:" && \
	$(TARGET_KUBECTL) get deployment notification-service -n $(PROD_NAMESPACE) && \
	$(TARGET_KUBECTL) get configmap notification-config -n $(PROD_NAMESPACE) 2>/dev/null || echo "ConfigMap notification-config: NOT FOUND" && \
	echo "" && \
	echo "Waiting for HITL checkpoint (up to 20 minutes)..." && \
	STATUS="not_found" && \
	for i in $$(seq 1 120); do \
		STATUS=$$($(AGENT_KUBECTL) exec -n $(NAMESPACE) deployment/langgraph-agent -- \
			curl -s "http://localhost:8000/investigate/$$TICKET/pending" 2>/dev/null | jq -r '.status // "not_found"' 2>/dev/null || echo "not_found") && \
		if [ "$$STATUS" = "pending_approval" ]; then break; fi && \
		if [ "$$STATUS" = "completed" ]; then break; fi && \
		printf "\r  Waiting... [%03d/120]" "$$i" && \
		sleep 10; \
	done && \
	echo "" && \
	if [ "$$STATUS" = "pending_approval" ]; then \
		echo "" && \
		echo "HITL checkpoint reached. Review the multi-step plan and add 'approve' comment to:" && \
		echo "  $(JIRA_SERVER)/browse/$$TICKET" && \
		echo "" && \
		echo "Waiting for approval (20 minutes)..." && \
		for i in $$(seq 1 120); do \
			POLL_STATUS=$$($(AGENT_KUBECTL) exec -n $(NAMESPACE) deployment/langgraph-agent -- \
				curl -s "http://localhost:8000/investigate/$$TICKET/pending" 2>/dev/null | jq -r '.status // "unknown"' 2>/dev/null || echo "unknown") && \
			if [ "$$POLL_STATUS" = "completed" ]; then \
				echo "" && \
				echo "Workflow completed!" && \
				break; \
			fi && \
			printf "\r  Waiting... [%03d/120]" "$$i" && \
			sleep 10; \
		done; \
	fi && \
	echo "" && \
	$(MAKE) test-hitl-3-verify

test-hitl-3-verify:
	@echo "Verifying Test 3 Results (Multi-Step):"
	@echo ""
	@echo "1. ConfigMap should exist with SMTP values:"
	@$(TARGET_KUBECTL) get configmap notification-config -n $(PROD_NAMESPACE) -o yaml 2>/dev/null || echo "  FAIL: ConfigMap notification-config NOT FOUND"
	@echo ""
	@echo "2. Deployment should have running pods:"
	@$(TARGET_KUBECTL) get deployment notification-service -n $(PROD_NAMESPACE)
	@$(TARGET_KUBECTL) get pods -n $(PROD_NAMESPACE) -l app=notification-service

# ============================================================================
# TEST 4: Post-fix loop (scale up -> CrashLoopBackOff -> fix ConfigMap)
# Tests the post-fix diagnosis loop: agent fixes one issue, a NEW issue
# appears, and the agent loops to fix the new issue.
# Flow:
#   1. Deployment with 0 replicas + envFrom configMapRef (optional: true)
#   2. ConfigMap EXISTS during investigation (agent sees it's present)
#   3. Agent diagnoses: scale up (ConfigMap looks fine)
#   4. At HITL checkpoint, TEST DELETES ConfigMap (injecting second issue)
#   5. User approves -> agent scales to 1
#   6. Pod starts but CrashLoopBackOff (DATABASE_URL empty, ConfigMap gone)
#   7. Post-fix loop detects new issue -> re-diagnoses -> creates ConfigMap
#   8. Second HITL checkpoint -> user approves -> pods recover
# NOTE: Requires TWO manual approvals in Jira.
# ============================================================================

.PHONY: test-hitl-4-setup test-hitl-4-create-ticket test-hitl-4 test-hitl-4-verify test-hitl-4-cleanup

test-hitl-4-cleanup:
	@echo "Cleaning up test 4..."
	@$(TARGET_KUBECTL) delete deployment crashloop-service -n $(PROD_NAMESPACE) --ignore-not-found
	@$(TARGET_KUBECTL) delete service crashloop-service -n $(PROD_NAMESPACE) --ignore-not-found
	@$(TARGET_KUBECTL) delete configmap crashloop-config -n $(PROD_NAMESPACE) --ignore-not-found
	@rm -f /tmp/test4-ticket.txt 2>/dev/null || true

test-hitl-4-setup:
	@echo "Setting up crashloop-service (0 replicas + ConfigMap present) on target cluster..."
	@$(TARGET_KUBECTL) create namespace $(PROD_NAMESPACE) --dry-run=client -o yaml | $(TARGET_KUBECTL) apply -f -
	@$(TARGET_KUBECTL) apply -f ../tests/fixtures/k8s-manifests/crashloop-service.yaml
	@echo "ConfigMap crashloop-config present (will be deleted at HITL checkpoint):"
	@$(TARGET_KUBECTL) get configmap crashloop-config -n $(PROD_NAMESPACE)
	@$(TARGET_KUBECTL) get deployment crashloop-service -n $(PROD_NAMESPACE)

test-hitl-4-create-ticket:
	@echo "Creating ticket for post-fix loop test (mentions only 0 replicas)..."
	$(call get_jira_creds)
	$(call eval_cluster_tags)
	@TICKET=$$(curl -s -k -X POST "$(JIRA_SERVER)/rest/api/2/issue" \
		-H "Content-Type: application/json" \
		-u "$(JIRA_EMAIL):$(JIRA_TOKEN)" \
		-d '{"fields": {"project": {"key": "$(JIRA_PROJECT)"}, "summary": "crashloop-service has 0 replicas in production $(G3_TAG)$(CLUSTER_TAG)", "description": "The crashloop-service deployment in the production namespace has 0 replicas and no pods are running. Service is completely unavailable. Please investigate and restore service.$(if $(TARGET_CLUSTER), Cluster: $(TARGET_CLUSTER). Namespace: production.)", "issuetype": {"name": "$(JIRA_ISSUE_TYPE)"}, "components": [{"name": "$(JIRA_COMPONENT)"}], "labels": ["ai-investigate"], "assignee": {"name": "anoah"}, "customfield_24301": [{"name": "Unknown"}], "customfield_20821": {"value": "No"}}}' | jq -r '.key') && \
	echo "Created ticket: $$TICKET" && \
	echo "$$TICKET" > /tmp/test4-ticket.txt

test-hitl-4: test-hitl-4-cleanup test-hitl-4-setup test-hitl-4-create-ticket
	$(call get_jira_creds)
	@TICKET=$$(cat /tmp/test4-ticket.txt) && \
	echo "" && \
	echo "============================================================" && \
	echo "HITL Test 4 (Post-Fix Loop): $$TICKET" && \
	echo "Scenario: 0 replicas -> scale -> CrashLoopBackOff -> fix ConfigMap" && \
	echo "NOTE: This test requires TWO approvals in Jira!" && \
	echo "============================================================" && \
	echo "" && \
	echo "Before state:" && \
	$(TARGET_KUBECTL) get deployment crashloop-service -n $(PROD_NAMESPACE) && \
	$(TARGET_KUBECTL) get configmap crashloop-config -n $(PROD_NAMESPACE) && \
	echo "" && \
	echo "--- Phase 1: Waiting for first HITL checkpoint ---" && \
	PHASE="waiting_first_hitl" && \
	for i in $$(seq 1 360); do \
		STATUS=$$($(AGENT_KUBECTL) exec -n $(NAMESPACE) deployment/langgraph-agent -- \
			curl -s "http://localhost:8000/investigate/$$TICKET/pending" 2>/dev/null | jq -r '.status // "not_found"' 2>/dev/null || echo "not_found") && \
		case "$$PHASE" in \
			waiting_first_hitl) \
				if [ "$$STATUS" = "pending_approval" ]; then \
					echo "" && \
					echo "==> First HITL checkpoint reached!" && \
					echo "    Injecting second issue: deleting ConfigMap..." && \
					$(TARGET_KUBECTL) delete configmap crashloop-config -n $(PROD_NAMESPACE) --ignore-not-found && \
					echo "    ConfigMap deleted. After approval, pods will CrashLoopBackOff." && \
					echo "" && \
					echo ">>> FIRST APPROVAL: Add 'approve' comment to $(JIRA_SERVER)/browse/$$TICKET" && \
					echo "    (Agent will scale deployment to 1 replica)" && \
					echo "" && \
					PHASE="waiting_first_done" && \
					continue; \
				fi && \
				if [ "$$STATUS" = "completed" ]; then \
					echo "" && \
					echo "Workflow completed before HITL (unexpected)" && \
					break; \
				fi \
				;; \
			waiting_first_done) \
				if [ "$$STATUS" != "pending_approval" ]; then \
					echo "==> First approval processed. Agent executing scale-up..." && \
					PHASE="waiting_second_hitl" && \
					continue; \
				fi \
				;; \
			waiting_second_hitl) \
				if [ "$$STATUS" = "pending_approval" ]; then \
					echo "" && \
					echo "==> POST-FIX LOOP TRIGGERED! Second HITL checkpoint reached!" && \
					echo "    Agent detected CrashLoopBackOff and re-diagnosed." && \
					echo "" && \
					echo ">>> SECOND APPROVAL: Add 'approve' comment to $(JIRA_SERVER)/browse/$$TICKET" && \
					echo "    (Agent will create ConfigMap to fix CrashLoopBackOff)" && \
					echo "" && \
					PHASE="waiting_completion" && \
					continue; \
				fi && \
				if [ "$$STATUS" = "completed" ]; then \
					echo "" && \
					echo "Workflow completed without post-fix loop." && \
					echo "(Agent may have resolved the issue without a second cycle)" && \
					break; \
				fi \
				;; \
			waiting_completion) \
				if [ "$$STATUS" = "completed" ]; then \
					echo "" && \
					echo "==> Workflow completed after post-fix loop!" && \
					break; \
				fi \
				;; \
		esac && \
		printf "\r  [$$PHASE] Waiting... [%03d/360]" "$$i" && \
		sleep 10; \
	done && \
	echo "" && \
	echo "" && \
	$(MAKE) test-hitl-4-verify

test-hitl-4-verify:
	@echo "Verifying Test 4 Results (Post-Fix Loop):"
	@echo "NOTE: Verifying against TARGET cluster where agent actually runs"
	@echo ""
	@echo "1. ConfigMap should exist (recreated by agent):"
	@$(TARGET_KUBECTL) get configmap crashloop-config -n $(PROD_NAMESPACE) -o yaml 2>/dev/null || echo "  FAIL: ConfigMap crashloop-config NOT FOUND"
	@echo ""
	@echo "2. Deployment should have running pods:"
	@$(TARGET_KUBECTL) get deployment crashloop-service -n $(PROD_NAMESPACE)
	@$(TARGET_KUBECTL) get pods -n $(PROD_NAMESPACE) -l app=crashloop-service
	@echo ""
	@echo "3. Pod logs should show successful start:"
	@$(TARGET_KUBECTL) logs -l app=crashloop-service -n $(PROD_NAMESPACE) --tail=5 2>/dev/null || echo "  (no logs available)"

# ============================================================================
# TEST 5: Jenkins enrichment (0 replicas + Jenkins URL in ticket)
# Tests that the investigate_jenkins node extracts Jenkins build info
# from URLs in the Jira ticket description. Uses order-service with
# 0 replicas as the K8s fixture so the full pipeline runs.
# Override JENKINS_URL to test with a different failed build.
# ============================================================================

JENKINS_URL ?= https://qa-jenkins-ctrl02.habana-labs.com/job/qa_jobs/job/qa_testers/job/habanaqa_core/job/cpuPacketCoreTest_pytorch_gaudi2-ankvm_v1_23_1/4/

.PHONY: test-hitl-5-setup test-hitl-5-create-ticket test-hitl-5 test-hitl-5-verify test-hitl-5-cleanup

test-hitl-5-cleanup:
	@echo "Cleaning up test 5..."
	@$(TARGET_KUBECTL) delete deployment order-service -n $(PROD_NAMESPACE) --ignore-not-found
	@$(TARGET_KUBECTL) delete service order-service -n $(PROD_NAMESPACE) --ignore-not-found
	@rm -f /tmp/test5-ticket.txt 2>/dev/null || true

test-hitl-5-setup:
	@echo "Setting up order-service with 0 replicas on target cluster..."
	@$(TARGET_KUBECTL) create namespace $(PROD_NAMESPACE) --dry-run=client -o yaml | $(TARGET_KUBECTL) apply -f -
	@$(TARGET_KUBECTL) apply -f ../tests/fixtures/k8s-manifests/order-service.yaml
	@$(TARGET_KUBECTL) get deployment order-service -n $(PROD_NAMESPACE)

test-hitl-5-create-ticket:
	@echo "Creating ticket with Jenkins URL in description..."
	$(call get_jira_creds)
	$(call eval_cluster_tags)
	@TICKET=$$(curl -s -k -X POST "$(JIRA_SERVER)/rest/api/2/issue" \
		-H "Content-Type: application/json" \
		-u "$(JIRA_EMAIL):$(JIRA_TOKEN)" \
		-d '{"fields": {"project": {"key": "$(JIRA_PROJECT)"}, "summary": "order-service is down after Jenkins build failure $(G3_TAG)$(CLUSTER_TAG)", "description": "The order-service deployment in the production namespace has 0 running pods. This may be related to a recent Jenkins build failure:\n\n$(JENKINS_URL)\n\nPlease investigate the Jenkins failure and restore the service.$(if $(TARGET_CLUSTER), Cluster: $(TARGET_CLUSTER). Namespace: production.)", "issuetype": {"name": "$(JIRA_ISSUE_TYPE)"}, "components": [{"name": "$(JIRA_COMPONENT)"}], "labels": ["ai-investigate"], "assignee": {"name": "anoah"}, "customfield_24301": [{"name": "Unknown"}], "customfield_20821": {"value": "No"}}}' | jq -r '.key') && \
	echo "Created ticket: $$TICKET" && \
	echo "Jenkins URL: $(JENKINS_URL)" && \
	echo "$$TICKET" > /tmp/test5-ticket.txt

test-hitl-5: test-hitl-5-cleanup test-hitl-5-setup test-hitl-5-create-ticket
	$(call get_jira_creds)
	@TICKET=$$(cat /tmp/test5-ticket.txt) && \
	echo "" && \
	echo "============================================================" && \
	echo "HITL Test 5 (Jenkins Enrichment): $$TICKET" && \
	echo "Scenario: 0 replicas + Jenkins URL -> enrich + investigate + fix" && \
	echo "Jenkins URL: $(JENKINS_URL)" && \
	echo "Expected: Jira comment includes Jenkins failure classification" && \
	echo "============================================================" && \
	echo "" && \
	echo "Waiting for HITL checkpoint (up to 20 minutes)..." && \
	STATUS="not_found" && \
	for i in $$(seq 1 120); do \
		STATUS=$$($(AGENT_KUBECTL) exec -n $(NAMESPACE) deployment/langgraph-agent -- \
			curl -s "http://localhost:8000/investigate/$$TICKET/pending" 2>/dev/null | jq -r '.status // "not_found"' 2>/dev/null || echo "not_found") && \
		if [ "$$STATUS" = "pending_approval" ]; then break; fi && \
		if [ "$$STATUS" = "completed" ]; then break; fi && \
		printf "\r  Waiting... [%03d/120]" "$$i" && \
		sleep 10; \
	done && \
	echo "" && \
	if [ "$$STATUS" = "pending_approval" ]; then \
		echo "" && \
		echo "HITL checkpoint reached. Review the diagnosis (should include Jenkins findings)." && \
		echo "Add 'approve' comment to: $(JIRA_SERVER)/browse/$$TICKET" && \
		echo "" && \
		echo "Waiting for approval (20 minutes)..." && \
		for i in $$(seq 1 120); do \
			POLL_STATUS=$$($(AGENT_KUBECTL) exec -n $(NAMESPACE) deployment/langgraph-agent -- \
				curl -s "http://localhost:8000/investigate/$$TICKET/pending" 2>/dev/null | jq -r '.status // "unknown"' 2>/dev/null || echo "unknown") && \
			if [ "$$POLL_STATUS" = "completed" ]; then \
				echo "" && \
				echo "Workflow completed!" && \
				break; \
			fi && \
			printf "\r  Waiting... [%03d/120]" "$$i" && \
			sleep 10; \
		done; \
	fi && \
	echo "" && \
	$(MAKE) test-hitl-5-verify

test-hitl-5-verify:
	@echo "Verifying Test 5 Results (Jenkins Enrichment):"
	@echo ""
	@echo "1. Deployment should have running pods:"
	@$(TARGET_KUBECTL) get deployment order-service -n $(PROD_NAMESPACE)
	@$(TARGET_KUBECTL) get pods -n $(PROD_NAMESPACE) -l app=order-service
	@echo ""
	@echo "2. Check Jira comment for Jenkins findings:"
	$(call get_jira_creds)
	@TICKET=$$(cat /tmp/test5-ticket.txt) && \
	COMMENTS=$$(curl -s -k "$(JIRA_SERVER)/rest/api/2/issue/$$TICKET/comment" \
		-u "$(JIRA_EMAIL):$(JIRA_TOKEN)" | jq -r '.comments[-1].body // "no comments"') && \
	if echo "$$COMMENTS" | grep -qi "jenkins\|build.*fail\|failure.*type"; then \
		echo "  PASS: Jira comment contains Jenkins findings"; \
	else \
		echo "  WARN: Jira comment may not contain Jenkins findings"; \
	fi && \
	echo "" && \
	echo "Latest comment (first 500 chars):" && \
	echo "$$COMMENTS" | head -c 500 && \
	echo ""

# ============================================================================
# CLEANUP
# ============================================================================

.PHONY: cleanup-all

cleanup-all: test-remediation-1-cleanup test-remediation-2-cleanup test-concurrent-dispatch-cleanup test-hitl-3-cleanup test-hitl-4-cleanup test-hitl-5-cleanup
	@echo "All test resources cleaned up"

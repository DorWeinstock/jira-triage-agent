# Build stage
FROM golang:1.23-alpine AS builder

WORKDIR /build

# Copy the infra-services dependency (referenced via replace directive)
COPY infra-services/ ./infra-services/

# Set working directory for the main app
WORKDIR /build/jira-jenkins-agent

# Copy go mod files
COPY jira-jenkins-agent/go.mod jira-jenkins-agent/go.sum ./
RUN go mod download

# Copy source code
COPY jira-jenkins-agent/cmd/ ./cmd/
COPY jira-jenkins-agent/pkg/ ./pkg/

# Build the jira-agent binary
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-w -s" -o jira-agent ./cmd/jira-agent

# Final stage - minimal image
FROM alpine:3.19

# Add ca-certificates for HTTPS, tzdata for timezones, and curl for health check
RUN apk --no-cache add ca-certificates tzdata curl

# Create non-root user
RUN adduser -D -g '' appuser

WORKDIR /app

# Copy the binary from builder with proper ownership
COPY --from=builder --chown=appuser:appuser /build/jira-jenkins-agent/jira-agent .

# Use non-root user
USER appuser

# Expose MCP server port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Run the application
ENTRYPOINT ["./jira-agent"]

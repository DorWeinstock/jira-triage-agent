package health

import (
	"encoding/json"
	"net/http"

	"go.uber.org/zap"
)

// Handler handles health check HTTP endpoints.
type Handler struct {
	logger *zap.Logger
}

// NewHandler creates a new health check handler.
func NewHandler(logger *zap.Logger) *Handler {
	if logger == nil {
		logger = zap.NewNop()
	}
	return &Handler{logger: logger}
}

// Response represents the JSON response from health endpoints.
type Response struct {
	Status string `json:"status"`
}

// setHeaders sets common HTTP headers for health check responses.
func (h *Handler) setHeaders(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-cache, no-store, must-revalidate")
}

// Health handles liveness probe requests (GET /health).
func (h *Handler) Health(w http.ResponseWriter, r *http.Request) {
	h.setHeaders(w)
	w.WriteHeader(http.StatusOK)
	if err := json.NewEncoder(w).Encode(Response{Status: "ok"}); err != nil {
		h.logger.Error("failed to encode health response", zap.Error(err))
	}
}

// Ready handles readiness probe requests (GET /ready).
func (h *Handler) Ready(w http.ResponseWriter, r *http.Request) {
	h.setHeaders(w)
	w.WriteHeader(http.StatusOK)
	if err := json.NewEncoder(w).Encode(Response{Status: "ready"}); err != nil {
		h.logger.Error("failed to encode ready response", zap.Error(err))
	}
}

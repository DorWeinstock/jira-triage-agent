package api

import (
	"encoding/json"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"go.uber.org/zap"

	pkglog "jira-triage-agent/pkg/logger"
	"jira-triage-agent/pkg/poller"
)

// RegisterPendingRequest is the request body for registering a pending HITL.
type RegisterPendingRequest struct {
	TicketID    string `json:"ticket_id"`
	ThreadID    string `json:"thread_id"`
	RequestedAt string `json:"requested_at"`
}

// HITLHandler handles HITL-related HTTP endpoints.
type HITLHandler struct {
	watcher *poller.HITLWatcher
	logger  *zap.Logger
}

// NewHITLHandler creates a new HITL handler.
func NewHITLHandler(watcher *poller.HITLWatcher, logger *zap.Logger) *HITLHandler {
	// Use the project's centralized logger when none is provided. This ensures
	// consistent configuration (format/level) and attaches a component field.
	if logger == nil {
		logger = pkglog.WithComponent("api")
	}
	return &HITLHandler{watcher: watcher, logger: logger}
}

// RegisterPending handles POST /hitl/pending.
func (h *HITLHandler) RegisterPending(w http.ResponseWriter, r *http.Request) {
	var req RegisterPendingRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		h.logger.Warn("failed to decode request body", zap.Error(err))
		http.Error(w, `{"error":"invalid request body"}`, http.StatusBadRequest)
		return
	}

	if req.TicketID == "" {
		h.logger.Warn("missing ticket_id in request")
		http.Error(w, `{"error":"ticket_id is required"}`, http.StatusBadRequest)
		return
	}
	if req.ThreadID == "" {
		h.logger.Warn("missing thread_id in request", zap.String("ticket_id", req.TicketID))
		http.Error(w, `{"error":"thread_id is required"}`, http.StatusBadRequest)
		return
	}

	requestedAt, err := time.Parse(time.RFC3339, req.RequestedAt)
	if err != nil {
		h.logger.Debug("invalid timestamp, using current time",
			zap.String("ticket_id", req.TicketID),
			zap.String("requested_at", req.RequestedAt))
		requestedAt = time.Now()
	}

	h.watcher.AddPending(req.TicketID, req.ThreadID, requestedAt)

	h.logger.Info("hitl pending registered",
		zap.String("ticket_id", req.TicketID),
		zap.String("thread_id", req.ThreadID))

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(map[string]string{"status": "registered", "ticket_id": req.TicketID})
}

// RemovePending handles DELETE /hitl/pending/{ticketID}.
func (h *HITLHandler) RemovePending(w http.ResponseWriter, r *http.Request) {
	ticketID := chi.URLParam(r, "ticketID")
	if ticketID == "" {
		h.logger.Warn("missing ticketID in URL path")
		http.Error(w, `{"error":"ticket_id required"}`, http.StatusBadRequest)
		return
	}

	h.watcher.RemovePending(ticketID)

	h.logger.Info("hitl pending removed", zap.String("ticket_id", ticketID))

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]string{"status": "removed", "ticket_id": ticketID})
}

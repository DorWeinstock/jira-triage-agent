package api

import (
	"context"
	"encoding/json"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"go.uber.org/zap"

	pkglog "jira-triage-agent/pkg/logger"
	"jira-triage-agent/pkg/mcp/jira"
)

// TransitionHandler handles Jira ticket status transition REST endpoints.
type TransitionHandler struct {
	client       *jira.Client
	assignID     string // prerequisite: Open → Assigned
	inProgressID string // Assigned → In Progress
	inReviewID   string // In Progress → In Review
	logger       *zap.Logger
}

// NewTransitionHandler creates a new transition handler.
func NewTransitionHandler(client *jira.Client, assignID, inProgressID, inReviewID string, logger *zap.Logger) *TransitionHandler {
	if logger == nil {
		// Use the project's logger package so component-level overrides and
		// formatting configured via environment variables are respected.
		logger = pkglog.WithComponent("api.transition")
	}
	return &TransitionHandler{
		client:       client,
		assignID:     assignID,
		inProgressID: inProgressID,
		inReviewID:   inReviewID,
		logger:       logger,
	}
}

// TransitionToInProgress handles POST /api/transition/{ticketID}/in-progress.
// Chains through Open → Assigned → In Progress if assignID is configured.
func (h *TransitionHandler) TransitionToInProgress(w http.ResponseWriter, r *http.Request) {
	ticketID := chi.URLParam(r, "ticketID")

	// Best-effort: move through prerequisite state (Open → Assigned).
	// Fails silently if already Assigned or assignID is not configured.
	if h.assignID != "" && ticketID != "" {
		ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancel()
		err := h.client.TransitionIssue(ctx, ticketID, h.assignID)
		if err != nil {
			h.logger.Debug("assign prerequisite skipped (likely already past Open)",
				zap.String("ticket_id", ticketID),
				zap.Error(err))
		}
	}

	h.doTransition(w, r, h.inProgressID, "In Progress")
}

// TransitionToInReview handles POST /api/transition/{ticketID}/in-review.
func (h *TransitionHandler) TransitionToInReview(w http.ResponseWriter, r *http.Request) {
	h.doTransition(w, r, h.inReviewID, "In Review")
}

func (h *TransitionHandler) doTransition(w http.ResponseWriter, r *http.Request, transitionID, statusName string) {
	ticketID := chi.URLParam(r, "ticketID")
	if ticketID == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"status": "error", "error": "ticketID required"})
		return
	}

	if transitionID == "" {
		h.logger.Warn("transition ID not configured",
			zap.String("ticket_id", ticketID),
			zap.String("target_status", statusName))
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotImplemented)
		json.NewEncoder(w).Encode(map[string]string{"status": "error", "error": "transition not configured"})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()

	if err := h.client.TransitionIssue(ctx, ticketID, transitionID); err != nil {
		h.logger.Warn("transition failed",
			zap.String("ticket_id", ticketID),
			zap.String("target_status", statusName),
			zap.Error(err))
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadGateway)
		json.NewEncoder(w).Encode(map[string]string{
			"status":    "error",
			"ticket_id": ticketID,
			"error":     err.Error(),
		})
		return
	}

	h.logger.Info("ticket transitioned",
		zap.String("ticket_id", ticketID),
		zap.String("new_status", statusName))

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"status":     "success",
		"ticket_id":  ticketID,
		"new_status": statusName,
	})
}

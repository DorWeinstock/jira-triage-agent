package api

import (
	"context"
	"encoding/json"
	"net/http"
	"time"

	"go.uber.org/zap"

	pkglog "jira-triage-agent/pkg/logger"
	"jira-triage-agent/pkg/poller"
)

// pollTimeout bounds how long a manually-triggered poll cycle may run in the
// background, as a safety net against a hung dispatch chain.
const pollTimeout = 5 * time.Minute

// PollHandler exposes an on-demand trigger for the poller, so a triage cycle
// can be kicked off immediately instead of waiting for the next scheduled
// tick or restarting the pod.
type PollHandler struct {
	poller *poller.Poller
	logger *zap.Logger
}

// NewPollHandler creates a new poll handler.
func NewPollHandler(p *poller.Poller, logger *zap.Logger) *PollHandler {
	if logger == nil {
		logger = pkglog.WithComponent("api.poll")
	}
	return &PollHandler{poller: p, logger: logger}
}

// TriggerPoll handles POST /poll. It starts a poll cycle in the background and
// returns immediately — a full cycle (JQL search + dispatch + downstream
// triage) can take longer than a typical HTTP request/response, so the
// caller should check application logs or the ticket's resulting labels to
// observe the outcome, the same way the poller's own scheduled ticks work.
func (h *PollHandler) TriggerPoll(w http.ResponseWriter, r *http.Request) {
	h.logger.Info("poll triggered via API")

	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), pollTimeout)
		defer cancel()
		h.poller.Poll(ctx)
	}()

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(map[string]string{"status": "poll triggered"})
}

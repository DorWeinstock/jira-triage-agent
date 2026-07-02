package main

import (
	"context"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"go.uber.org/zap"
	"jira-triage-agent/pkg/api"
	"jira-triage-agent/pkg/health"
	"jira-triage-agent/pkg/logger"
	jiramcp "jira-triage-agent/pkg/mcp/jira"
	"jira-triage-agent/pkg/poller"
)

func main() {
	cfg := LoadConfig()

	zapLog := logger.New()
	defer zapLog.Sync()

	if err := cfg.Validate(); err != nil {
		zapLog.Fatal("invalid configuration", zap.Error(err))
	}

	jiraClient := jiramcp.NewClient(cfg.JiraURL, cfg.JiraAPIToken)
	p := buildPoller(cfg, jiraClient)

	r := setupRouter(cfg, jiraClient, p, zapLog)

	server := &http.Server{
		Addr:         ":" + cfg.Port,
		Handler:      r,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 30 * time.Second,
		IdleTimeout:  120 * time.Second,
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	go p.Run(ctx)
	startShutdownHandler(ctx, cancel, server, zapLog)

	zapLog.Info("starting jira-triage-agent",
		zap.String("port", cfg.Port),
		zap.String("project", cfg.FilterProject),
		zap.String("component", cfg.FilterComponent),
		zap.Strings("teamMembers", cfg.TeamMembers),
		zap.Duration("pollInterval", cfg.PollingInterval),
	)

	if err := server.ListenAndServe(); err != http.ErrServerClosed {
		zapLog.Fatal("server error", zap.Error(err))
	}

	<-ctx.Done()
	zapLog.Info("server stopped")
}

func setupRouter(cfg *Config, jiraClient *jiramcp.Client, p *poller.Poller, zapLog *zap.Logger) chi.Router {
	r := chi.NewRouter()

	healthHandler := health.NewHandler(zapLog)
	r.Get("/health", healthHandler.Health)
	r.Get("/ready", healthHandler.Ready)

	jiraMCPServer := jiramcp.NewMCPServer(jiraClient)
	jiraMCPHandler := mcp.NewStreamableHTTPHandler(
		func(req *http.Request) *mcp.Server { return jiraMCPServer },
		&mcp.StreamableHTTPOptions{SessionTimeout: 15 * time.Minute},
	)
	r.Handle("/mcp/jira", jiraMCPHandler)
	zapLog.Info("registered Jira MCP endpoint", zap.String("endpoint", "/mcp/jira"))

	pollHandler := api.NewPollHandler(p, zapLog)
	r.Post("/poll", pollHandler.TriggerPoll)
	zapLog.Info("registered poll trigger endpoint", zap.String("endpoint", "/poll"))

	return r
}

func buildPoller(cfg *Config, jiraClient *jiramcp.Client) *poller.Poller {
	dispatcher := poller.NewHTTPDispatcher(cfg.AgentURL + "/triage")

	pollerCfg := poller.Config{
		FilterProject:           cfg.FilterProject,
		FilterComponent:         cfg.FilterComponent,
		FilterIssueType:         cfg.FilterIssueType,
		Interval:                cfg.PollingInterval,
		MaxConcurrentDispatches: cfg.MaxConcurrentDispatches,
		ProcessedLabel:          cfg.ProcessedLabel,
	}

	pollerLog := logger.WithComponent("poller")
	jiraPollerClient := &jiraPollerAdapter{client: jiraClient}
	return poller.New(jiraPollerClient, dispatcher, pollerCfg, poller.WithLogger(pollerLog))
}

func startShutdownHandler(ctx context.Context, cancel context.CancelFunc, server *http.Server, zapLog *zap.Logger) {
	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
		<-sigCh
		zapLog.Info("shutting down")
		cancel()
		shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer shutdownCancel()
		if err := server.Shutdown(shutdownCtx); err != nil {
			zapLog.Error("server shutdown error", zap.Error(err))
		}
	}()
}

type jiraPollerAdapter struct {
	client *jiramcp.Client
}

var _ poller.JiraClient = (*jiraPollerAdapter)(nil)

func (a *jiraPollerAdapter) SearchTickets(ctx context.Context, jql string, maxResults int) ([]jiramcp.Ticket, error) {
	return a.client.SearchTickets(ctx, jql, maxResults)
}

func (a *jiraPollerAdapter) AddLabel(ctx context.Context, ticketID, label string) error {
	return a.client.AddLabel(ctx, ticketID, label)
}

func (a *jiraPollerAdapter) RemoveLabel(ctx context.Context, ticketID, label string) error {
	return a.client.RemoveLabel(ctx, ticketID, label)
}

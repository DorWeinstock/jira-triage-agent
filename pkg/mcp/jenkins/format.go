package jenkins

import (
	"fmt"
	"time"
)

// formatBuildInfo formats build metadata for LLM consumption.
func formatBuildInfo(info *BuildInfo) string {
	result := info.Result
	if result == "" {
		result = "IN_PROGRESS"
	}

	started := time.UnixMilli(info.Timestamp).UTC().Format(time.RFC3339)

	return fmt.Sprintf(`BUILD INFO:
  Result: %s
  Duration: %s
  Started: %s
  Display Name: %s
  Node: %s`,
		result,
		formatDuration(info.Duration),
		started,
		info.DisplayName,
		info.BuiltOn,
	)
}

// formatConsoleLog formats console output with truncation indicator.
func formatConsoleLog(log string, maxBytes int64, wasTruncated bool) string {
	if log == "" {
		return "CONSOLE LOG:\n  No console output available"
	}

	header := "CONSOLE LOG:"
	if wasTruncated {
		header = fmt.Sprintf("CONSOLE LOG (last %s):", formatBytes(maxBytes))
	}

	return fmt.Sprintf("%s\n%s", header, log)
}

// formatUpstreamCause formats upstream trigger information.
func formatUpstreamCause(cause *UpstreamCause) string {
	if cause == nil {
		return "No upstream trigger found"
	}

	return fmt.Sprintf(`UPSTREAM CAUSE:
  Triggered by: %s #%d
  Parent URL: %s`,
		cause.Project,
		cause.Build,
		cause.URL,
	)
}

// formatDuration converts milliseconds to human-readable duration.
func formatDuration(ms int64) string {
	d := time.Duration(ms) * time.Millisecond

	hours := int(d.Hours())
	minutes := int(d.Minutes()) % 60
	seconds := int(d.Seconds()) % 60

	if hours > 0 {
		return fmt.Sprintf("%dh %dm %ds", hours, minutes, seconds)
	}
	if minutes > 0 {
		return fmt.Sprintf("%dm %ds", minutes, seconds)
	}
	return fmt.Sprintf("%ds", seconds)
}

// formatBytes converts bytes to human-readable size.
func formatBytes(b int64) string {
	const kb = 1024
	if b >= kb*kb {
		return fmt.Sprintf("%.1fMB", float64(b)/float64(kb*kb))
	}
	if b >= kb {
		return fmt.Sprintf("%.0fKB", float64(b)/float64(kb))
	}
	return fmt.Sprintf("%dB", b)
}

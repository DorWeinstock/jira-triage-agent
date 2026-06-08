"""Entry point for LangGraph agent."""
import logging
import os

import uvicorn


class HealthCheckFilter(logging.Filter):
    """Filter out health check endpoint logs to reduce noise."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Suppress GET /health requests from uvicorn access logs
        message = record.getMessage()
        if "GET /health" in message:
            return False
        return True


def main():
    port = int(os.getenv("PORT", "8000"))
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    # Configure root logger so all application loggers (supervisor, agents,
    # verification_service, etc.) emit to stdout instead of being silently dropped
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Add filter to suppress health check logs
    logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())

    uvicorn.run(
        "src.server:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()

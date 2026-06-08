"""K8s pod log preprocessing: boring line filtering and fuzzy deduplication.

Reduces token waste by removing noise lines (health checks, probes,
leader election, etc.) and fuzzy-deduplicating near-identical log lines.
"""

import logging
import re

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

# K8s-specific boring patterns
_BORING_PATTERNS: list[re.Pattern] = [
    re.compile(r"^\s*$"),                          # Empty / whitespace-only
    re.compile(r"^[\.\-\=\*]+$"),                  # Dots, dashes, decorators
    re.compile(r"^\d{4}[-/]\d{2}[-/]\d{2}[\sT]\d{2}:\d{2}:\d{2}\s*$"),  # Timestamp-only
    # Health / readiness / liveness probes
    re.compile(r"healthz|readyz|livez|/metrics", re.IGNORECASE),
    re.compile(r"readiness\s+probe|liveness\s+probe", re.IGNORECASE),
    # Leader election chatter
    re.compile(r"leader\s+election|successfully\s+acquired\s+lease|renewed\s+lease", re.IGNORECASE),
    # Lifecycle noise
    re.compile(r"watch\s+channel\s+closed|cache\s+synced|informer\s+started", re.IGNORECASE),
]


def is_boring_line(line: str, extra_boring_patterns: list[re.Pattern] | None = None) -> bool:
    """Return True if `line` is noise that should be filtered from logs.

    Args:
        line: A single log line.
        extra_boring_patterns: Optional additional compiled regex patterns to treat as boring.

    Returns:
        True if the line matches any boring pattern.
    """
    patterns = _BORING_PATTERNS
    if extra_boring_patterns:
        for i, p in enumerate(extra_boring_patterns):
            if not isinstance(p, re.Pattern):
                raise TypeError(
                    f"extra_boring_patterns[{i}] must be a compiled re.Pattern, "
                    f"got {type(p).__name__!r}"
                )
        patterns = _BORING_PATTERNS + extra_boring_patterns

    for pattern in patterns:
        if pattern.search(line):
            return True
    return False


def deduplicate_lines(text: str, threshold: int = 85) -> str:
    """Filter boring lines, then fuzzy-deduplicate remaining lines.

    Lines that are >= `threshold`% similar (rapidfuzz ratio) to an already-seen
    line are dropped. The first occurrence becomes the representative.

    Args:
        text: Multi-line log text.
        threshold: Similarity threshold (0-100). Lines scoring >= this
                   against any kept line are considered duplicates.

    Returns:
        Deduplicated text with boring lines removed.
    """
    if not text or not text.strip():
        return ""
    if not 0 <= threshold <= 100:
        raise ValueError(f"threshold must be 0–100, got {threshold!r}")

    seen_exact: set[str] = set()
    kept: list[str] = []

    for line in text.splitlines():
        if is_boring_line(line):
            continue

        # O(1) exact-duplicate check — handles the common case
        if line in seen_exact:
            continue
        seen_exact.add(line)

        # O(|kept|) fuzzy check — only reached for distinct lines
        is_dup = any(fuzz.ratio(line, kept_line) >= threshold for kept_line in kept)
        if not is_dup:
            kept.append(line)

    return "\n".join(kept)


def process_pod_logs(
    pod_logs: dict[str, str],
    threshold: int = 85,
) -> dict[str, str]:
    """Process a dict of pod_name -> raw_logs, deduplicating each independently.

    Args:
        pod_logs: Mapping of pod name to raw log text.
        threshold: Fuzzy similarity threshold for deduplication.

    Returns:
        Mapping of pod name to processed log text.
    """
    if not pod_logs:
        return {}

    processed: dict[str, str] = {}

    for pod_name, raw_logs in pod_logs.items():
        original_len = len(raw_logs)
        deduped = deduplicate_lines(raw_logs, threshold=threshold)
        processed[pod_name] = deduped

        new_len = len(deduped)
        if original_len > 0:
            reduction = (1 - new_len / original_len) * 100
            logger.info(
                "Log processing for %s: %d -> %d chars (%.0f%% reduction)",
                pod_name,
                original_len,
                new_len,
                reduction,
            )

    return processed

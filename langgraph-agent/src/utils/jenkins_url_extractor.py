"""Extract Jenkins build URLs from ticket descriptions."""

import re

# Matches Jenkins build URLs in any text context (plain, Jira markup, markdown).
# Pattern: http(s)://host/job/path/.../buildNumber
# Handles nested folders: /job/folder/job/subfolder/job/name/123/
_JENKINS_URL_RE = re.compile(
    r'https?://[^\s\])|>]+/job/[^\s\])|>]+?/(\d+)/?(?=[?\s\])|>]|$)'
)


def extract_jenkins_urls(text: str) -> list[str]:
    """Extract Jenkins build URLs from text.

    Handles plain text, Jira wiki markup [text|url], and markdown [text](url).
    Returns deduplicated list preserving first-occurrence order.
    Only returns URLs that match Jenkins build URL pattern (contain /job/ and
    end with build number).

    Args:
        text: Ticket description or any text containing Jenkins URLs.

    Returns:
        List of unique Jenkins build URLs found.
    """
    if not text:
        return []

    urls = []
    for match in _JENKINS_URL_RE.finditer(text):
        url = match.group(0)
        # Normalize: strip trailing slash for deduplication
        normalized = url.rstrip("/")
        if normalized not in urls:
            urls.append(normalized)

    return urls

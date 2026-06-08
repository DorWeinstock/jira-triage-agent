"""LLM call helpers: retry and timeout wrappers."""

import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

LLM_TIMEOUT_SECONDS = 60
LLM_MAX_RETRIES = 3


@retry(
    stop=stop_after_attempt(LLM_MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def invoke_llm_with_retry(llm, messages: list, timeout: float = LLM_TIMEOUT_SECONDS):
    """Invoke LLM with exponential backoff retry and hard timeout.

    Args:
        llm: LangChain LLM instance with ainvoke method
        messages: List of messages to send to LLM
        timeout: Timeout in seconds for the LLM call (default 60)

    Returns:
        LLM response object

    Raises:
        asyncio.TimeoutError: If LLM call exceeds timeout
        Exception: If max retries exceeded or other error occurs
    """
    return await asyncio.wait_for(llm.ainvoke(messages), timeout=timeout)

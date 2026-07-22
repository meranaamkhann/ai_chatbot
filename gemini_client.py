"""
Retry logic for Gemini API calls.

Before this, one transient Gemini hiccup (a 503, a dropped connection) was
a failed reply, full stop. That's not acceptable for anything calling
itself production-minded — most API failures in the wild are transient.

What this does and doesn't do:
- Retries on 5xx/429 (rate limit) with exponential backoff, up to 3
  attempts total. Does NOT retry on 4xx auth/bad-request errors — retrying
  a bad API key just burns quota for the same guaranteed failure.
- Covers the non-streaming call fully. For the *streaming* call, only the
  attempt to obtain the first chunk is retried — once tokens have started
  reaching the client, restarting the whole generation from scratch would
  mean silently replaying already-shown text, which is worse than just
  surfacing the error. Full resumable streaming is a real gap, not
  something this quietly papers over — see AUDIT.md.
"""

from __future__ import annotations

from google.genai import errors as genai_errors
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, genai_errors.APIError):
        status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        return status in RETRYABLE_STATUS_CODES
    return False


_retry_policy = retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    reraise=True,
)


@_retry_policy
def generate_content_with_retry(client, **kwargs):
    return client.models.generate_content(**kwargs)


def start_stream_with_retry(client, **kwargs):
    """Return an iterator positioned at its first successfully-fetched chunk.

    Retries only the "get the stream going" phase. Returns (first_chunk,
    remaining_iterator) so the caller can yield the first chunk and then
    continue consuming the rest without re-requesting it.
    """

    @_retry_policy
    def _attempt():
        stream = client.models.generate_content_stream(**kwargs)
        iterator = iter(stream)
        first_chunk = next(iterator, None)
        return first_chunk, iterator

    return _attempt()


_SUMMARY_INSTRUCTION = (
    "Summarize the following health-related conversation in 2-4 short "
    "sentences, preserving any specific symptoms, conditions, or "
    "advice already given so the conversation can continue naturally. "
    "Write the summary itself only, with no preamble."
)


@_retry_policy
def summarize_messages(client, model: str, messages: list[dict]) -> str:
    """Condenses older turns into a short running summary — used by
    conversation_store's rolling summarization so a long chat doesn't
    silently lose context once it falls outside the recent-turns window."""
    transcript = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in messages)
    response = client.models.generate_content(
        model=model,
        contents=f"Conversation to summarize:\n{transcript}",
        config={"system_instruction": _SUMMARY_INSTRUCTION},
    )
    return (response.text or "").strip()

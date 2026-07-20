from unittest.mock import MagicMock

import pytest
from google.genai import errors as genai_errors

from gemini_client import generate_content_with_retry, start_stream_with_retry


def _api_error(status_code):
    err = genai_errors.APIError(status_code, {"message": "boom"})
    return err


def test_retries_on_transient_error_then_succeeds():
    client = MagicMock()
    success_response = MagicMock()
    success_response.text = "ok"
    client.models.generate_content.side_effect = [_api_error(503), success_response]

    result = generate_content_with_retry(client, model="m", contents="hi", config={})
    assert result.text == "ok"
    assert client.models.generate_content.call_count == 2


def test_does_not_retry_on_permanent_error():
    client = MagicMock()
    client.models.generate_content.side_effect = _api_error(401)

    with pytest.raises(genai_errors.APIError):
        generate_content_with_retry(client, model="m", contents="hi", config={})

    assert client.models.generate_content.call_count == 1


def test_gives_up_after_three_attempts():
    client = MagicMock()
    client.models.generate_content.side_effect = _api_error(503)

    with pytest.raises(genai_errors.APIError):
        generate_content_with_retry(client, model="m", contents="hi", config={})

    assert client.models.generate_content.call_count == 3


def test_start_stream_with_retry_returns_first_chunk_and_rest():
    client = MagicMock()
    chunk1, chunk2 = MagicMock(text="a"), MagicMock(text="b")
    client.models.generate_content_stream.return_value = iter([chunk1, chunk2])

    first_chunk, rest = start_stream_with_retry(client, model="m", contents="hi", config={})
    assert first_chunk is chunk1
    assert list(rest) == [chunk2]

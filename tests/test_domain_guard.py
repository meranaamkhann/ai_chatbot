from unittest.mock import MagicMock

from domain_guard import Topic, _keyword_pass, classify_message


def test_clear_health_question():
    result = classify_message("What helps with a mild headache?")
    assert result.topic == Topic.HEALTH
    assert result.reason == "keyword_pass"


def test_clear_off_topic_question():
    result = classify_message("What's the current bitcoin price?")
    assert result.topic == Topic.OFF_TOPIC
    assert result.reason == "keyword_pass"


def test_off_topic_coding_request():
    result = classify_message("Can you help me write code in Python?")
    assert result.topic == Topic.OFF_TOPIC


def test_off_topic_script_request_regression():
    # Regression: this phrasing previously fell through to UNSURE because
    # the keyword list only had "write code", not "python script" /
    # "write a script". Caught directly by the keyword pass now.
    result = classify_message("write me a python script to scrape a website")
    assert result.topic == Topic.OFF_TOPIC
    assert result.reason == "keyword_pass"


def test_off_topic_creative_writing():
    result = classify_message("Write a poem about the ocean")
    assert result.topic == Topic.OFF_TOPIC


def test_emergency_keywords_detected_without_llm():
    result = classify_message("I am having severe chest pain and can't breathe")
    assert result.topic == Topic.EMERGENCY


def test_suicidal_ideation_detected_as_emergency():
    result = classify_message("I want to kill myself")
    assert result.topic == Topic.EMERGENCY


def test_unsure_without_client_fails_open_to_health():
    # A message with no keyword signal either way and no LLM client provided.
    result = classify_message("xyz qwerty unrelated gibberish")
    assert result.topic == Topic.HEALTH
    assert result.reason == "unsure_fail_open"


def test_unsure_falls_back_to_llm_when_client_provided():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "OFF_TOPIC"
    mock_client.models.generate_content.return_value = mock_response

    result = classify_message(
        "xyz qwerty unrelated gibberish", client=mock_client, model="test-model"
    )
    assert result.topic == Topic.OFF_TOPIC
    assert result.reason == "llm_fallback"
    mock_client.models.generate_content.assert_called_once()


def test_llm_fallback_emergency():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "EMERGENCY"
    mock_client.models.generate_content.return_value = mock_response

    result = classify_message(
        "ambiguous message with no keywords", client=mock_client, model="test-model"
    )
    assert result.topic == Topic.EMERGENCY


def test_llm_classifier_failure_fails_open_to_health():
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = RuntimeError("boom")

    result = classify_message(
        "ambiguous message with no keywords", client=mock_client, model="test-model"
    )
    assert result.topic == Topic.HEALTH


def test_health_keyword_with_no_off_topic_overlap():
    result = classify_message("I've had a fever and cough for two days")
    assert result.topic == Topic.HEALTH


def test_simple_greeting_hello():
    result = classify_message("Hello")
    assert result.topic == Topic.GREETING
    assert result.reason == "keyword_pass"


def test_simple_greeting_hi_with_punctuation():
    result = classify_message("hi!")
    assert result.topic == Topic.GREETING


def test_greeting_hey_there():
    result = classify_message("hey there")
    assert result.topic == Topic.GREETING


def test_greeting_good_morning():
    result = classify_message("Good morning!")
    assert result.topic == Topic.GREETING


def test_greeting_how_are_you():
    result = classify_message("how are you?")
    assert result.topic == Topic.GREETING


def test_greeting_thanks():
    result = classify_message("thank you")
    assert result.topic == Topic.GREETING


def test_greeting_who_are_you():
    result = classify_message("who are you?")
    assert result.topic == Topic.GREETING


def test_greeting_what_can_you_do():
    result = classify_message("what can you do?")
    assert result.topic == Topic.GREETING


def test_greeting_does_not_match_real_health_question():
    # "hi" appears nowhere, but make sure a real health question
    # containing a greeting-ish word isn't accidentally swept up.
    result = classify_message("Hi, I have a really bad headache, what should I do?")
    assert result.topic == Topic.HEALTH


def test_llm_fallback_greeting():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "GREETING"
    mock_client.models.generate_content.return_value = mock_response

    result = classify_message(
        "ambiguous message with no keywords", client=mock_client, model="test-model"
    )
    assert result.topic == Topic.GREETING


def test_mixed_keywords_detected_as_unsure_by_keyword_pass():
    # Contains both a health keyword and an off-topic keyword -> ambiguous
    # at the keyword-pass level, even though classify_message() without a
    # classifier will still fail open toward HEALTH (tested separately).
    assert _keyword_pass("Can the stock market cause stress and anxiety?") == Topic.UNSURE


def test_mixed_keywords_without_client_fails_open_to_health():
    result = classify_message("Can the stock market cause stress and anxiety?")
    assert result.topic == Topic.HEALTH
    assert result.reason == "unsure_fail_open"


def test_mixed_keywords_with_client_uses_llm_fallback():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "OFF_TOPIC"
    mock_client.models.generate_content.return_value = mock_response

    result = classify_message(
        "Can the stock market cause stress and anxiety?",
        client=mock_client,
        model="test-model",
    )
    assert result.topic == Topic.OFF_TOPIC
    assert result.reason == "llm_fallback"

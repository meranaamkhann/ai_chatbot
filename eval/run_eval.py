"""
Evaluation harness for the domain guard.

Run with:

    python eval/run_eval.py

Requires GEMINI_API_KEY in the environment (or .env) so the LLM fallback
tier can actually be exercised — about a third of the dataset is
deliberately ambiguous/adversarial phrasing designed to miss the keyword
pass and hit the classifier call, since that's the tier most likely to
have real failure modes and least likely to get manually eyeballed.

This is a small, honest measurement, not a claim of rigor: ~60 hand-written
examples is enough to catch obvious regressions and give a real number to
put in a README or say out loud in an interview ("94% accuracy on a
60-example labeled set, mostly missing on X") — it is not enough to certify
the guard for a real clinical deployment. Said explicitly so nobody,
including future-you, mistakes this for more than it is.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from domain_guard import classify_message  # noqa: E402

DATASET_PATH = Path(__file__).parent / "eval_dataset.json"
LABELS = ["health", "off_topic", "emergency", "greeting"]


def _get_client_and_model():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY not set — running keyword-tier only (LLM fallback cases will fail-open to HEALTH).")
        return None, None
    from google import genai

    model = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
    return genai.Client(api_key=api_key), model


def run_eval():
    dataset = json.loads(DATASET_PATH.read_text())
    client, model = _get_client_and_model()

    confusion = defaultdict(Counter)  # confusion[true_label][predicted_label] += 1
    tier_counts = Counter()  # "keyword_pass" vs "llm_fallback" vs "unsure_fail_open"
    misclassified = []

    for i, item in enumerate(dataset):
        message, true_label = item["message"], item["label"]
        result = classify_message(message, client=client, model=model)
        predicted = result.topic.value

        confusion[true_label][predicted] += 1
        tier_counts[result.reason] += 1

        if predicted != true_label:
            misclassified.append((message, true_label, predicted, result.reason))

        # Free-tier rate limits: a small pause when we're actually hitting the API.
        if result.reason == "llm_fallback" and client is not None:
            time.sleep(1.2)

        print(f"[{i + 1}/{len(dataset)}] {'OK ' if predicted == true_label else 'MISS'} "
              f"true={true_label:<10} pred={predicted:<10} ({result.reason})  {message[:60]}")

    print("\n" + "=" * 72)
    print("CONFUSION MATRIX (rows = true label, cols = predicted)")
    print("=" * 72)
    header = " " * 12 + "".join(f"{l:>12}" for l in LABELS)
    print(header)
    for true_label in LABELS:
        row = "".join(f"{confusion[true_label][pred]:>12}" for pred in LABELS)
        print(f"{true_label:<12}{row}")

    print("\n" + "=" * 72)
    print("PER-CLASS PRECISION / RECALL")
    print("=" * 72)
    total_correct = 0
    total_count = 0
    for label in LABELS:
        tp = confusion[label][label]
        fn = sum(confusion[label][p] for p in LABELS if p != label)
        fp = sum(confusion[t][label] for t in LABELS if t != label)
        support = tp + fn
        precision = tp / (tp + fp) if (tp + fp) else float("nan")
        recall = tp / support if support else float("nan")
        total_correct += tp
        total_count += support
        print(f"{label:<12} precision={precision:.2f}  recall={recall:.2f}  support={support}")

    accuracy = total_correct / total_count if total_count else 0
    print(f"\nOverall accuracy: {accuracy:.1%}  ({total_correct}/{total_count})")
    print(f"Classification tier usage: {dict(tier_counts)}")

    if misclassified:
        print("\n" + "=" * 72)
        print("MISCLASSIFIED EXAMPLES")
        print("=" * 72)
        for message, true_label, predicted, reason in misclassified:
            print(f"  true={true_label} pred={predicted} ({reason}): {message}")

    return accuracy


if __name__ == "__main__":
    run_eval()

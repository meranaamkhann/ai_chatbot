"""
Branding configuration for Sibbu.

Sibbu is built as a white-label-ready healthcare AI assistant: the product
itself is generic, and any clinic, hospital, or health platform can apply
its own name, tagline, and color without touching application logic.

To rebrand for a specific client, only this file (and optionally the
environment variables it reads from) needs to change.
"""

import os

BRAND_NAME = os.getenv("BRAND_NAME", "Sibbu")
BRAND_TAGLINE = os.getenv(
    "BRAND_TAGLINE", "Your AI healthcare assistant"
)
BRAND_GREETING = os.getenv(
    "BRAND_GREETING",
    "Hi, I'm {brand_name}. I can help with general health questions, "
    "symptoms, conditions, medications, and wellness — what's on your mind?",
).format(brand_name=BRAND_NAME)

# Shown persistently in the UI and prepended to context the model sees.
DISCLAIMER = os.getenv(
    "BRAND_DISCLAIMER",
    "{brand_name} provides general health information only. It is not a "
    "substitute for professional medical advice, diagnosis, or treatment, "
    "and it does not prescribe medication or dosages. Always consult a "
    "qualified healthcare provider for medical concerns, and contact local "
    "emergency services for urgent symptoms.",
).format(brand_name=BRAND_NAME)

# Accent color used by the frontend (CSS custom property), configurable per
# deployment without touching the stylesheet.
ACCENT_COLOR = os.getenv("BRAND_ACCENT_COLOR", "#0d9488")  # teal

OFF_TOPIC_MESSAGE = os.getenv(
    "BRAND_OFF_TOPIC_MESSAGE",
    "Sorry, I can only answer health and medical related questions. "
    "Is there something about your health, symptoms, medications, or "
    "wellness I can help you with?",
)

GREETING_REPLY = os.getenv(
    "BRAND_GREETING_REPLY",
    "Hello! I'm {brand_name}, your AI healthcare assistant. I can help "
    "with questions about symptoms, conditions, medications, nutrition, "
    "and general wellness. What would you like to know?",
).format(brand_name=BRAND_NAME)

EMERGENCY_MESSAGE = os.getenv(
    "BRAND_EMERGENCY_MESSAGE",
    "This sounds like it could be a medical emergency. Please contact your "
    "local emergency number or go to the nearest emergency room right away. "
    "I'm not able to provide emergency care.",
)

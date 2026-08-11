import asyncio
"""
Vertex Gemini Service - Pure LLM adapter for medical visit summaries.

This service provides a clean interface to Vertex AI Gemini models without
any database operations or business logic. It focuses solely on LLM inference.
"""

import json
import logging
import os
import re
from datetime import datetime

from google.cloud import aiplatform
from vertexai.generative_models import GenerativeModel  # type: ignore

from utils.prompts.medical_summary import build_medical_summary_prompt
from utils.prompts.medical_summary_actions_v2 import build_medical_summary_prompt_v2

# Constants
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_REGION = "us-west4"
logger = logging.getLogger(__name__)

# Language name mapping for Gemini prompts
LANGUAGE_NAME_MAP = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "ar": "Arabic",
    "hi": "Hindi",
    "zh": "Chinese",
    "bn": "Bengali",
    "ta": "Tamil",
    "gu": "Gujarati",
    "pa": "Punjabi",
    "pt": "Portuguese"
}


def _get_prompt_version() -> str:
    """
    Feature flag for prompt versioning.
    Defaults to v1 for missing/invalid values.
    """
    version = (os.getenv("AI_SUMMARY_PROMPT_VERSION") or "v1").lower().strip()
    return "v2" if version == "v2" else "v1"


def extract_json_from_llm_response(text: str) -> dict:
    """
    Extract and parse JSON from LLM response text.

    Handles cases where LLM returns JSON wrapped in markdown code fences
    or with extra text around the JSON object.

    Args:
        text: Raw response text from LLM

    Returns:
        Parsed JSON dictionary

    Raises:
        ValueError: If no valid JSON object is found
        json.JSONDecodeError: If JSON parsing fails
    """
    original_text = text  # Keep for error logging

    # Strip markdown code fences if present
    text = text.strip()

    # Remove markdown code blocks - handle various formats
    # Remove ```json or ``` followed by optional language identifier
    text = re.sub(r'```\w*\s*\n?', '', text)
    # Remove any remaining ``` markers
    text = re.sub(r'```', '', text)

    # Clean up extra whitespace
    text = text.strip()

    # Find the first JSON object (anything between { and })
    # Use a more robust pattern that handles nested braces
    json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)

    if not json_match:
        # Try a simpler pattern as fallback
        json_match = re.search(r'\{.*\}', text, re.DOTALL)

    if not json_match:
        logger.error(f"No JSON object found in response: {original_text[:500]}...")
        logger.error(f"Cleaned text: {text[:500]}...")
        raise ValueError("No JSON object found in LLM response")

    json_str = json_match.group(0).strip()

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing failed for extracted string: {json_str[:500]}...")
        logger.error(f"Original response: {original_text[:500]}...")
        raise json.JSONDecodeError(f"Invalid JSON in LLM response: {str(e)}", json_str, e.pos)


# COMMENTED OUT: Old plain-text summary prompt - keeping for safety
# def build_plain_text_summary_prompt(transcript: str) -> str:
#     """
#     Build a simple prompt for generating plain-text medical visit summaries.
#     """
#     current_datetime = datetime.now().strftime("%A, %B %d, %Y, %I:%M %p")
#
#     return f"""You are a helpful medical assistant. Please provide a clear, concise summary of this doctor-patient conversation.
#
# Current date and time: {current_datetime}
#
# Please write a patient-friendly summary that covers:
# - What the patient discussed with the doctor
# - Any diagnoses or concerns mentioned
# - Medications discussed (if any)
# - Next steps or follow-up instructions
#
# Keep the summary clear, empathetic, and easy to understand. Write in plain language without medical jargon.
#
# Transcript:
# {transcript}
#
# Summary:"""


async def detect_transcript_language(raw_text: str) -> str:
    """Detect the spoken language of a transcript using Gemini. Returns language code (e.g. 'en', 'hi', 'bn')."""
    try:
        model = GenerativeModel("gemini-2.5-flash")
        snippet = raw_text[:1500]
        response = await asyncio.to_thread(
            model.generate_content,
            f"What language is this text written in? Reply with ONLY the ISO 639-1 two-letter code (e.g. en, hi, bn, es, ta, te). Text:\n\n{snippet}"
        )
        detected = response.text.strip().lower()[:5]
        # Extract just the 2-letter code
        for code in LANGUAGE_NAME_MAP:
            if code in detected:
                return code
        return detected[:2]
    except Exception as e:
        logger.warning(f"Language detection failed: {e}")
        return None


async def generate_visit_summary(raw_text: str, language: str = "en") -> dict:
    """
    Calls Vertex Gemini with structured prompt and returns parsed JSON dict.

    Args:
        raw_text: The raw transcript text to summarize
        language: Language code for the response (e.g., 'en', 'es')

    Returns:
        Parsed Python dict from Gemini JSON response

    Raises:
        Exception: If Gemini API call fails or JSON parsing fails
    """
    try:
        # Get configuration from environment (region is fixed to us-west4)
        project_id = os.getenv("GCP_PROJECT")

        if not project_id:
            raise ValueError("GCP_PROJECT environment variable must be set")

        logger.info(f"Initializing Vertex AI with project: {project_id}, region: {GEMINI_REGION}")

        # Initialize Vertex AI
        aiplatform.init(project=project_id, location=GEMINI_REGION)

        # Get the model
        model = GenerativeModel(GEMINI_MODEL)

        # Get language name for prompt
        language_name = LANGUAGE_NAME_MAP.get(language, "English")
        logger.info(f"🔍 [GEMINI] Generating summary in language: {language_name} ({language})")
        logger.info(f"🔍 [GEMINI] Sample input text: '{raw_text[:200]}...'")

        # Build the structured prompt (feature-flagged)
        prompt_version = _get_prompt_version()
        if prompt_version == "v2":
            prompt = build_medical_summary_prompt_v2(raw_text, language_name)
        else:
            prompt = build_medical_summary_prompt(raw_text, language_name)

        logger.info(f"🔍 [GEMINI] Sending structured prompt to Gemini (length: {len(prompt)} chars)")
        logger.info(f"🔍 [GEMINI] Prompt language instruction: 'IMPORTANT: The response MUST be written entirely in {language_name}.'")

        # Generate response
        response = model.generate_content(prompt)

        # Extract the text
        response_text = response.text.strip()

        logger.info(f"Received response from Gemini (length: {len(response_text)} chars)")

        # Parse JSON response using robust extraction
        try:
            parsed_result = extract_json_from_llm_response(response_text)
            logger.info(f"Successfully parsed JSON response with keys: {list(parsed_result.keys())}")
            return parsed_result
        except (ValueError, json.JSONDecodeError) as json_error:
            logger.error(f"Failed to parse JSON response: {str(json_error)}")
            logger.error(f"Raw response text: {response_text[:500]}...")  # Log first 500 chars
            raise Exception(f"Gemini returned invalid JSON: {str(json_error)}")

    except Exception as e:
        logger.error(f"Gemini API call failed: {str(e)}")
        raise

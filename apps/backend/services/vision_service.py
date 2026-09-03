"""
Google Vision API service for OCR processing.
Processes images from Google Cloud Storage.
"""

import logging
from google.cloud import vision

logger = logging.getLogger(__name__)


def _get_vision_client() -> vision.ImageAnnotatorClient:
    """
    Vision client on Application Default Credentials.

    On Cloud Run that resolves to the runtime service account, and
    vision.googleapis.com is enabled on the project, which is all an
    authenticated principal needs — there is no Vision-specific IAM role to
    hold.

    This used to claim it fell back to a service account "if ADC not
    available". It never did: the branch read FIREBASE_SERVICE_ACCOUNT, and
    only FIREBASE_SERVICE_ACCOUNT_JSON has ever been set, so sa_b64 was always
    empty and every call has come through the line below. The two April
    failures recorded as "default credentials were not found" are what that
    looks like when ADC cannot be resolved — there was no second path to catch
    them, and the except that would have logged one was unreachable.

    Pointing it at the variable that does exist would not have helped either:
    that secret holds raw JSON, not base64, so the decode would throw, and the
    identity inside it is the Firebase admin account, whose roles are
    Firebase-scoped. Wiring it up would have replaced a working path with an
    untested one.
    """
    return vision.ImageAnnotatorClient()


async def extract_text_from_gcs_uri(gcs_uri: str) -> dict:
    """
    Perform OCR on an image stored in Google Cloud Storage using Google Vision API.

    Args:
        gcs_uri: GCS URI in format gs://bucket/path

    Returns:
        Dict containing:
        - text: Full extracted text
        - confidence: Overall confidence score
        - pages: Number of pages detected
        - language: Detected language code
    """
    try:
        # Initialize Vision client
        client = _get_vision_client()

        # Create image source from GCS URI
        image = vision.Image()
        image.source.image_uri = gcs_uri

        # Configure for document text detection
        features = [vision.Feature(type_=vision.Feature.Type.DOCUMENT_TEXT_DETECTION)]

        # Perform OCR
        response = client.annotate_image({'image': image, 'features': features})

        # Extract full text
        if response.full_text_annotation and response.full_text_annotation.text:
            full_text = response.full_text_annotation.text.strip()

            # Calculate average confidence from pages
            pages = response.full_text_annotation.pages
            confidence_scores = []

            for page in pages:
                if hasattr(page, 'confidence') and page.confidence > 0:
                    confidence_scores.append(page.confidence)

            avg_confidence = (
                sum(confidence_scores) / len(confidence_scores)
                if confidence_scores
                else None
            )

            # Detect language (use first page's language if available)
            detected_language = None
            if pages and pages[0].property.detected_languages:
                detected_language = pages[0].property.detected_languages[0].language_code

            logger.info(f"OCR completed: {len(full_text)} chars, {len(pages)} pages, confidence={avg_confidence}")

            return {
                "text": full_text,
                "confidence": avg_confidence,
                "pages": len(pages),
                "language": detected_language or "en"  # Default to English
            }
        else:
            raise ValueError("No text detected in image")

    except Exception as e:
        logger.error(f"OCR failed for {gcs_uri}: {str(e)}")
        raise

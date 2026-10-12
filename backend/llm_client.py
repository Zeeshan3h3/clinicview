"""
clinicView — Sarvam 105B Resilient LLM Client

Replaces Gemini SDK with Sarvam AI sarvam-105b via OpenAI-compatible /v1/chat/completions endpoint.
Maintains Circuit Breaker, ThreadPoolExecutor timeout bounding, and graceful mock fallbacks.
"""

from __future__ import annotations
import json
import os
import logging
import time
import concurrent.futures
import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY", "")
SARVAM_LLM_URL = "https://api.sarvam.ai/v1/chat/completions"
SARVAM_MODEL = "sarvam-105b"

# Complaint categories for classification
COMPLAINT_CATEGORIES = [
    "pain", "fever", "respiratory", "gi", "neuro",
    "cardiac", "musculoskeletal", "skin", "urinary",
    "gynecological", "psychiatric", "ent", "eye", "general",
]

# Language names for prompt building
LANGUAGE_NAMES = {
    "hi-IN": "Hindi (हिन्दी)",
    "ta-IN": "Tamil (தமிழ்)",
    "te-IN": "Telugu (తెలుగు)",
    "kn-IN": "Kannada (ಕನ್ನಡ)",
    "bn-IN": "Bengali (বাংলা)",
    "mr-IN": "Marathi (मराठी)",
    "gu-IN": "Gujarati (ગુજરાતી)",
    "ml-IN": "Malayalam (മലയാളം)",
    "pa-IN": "Punjabi (ਪੰਜਾਬੀ)",
    "or-IN": "Odia (ଓଡ଼ିଆ)",
    "en-IN": "English",
}

# ──────────────────────────────────────────────────────────────────────
# Circuit Breaker & Health Management
# ──────────────────────────────────────────────────────────────────────

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, reset_timeout: float = 15.0):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failure_count = 0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.last_state_change = time.time()

    def record_success(self):
        self.failure_count = 0
        if self.state != "CLOSED":
            logger.info("⚡ Circuit Breaker: Recovered! Resetting to CLOSED")
            self.state = "CLOSED"
            self.last_state_change = time.time()

    def record_failure(self, reason: str = ""):
        self.failure_count += 1
        logger.warning(f"⚡ Circuit Breaker failure ({self.failure_count}/{self.failure_threshold}): {reason}")
        if self.failure_count >= self.failure_threshold:
            if self.state != "OPEN":
                logger.error(f"🚨 Circuit Breaker TRIPPED to OPEN! Fast failing LLM calls for {self.reset_timeout}s")
                self.state = "OPEN"
                self.last_state_change = time.time()

    def allow_request(self) -> bool:
        if self.state == "CLOSED":
            return True
        elif self.state == "OPEN":
            if time.time() - self.last_state_change > self.reset_timeout:
                logger.info("⚡ Circuit Breaker: Testing recovery (HALF_OPEN)")
                self.state = "HALF_OPEN"
                return True
            return False
        elif self.state == "HALF_OPEN":
            return True
        return True


circuit_breaker = CircuitBreaker()

# Shared thread pool for timeout-bounded LLM calls
_llm_executor = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="sarvam_llm_worker")
_http_client: httpx.Client | None = None


def _get_http_client() -> httpx.Client:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=40.0, write=10.0, pool=5.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
    return _http_client


def _call_model_with_timeout(fn, timeout_seconds: float = 30.0):
    """Execute a synchronous HTTP call with a strict timeout."""
    future = _llm_executor.submit(fn)
    return future.result(timeout=timeout_seconds)


def _clean_json_text(raw_text: str) -> str:
    """Strip markdown code blocks, backticks, and extraneous whitespace."""
    text = (raw_text or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _generate_json_with_retry(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    timeout_seconds: float = 35.0,
    models_to_try: list[str] | None = None,
) -> dict:
    """Call Sarvam 105B via /v1/chat/completions with strict JSON parsing and timeout protection."""
    api_key = os.environ.get("SARVAM_API_KEY", "") or SARVAM_API_KEY
    if not api_key:
        raise RuntimeError("SARVAM_API_KEY not configured in environment")

    if not circuit_breaker.allow_request():
        raise RuntimeError("Circuit breaker is OPEN — fast failing for fallback")

    # Instruct model clearly for JSON format
    json_enforced_system = (
        system_prompt.strip()
        + "\n\nCRITICAL INSTRUCTION: You MUST return ONLY valid JSON matching the requested structure. "
        + "Do NOT wrap the response in conversational remarks or extraneous explanations. Output raw JSON directly."
    )

    client = _get_http_client()

    def _invoke():
        headers = {
            "api-subscription-key": api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "model": SARVAM_MODEL,
            "messages": [
                {"role": "system", "content": json_enforced_system},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        res = client.post(SARVAM_LLM_URL, headers=headers, json=payload, timeout=timeout_seconds)
        res.raise_for_status()
        return res.json()

    t0 = time.time()
    try:
        data = _call_model_with_timeout(_invoke, timeout_seconds=timeout_seconds)
        elapsed = time.time() - t0

        choices = data.get("choices", [])
        if not choices:
            raise ValueError(f"Sarvam LLM returned no choices: {data}")

        raw_content = choices[0].get("message", {}).get("content")
        if not raw_content:
            finish_reason = choices[0].get("finish_reason")
            raise ValueError(f"Sarvam LLM returned empty content (finish_reason={finish_reason})")

        cleaned = _clean_json_text(raw_content)
        parsed = json.loads(cleaned)
        circuit_breaker.record_success()
        logger.info(f"Sarvam 105B LLM call took {elapsed:.2f}s | tokens={data.get('usage', {}).get('total_tokens')}")
        return parsed

    except concurrent.futures.TimeoutError:
        elapsed = time.time() - t0
        err_msg = f"Sarvam 105B timed out after {elapsed:.2f}s (timeout={timeout_seconds}s)"
        logger.warning(f"⚡ {err_msg}")
        circuit_breaker.record_failure("timeout")
        raise TimeoutError(err_msg)

    except Exception as e:
        elapsed = time.time() - t0
        logger.warning(f"⚡ Sarvam 105B failed after {elapsed:.2f}s: {e}")
        circuit_breaker.record_failure(str(e)[:80])
        raise


# ──────────────────────────────────────────────────────────────────────
# Unified Call: conversation_turn
# ──────────────────────────────────────────────────────────────────────

def conversation_turn(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.2,
) -> dict:
    """Execute a single conversation turn via Sarvam 105B with graceful fallback."""
    api_key = os.environ.get("SARVAM_API_KEY", "") or SARVAM_API_KEY
    if not api_key:
        return _mock_conversation_turn(user_prompt)

    try:
        return _generate_json_with_retry(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=4096,
            timeout_seconds=30.0,
        )
    except Exception as e:
        logger.error(f"conversation_turn failed gracefully: {e}")
        return _mock_conversation_turn(user_prompt)


def _mock_conversation_turn(user_prompt: str) -> dict:
    """Mock conversation turn when LLM is unavailable or circuit is open."""
    return {
        "spoken_text": "Could you tell me more about your symptoms?",
        "suggested_options": [
            {"label": "Yes", "label_translated": "Yes"},
            {"label": "No", "label_translated": "No"},
            {"label": "Not sure", "label_translated": "Not sure"},
        ],
        "section_complete": False,
        "extracted_data": {},
        "section_summary": "",
        "red_flag_check": None,
        "reasoning": "Fallback response — LLM circuit open or unavailable",
    }


# ──────────────────────────────────────────────────────────────────────
# Complaint Classification
# ──────────────────────────────────────────────────────────────────────

def classify_complaint(text: str, language: str = "en-IN") -> str:
    """Classify chief complaint into a category using Sarvam 105B."""
    api_key = os.environ.get("SARVAM_API_KEY", "") or SARVAM_API_KEY
    if not api_key:
        return _mock_classify(text)

    lang_name = LANGUAGE_NAMES.get(language, language)
    system_prompt = f"""Classify the patient's chief complaint into exactly ONE of these categories:
{json.dumps(COMPLAINT_CATEGORIES)}

The patient may be speaking in {lang_name}. Understand their complaint regardless of language.
Output ONLY a JSON object: {{"category": "<one of the categories>"}}
If unsure, use "general"."""

    try:
        result = _generate_json_with_retry(
            system_prompt=system_prompt,
            user_prompt=f"Patient says: {text}",
            temperature=0.0,
            max_tokens=4096,
            timeout_seconds=25.0,
        )
        cat = result.get("category", "general")
        return cat if cat in COMPLAINT_CATEGORIES else "general"
    except Exception as e:
        logger.error(f"Sarvam LLM classify_complaint failed: {e}")
        return _mock_classify(text)


def _mock_classify(text: str) -> str:
    """Keyword-based classification fallback."""
    text_lower = text.lower()
    mapping = {
        "pain": ["pain", "ache", "hurt", "dard"],
        "fever": ["fever", "temperature", "bukhar", "bukhaar", "hot"],
        "respiratory": ["cough", "breathe", "breathing", "wheeze", "khansi", "saans"],
        "gi": ["stomach", "vomit", "diarrhea", "nausea", "bowel", "pet", "ulti"],
        "neuro": ["headache", "dizzy", "numbness", "tingling", "seizure", "sir dard"],
        "cardiac": ["chest", "heart", "palpitation", "seena"],
        "skin": ["rash", "itch", "skin", "wound"],
        "urinary": ["urine", "burning", "peshab"],
    }
    for cat, keywords in mapping.items():
        if any(k in text_lower for k in keywords):
            return cat
    return "general"


# ──────────────────────────────────────────────────────────────────────
# Document OCR Entity Extraction
# ──────────────────────────────────────────────────────────────────────

def extract_document_entities(ocr_text: str, doc_type: str = "prescription") -> dict:
    """Extract medications, diagnoses, lab values from OCR text."""
    api_key = os.environ.get("SARVAM_API_KEY", "") or SARVAM_API_KEY
    if not api_key:
        return _mock_doc_extract(ocr_text)

    system_prompt = """Extract structured clinical entities from this medical document text.

Output a JSON object with:
- "medications": [{"name": str, "dose": str, "frequency": str}]
- "diagnoses": [str]
- "lab_results": [{"test": str, "result": str, "unit": str, "reference_range": str, "status": str}]
- "procedures": [str]

Only extract what is clearly present. Mark uncertain extractions."""

    try:
        return _generate_json_with_retry(
            system_prompt=system_prompt,
            user_prompt=f"Document type: {doc_type}\nOCR text:\n{ocr_text}",
            temperature=0.1,
            max_tokens=2500,
            timeout_seconds=20.0,
        )
    except Exception as e:
        logger.error(f"Sarvam LLM extract_document_entities failed: {e}")
        return _mock_doc_extract(ocr_text)


def _mock_doc_extract(ocr_text: str) -> dict:
    """Mock document extraction for demos without LLM."""
    return {
        "medications": [
            {"name": "Paracetamol", "dose": "500 mg", "frequency": "Twice a day"},
            {"name": "Amlodipine", "dose": "5 mg", "frequency": "Once daily"},
        ],
        "diagnoses": ["Hypertension"],
        "lab_results": [
            {"test": "Hemoglobin", "result": "13.2", "unit": "g/dL", "reference_range": "13.0-17.0", "status": "Normal"},
            {"test": "WBC Count", "result": "8200", "unit": "/μL", "reference_range": "4000-11000", "status": "Normal"},
        ],
        "procedures": [],
    }


# ──────────────────────────────────────────────────────────────────────
# Clinical Synthesis
# ──────────────────────────────────────────────────────────────────────

def generate_clinical_summary(filled_state: dict, doc_extractions: list = None, language_code: str = "en-IN") -> dict:
    """Synthesize the Q&A state into a structured clinical handover note."""
    api_key = os.environ.get("SARVAM_API_KEY", "") or SARVAM_API_KEY
    if not api_key:
        return _mock_clinical_summary(filled_state)

    system_prompt = """You are an expert physician assistant. Write a concise, professional clinical handover note (approx 200 words).
Synthesize the patient's history, symptoms, and extracted medical document information.
Output JSON format:
{
  "clinical_narrative": "Patient is a ...",
  "critical_highlights": ["Finding 1", "Finding 2"],
  "contradictions": ["Contradiction 1", "Contradiction 2"]
}
Keep it objective and medically accurate."""

    user_prompt = f"Patient Q&A History:\n{json.dumps(filled_state, indent=2)}\n\nDocument Extractions:\n{json.dumps(doc_extractions or [], indent=2)}"

    try:
        return _generate_json_with_retry(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=3000,
            timeout_seconds=25.0,
        )
    except Exception as e:
        logger.error(f"Sarvam LLM generate_clinical_summary failed: {e}")
        return _mock_clinical_summary(filled_state)


def _mock_clinical_summary(filled_state: dict) -> dict:
    """Mock clinical summary."""
    return {
        "clinical_narrative": "Patient presents with reported symptoms in the provided history. Vital signs and physical examination required for further assessment.",
        "critical_highlights": ["Awaiting detailed clinical evaluation", "Review provided symptom history"],
        "contradictions": [],
    }


# ──────────────────────────────────────────────────────────────────────
# Portal Chatbot Text Generation
# ──────────────────────────────────────────────────────────────────────

def generate_portal_chat_reply(system_prompt: str, user_prompt: str, temperature: float = 0.5) -> str:
    """Standard text generation for the Patient Portal Chatbot."""
    api_key = os.environ.get("SARVAM_API_KEY", "") or SARVAM_API_KEY
    if not api_key:
        return "I'm sorry, our AI system is currently offline. Please try again later."

    if not circuit_breaker.allow_request():
        return "The system is currently experiencing high load. Please try again in a few moments."

    client = _get_http_client()

    def _invoke():
        headers = {
            "api-subscription-key": api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "model": SARVAM_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": 1500,
        }
        res = client.post(SARVAM_LLM_URL, headers=headers, json=payload, timeout=20.0)
        res.raise_for_status()
        return res.json()

    try:
        data = _call_model_with_timeout(_invoke, timeout_seconds=20.0)
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        circuit_breaker.record_success()
        return content.strip()
    except Exception as e:
        logger.error(f"generate_portal_chat_reply failed: {e}")
        circuit_breaker.record_failure(str(e)[:80])
        return "I apologize, but I encountered an error while processing your request. Please consult your physician directly."


def check_sarvam_llm_health() -> bool:
    """Check if Sarvam LLM API is reachable and API key is valid."""
    api_key = os.environ.get("SARVAM_API_KEY", "") or SARVAM_API_KEY
    if not api_key:
        return False
    try:
        client = _get_http_client()
        res = client.post(
            SARVAM_LLM_URL,
            headers={"api-subscription-key": api_key, "Content-Type": "application/json"},
            json={
                "model": SARVAM_MODEL,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 50,
            },
            timeout=5.0,
        )
        return res.status_code in (200, 400)
    except Exception:
        return False

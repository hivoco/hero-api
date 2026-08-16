"""Photo validation for Hero Destini.

The uploaded photo must contain a CHILD together with a PARENT. We use a Groq
vision model via LangChain with **structured output** to confirm, in one call,
that it's a real, appropriate, clear photo of two people (one adult + one child,
with a plausible age gap). It does NOT derive gender or roles — the user picks
the parent/child roles on the form; validation only issues the token.
"""

from fastapi import APIRouter, File, UploadFile, HTTPException, Depends
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from typing import Optional
import base64
import io
import json
import re
import hmac
import hashlib
import time
from uuid import uuid4
from PIL import Image
from pydantic import BaseModel, Field

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import settings
from app.core.database import get_db
from app.core.redis import GroqKeyManager, PhotoValidationQueue, FeatureFlags
from app.services.vision_service import get_active_vision, ensure_default_vision

VALIDATION_TOKEN_EXPIRY = 600  # 10 minutes

# Minimum age gap (years) for the two people to plausibly be parent and child.
MIN_PARENT_CHILD_AGE_GAP = 12

# The child must fall inside the campaign's eligible age range (inclusive).
CHILD_MIN_AGE = 4
CHILD_MAX_AGE = 10

MAX_IMAGE_SIZE = 640
JPEG_QUALITY = 85


# ── Validation token (proves a photo passed, consumed by /video/submit) ──
def generate_validation_token(photo_hash: str) -> str:
    timestamp = str(int(time.time()))
    payload = f"{photo_hash}:{timestamp}"
    signature = hmac.new(settings.JWT_SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()


def verify_validation_token(token: str) -> bool:
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        parts = decoded.split(":")
        if len(parts) != 3:
            return False
        photo_hash, timestamp, signature = parts
        if int(time.time()) - int(timestamp) > VALIDATION_TOKEN_EXPIRY:
            return False
        payload = f"{photo_hash}:{timestamp}"
        expected = hmac.new(settings.JWT_SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
    except Exception:
        return False


router = APIRouter(prefix="/api/v1/photo-validation", tags=["photo-validation"])

# ── Structured schema the LLM fills (Pydantic coerces "35"->35, "true"->True) ──
class PhotoAnalysis(BaseModel):
    """Structured analysis of a parent + child photo."""

    number_of_people: int = Field(description="Count of distinct human faces clearly visible")
    parent_present: bool = Field(description="True if an adult (likely a parent) is present")
    child_present: bool = Field(description="True if a child/minor is present")
    parent_estimated_age: int = Field(description="Best estimate of the adult's age in years")
    child_estimated_age: int = Field(description="Best estimate of the child's age in years")
    quality_ok: bool = Field(description="True if the photo is clear, well-lit and sharp (not blurry/dark)")
    is_real_photo: bool = Field(description="True if a real photo, False if a screenshot / photo-of-a-photo / poster")
    is_appropriate: bool = Field(description="True if there is no nudity, sexual or otherwise inappropriate content")
    faces_unobstructed: bool = Field(description="True if faces are not heavily covered by hands/objects/masks")
    # Defaulted True (fail-open): if the model omits it we don't block on
    # frontality — the frontend face gate already enforces looking at the camera.
    faces_frontal: bool = Field(True, description="True if BOTH faces look toward the camera AND are upright/straight (a small left/right turn or any up/down nod is fine); False if a face is turned ~45 degrees or more to the side (profile) OR tilted sideways / cocked toward a shoulder")


# Appended to the (admin-editable) prompt so the model returns a clean JSON object.
_SCHEMA_HINT = (
    "\n\nReturn ONLY a JSON object (no prose, no markdown) with exactly these keys:\n"
    '  "number_of_people": integer,\n'
    '  "parent_present": boolean,\n'
    '  "child_present": boolean,\n'
    '  "parent_estimated_age": integer,\n'
    '  "child_estimated_age": integer,\n'
    '  "quality_ok": boolean,\n'
    '  "is_real_photo": boolean,\n'
    '  "is_appropriate": boolean,\n'
    '  "faces_unobstructed": boolean,\n'
    '  "faces_frontal": boolean'
)


def _parse_json(content: str) -> dict:
    """Extract a JSON object from the model's reply (tolerant of fences / wrappers).

    Reasoning models (e.g. Groq's qwen) prepend a <think>...</think> block that
    can itself contain braces, so strip it before hunting for the JSON.
    """
    s = (content or "").strip()
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.DOTALL).strip()
    if s.startswith("```"):
        s = s.strip("`")
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b != -1:
        s = s[a:b + 1]
    data = json.loads(s)
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict) and "parameters" in data and "name" in data:
        data = data["parameters"]
    return data


class ValidationResponse(BaseModel):
    valid: bool
    reason: Optional[str] = None
    message: Optional[str] = None
    label: Optional[str] = None
    # Roles/gender and ages are NOT returned — the user picks parent/child roles
    # on the form. Validation only confirms it's a real, clear parent+child photo.
    analysis: Optional[dict] = None
    validation_token: Optional[str] = None


REASONS = {
    "REJECT_UNCLEAR": "The photo is blurry or too dark. Please upload a clear, well-lit photo.",
    "REJECT_SCREENSHOT": "This looks like a screenshot or a photo of a screen/printout. Please upload the original photo from your gallery.",
    "REJECT_NSFW": "This photo has inappropriate content. Please upload a family-friendly photo.",
    "REJECT_OBSTRUCTED": "A face is covered (hand, mask or object). Please upload a photo where both faces are clearly visible.",
    "REJECT_NOT_FRONTAL": "Please look straight into the camera and keep your head level — both faces should face the camera, not turned to the side or tilted.",
    "REJECT_TOO_MANY_PEOPLE": "There are more than two people. The photo must have exactly two — one child and one parent.",
    "REJECT_TOO_FEW_PEOPLE": "There is only one person. The photo must have exactly two — one child and one parent.",
    "REJECT_NOT_PARENT_CHILD": "We couldn't identify one adult and one child. Please upload a photo of the child with a parent.",
    "REJECT_AGE_GAP": "The two people look too close in age to be a parent and child. Please upload a photo of the child with a parent.",
    "REJECT_CHILD_AGE": (
        f"The child's age doesn't meet our criteria — the child's age should be between "
        f"{CHILD_MIN_AGE} and {CHILD_MAX_AGE} years. Please upload a photo of a child aged "
        f"{CHILD_MIN_AGE}–{CHILD_MAX_AGE} with a parent."
    ),
    "APPROVED": "Photo validated successfully!",
}


def get_reason_for_label(label: str) -> str:
    return REASONS.get(label, "Image validation failed. Please try again.")


def resize_image(file_bytes: bytes, max_size: int = MAX_IMAGE_SIZE) -> tuple[bytes, str]:
    try:
        img = Image.open(io.BytesIO(file_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        ow, oh = img.size
        if ow > max_size or oh > max_size:
            if ow > oh:
                nw, nh = max_size, int(oh * (max_size / ow))
            else:
                nh, nw = max_size, int(ow * (max_size / oh))
            img = img.resize((nw, nh), Image.LANCZOS)
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        output.seek(0)
        return output.read(), "image/jpeg"
    except Exception as e:
        print(f"⚠️ Resize failed, using original: {e}")
        return file_bytes, "image/jpeg"


def to_data_url(file_bytes: bytes, mime_type: str) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(file_bytes).decode('utf-8')}"


# ── LangChain call — prompt-guided JSON + Pydantic coercion ───────────────
# We avoid tool/function-calling strict validation because some models emit values
# as strings ("35", "true"); the tolerant _parse_json + Pydantic coercion handle
# that instead. Groq's json_object mode is NOT used: it's incompatible with
# reasoning models (e.g. qwen), which emit a <think> block before the JSON — the
# prompt asks for JSON and _parse_json strips the reasoning. The token budget is
# generous so a reasoning model has room to think AND emit the JSON.
# The provider is chosen from the active vision_config row; keys come from .env.
def _build_vision_llm(provider: str, model_name: str, api_key: Optional[str]):
    p = (provider or "groq").lower()
    if p == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(model=model_name, api_key=api_key, temperature=0, max_tokens=4096)
    if p == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model_name, api_key=api_key, temperature=0, max_tokens=1024,
            model_kwargs={"response_format": {"type": "json_object"}},
        )
    if p in ("google", "gemini"):
        from langchain_google_genai import ChatGoogleGenerativeAI
        # Gemini returns JSON when the prompt asks for it; _parse_json handles it.
        return ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key, temperature=0)
    raise ValueError(f"Unsupported vision provider: {provider}")


def _keys_for_provider(provider: str) -> list:
    """Which API key(s) to try for a provider. Groq supports multiple (failover)."""
    p = (provider or "groq").lower()
    if p == "groq":
        return settings.groq_api_keys_list or [None]
    if p == "openai":
        return [settings.OPENAI_API_KEY]
    if p in ("google", "gemini"):
        return [settings.GOOGLE_API_KEY]
    return [None]


def _content_to_text(content) -> str:
    """Flatten an LLM reply to plain text.

    Groq/OpenAI return a plain string, but langchain_google_genai (Gemini)
    returns a LIST of content blocks — e.g. [{"type": "text", "text": "{...}"}].
    str()-ing that list yields a Python repr (single quotes) that _parse_json
    can't read, so pull the text out of each block instead.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if text:
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def _analyze_with_key(data_url: str, provider: str, model_name: str, prompt: str, api_key: Optional[str]) -> PhotoAnalysis:
    llm = _build_vision_llm(provider, model_name, api_key)
    messages = [
        SystemMessage(content=(prompt or "") + _SCHEMA_HINT),
        HumanMessage(content=[
            {"type": "text", "text": "Analyze this photo and return the JSON object."},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]),
    ]
    resp = llm.invoke(messages)
    content = _content_to_text(resp.content)
    return PhotoAnalysis.model_validate(_parse_json(content))


def analyze_photo(data_url: str, provider: str, model_name: str, prompt: str) -> Optional[PhotoAnalysis]:
    """Run the active vision model. For Groq, retry across all configured keys."""
    keys = _keys_for_provider(provider)
    for api_key in keys:
        try:
            tag = f"...{api_key[-6:]}" if api_key else "(env key)"
            print(f"🔑 Analyzing with {provider}/{model_name.split('/')[-1]} key {tag}")
            return _analyze_with_key(data_url, provider, model_name, prompt, api_key)
        except Exception as e:
            print(f"❌ Vision attempt failed ({model_name}): {e}")
            continue
    return None


def decide(a: PhotoAnalysis, age_gap: int) -> tuple[bool, str]:
    # Content / quality checks first
    if not a.is_appropriate:
        return False, "REJECT_NSFW"
    if not a.is_real_photo:
        return False, "REJECT_SCREENSHOT"
    if not a.quality_ok:
        return False, "REJECT_UNCLEAR"
    if not a.faces_unobstructed:
        return False, "REJECT_OBSTRUCTED"
    # Both faces must look at the camera (a small turn / any up-down tilt is ok;
    # a ~45°+ side turn is not) → "look into the camera".
    if not a.faces_frontal:
        return False, "REJECT_NOT_FRONTAL"
    # Exactly two people: one parent + one child (not more, not fewer)
    if a.number_of_people > 2:
        return False, "REJECT_TOO_MANY_PEOPLE"
    if a.number_of_people < 2:
        return False, "REJECT_TOO_FEW_PEOPLE"
    if not a.parent_present or not a.child_present:
        return False, "REJECT_NOT_PARENT_CHILD"
    # The child's estimated age must fall inside the eligible range (4–10). This
    # also blocks a photo of two adults where the model tags the younger one as
    # the "child": a 30-something estimate falls outside the range and is rejected.
    if not (CHILD_MIN_AGE <= a.child_estimated_age <= CHILD_MAX_AGE):
        return False, "REJECT_CHILD_AGE"
    if age_gap < MIN_PARENT_CHILD_AGE_GAP:
        return False, "REJECT_AGE_GAP"
    return True, "APPROVED"


def build_result(resized_bytes: bytes, a: PhotoAnalysis) -> dict:
    age_gap = abs((a.parent_estimated_age or 0) - (a.child_estimated_age or 0))
    valid, label = decide(a, age_gap)

    # Give a specific "why" on failure using the detected details.
    message = get_reason_for_label(label)
    if label == "REJECT_TOO_MANY_PEOPLE":
        message = (f"We found {a.number_of_people} people. The photo must have exactly two — "
                   f"one child and one parent.")
    elif label == "REJECT_TOO_FEW_PEOPLE":
        message = (f"We found only {a.number_of_people} person. The photo must have exactly two — "
                   f"one child and one parent together.")
    elif label == "REJECT_AGE_GAP":
        message = ("The two people look too close in age to be a parent and child. "
                   "Please upload a photo of the child with a parent.")
    elif label == "REJECT_NOT_PARENT_CHILD":
        message = "We couldn't identify one adult and one child. Please upload one photo with the child and a parent."

    # Ages are used internally for the decision above but never returned — the
    # response must not reveal any person's age.
    analysis = {k: v for k, v in a.model_dump().items()
                if k not in ("parent_estimated_age", "child_estimated_age")}
    result = {
        "valid": valid,
        "label": label,
        "reason": None if valid else message,
        "message": message,
        "analysis": analysis,
    }
    if valid:
        result["validation_token"] = generate_validation_token(hashlib.sha256(resized_bytes).hexdigest())
    return result


@router.post("/check_photo", response_model=ValidationResponse)
async def check_photo(photo: UploadFile = File(...), db: Session = Depends(get_db)):
    """Validate a combined child+parent photo (no gender/role) and issue a token."""
    if not photo.content_type or not photo.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    file_bytes = await photo.read()
    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image size must be less than 10MB")

    resized_bytes, mime_type = resize_image(file_bytes)
    data_url = to_data_url(resized_bytes, mime_type)

    # Load the admin-configured active vision model (provider / model / prompt)
    vc = get_active_vision(db) or ensure_default_vision(db)
    analysis = await run_in_threadpool(analyze_photo, data_url, vc.provider, vc.model_name, vc.prompt)
    if analysis is None:
        print("⚠️ Auto-disabling photo validation due to Groq failure (admin must re-enable)")
        FeatureFlags.set_flag("photo_validation", False, auto=True)
        raise HTTPException(
            status_code=503,
            detail="Image validation service unavailable. Photo validation has been auto-disabled.",
        )

    return ValidationResponse(**build_result(resized_bytes, analysis))


# ── Burst-traffic queue (unchanged surface, structured worker) ───────────
class QueueResponse(BaseModel):
    status: str
    validation_id: Optional[str] = None
    position: Optional[int] = None
    message: str


class StatusResponse(BaseModel):
    status: str
    position: Optional[int] = None
    result: Optional[ValidationResponse] = None
    message: str


class CapacityResponse(BaseModel):
    total_keys: int
    remaining_requests: int
    queue_size: int
    retry_after: int


@router.post("/queue_photo", response_model=QueueResponse)
async def queue_photo(photo: UploadFile = File(...)):
    if GroqKeyManager.get_available_key():
        await photo.seek(0)
        return QueueResponse(status="processing", message="Capacity available. Use /check_photo.")

    if not photo.content_type or not photo.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    file_bytes = await photo.read()
    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image size must be less than 10MB")

    resized_bytes, mime_type = resize_image(file_bytes)
    data_url = to_data_url(resized_bytes, mime_type)

    validation_id = str(uuid4())
    if not PhotoValidationQueue.enqueue(validation_id, data_url):
        raise HTTPException(status_code=503, detail="Queue is full. Please try again later.")

    queue_size = PhotoValidationQueue.get_queue_size()
    return QueueResponse(
        status="queued",
        validation_id=validation_id,
        position=queue_size,
        message=f"Request queued at position {queue_size}. Check status with /status/{validation_id}",
    )


@router.get("/status/{validation_id}", response_model=StatusResponse)
async def get_validation_status(validation_id: str):
    status_data = PhotoValidationQueue.get_status(validation_id)
    if not status_data:
        return StatusResponse(status="not_found", message="Validation request not found or expired.")

    if status_data["status"] == "completed":
        r = status_data.get("result", {})
        return StatusResponse(status="completed", result=ValidationResponse(**r), message="Validation completed.")

    if status_data["status"] == "processing":
        return StatusResponse(status="processing", message="Your photo is being validated.")

    position = status_data.get("position", 0)
    return StatusResponse(status="queued", position=position, message=f"Your request is at position {position} in the queue.")


@router.get("/capacity", response_model=CapacityResponse)
async def get_capacity():
    total_keys = len(settings.groq_api_keys_list)
    remaining = GroqKeyManager.get_total_remaining()
    queue_size = PhotoValidationQueue.get_queue_size()
    retry_after = GroqKeyManager.get_retry_after() if remaining == 0 else 0
    return CapacityResponse(total_keys=total_keys, remaining_requests=remaining, queue_size=queue_size, retry_after=retry_after)

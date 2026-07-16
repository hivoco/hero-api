"""Helpers for the admin-editable vision (photo-validation) model config.

One active row (status=1) at a time. Saving from the admin panel inserts a new
active row and flips the previous active row to status=0.
"""

from typing import Optional
from sqlalchemy.orm import Session

from app.models.vision_config import VisionConfig

# Supported providers for the photo-validation vision call.
ALLOWED_VISION_PROVIDERS = ("groq", "openai", "google")

DEFAULT_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
DEFAULT_VISION_PROMPT = (
    "You are a strict but fair image analyst for a parent-and-child video product. "
    "You are given ONE photo that should contain a child together with a parent. "
    "The photo is UPLOADED from the user's device — a normal pre-clicked / gallery photo "
    "is expected and fully acceptable. Do NOT reject it for not being a live selfie or "
    "for being taken earlier. Only set is_real_photo=false when the image is clearly a "
    "screenshot, a picture of a phone/computer/TV screen, or a photo of a printed "
    "photo or poster. "
    "Count the people carefully and set number_of_people to the EXACT number of humans "
    "visible — the ideal photo has exactly two: one adult (the parent) and one child. "
    "BOTH faces (the parent's and the child's) must be clearly and fully visible — "
    "front-facing, not blurred, and not covered by hands, hair, masks, sunglasses or "
    "objects. If either face is not properly visible, set faces_unobstructed=false. "
    "Carefully estimate each person's age and gender, judge the photo's clarity, and "
    "fill every field of the schema truthfully. If unsure of a gender, use 'unknown'. "
    "Base ages on visual appearance."
)


def get_active_vision(db: Session) -> Optional[VisionConfig]:
    return (
        db.query(VisionConfig)
        .filter(VisionConfig.status == 1)
        .order_by(VisionConfig.id.desc())
        .first()
    )


def ensure_default_vision(db: Session) -> VisionConfig:
    """Seed a default active vision config if none exists."""
    active = get_active_vision(db)
    if active:
        return active
    vc = VisionConfig(
        provider="groq",
        model_name=DEFAULT_VISION_MODEL,
        prompt=DEFAULT_VISION_PROMPT,
        status=1,
        created_by="system",
    )
    db.add(vc)
    db.commit()
    db.refresh(vc)
    print(f"🌱 Seeded default vision_config id={vc.id}")
    return vc


def activate_vision(db: Session, config_id: int) -> Optional[VisionConfig]:
    """Make an existing vision_config row the active one; deactivate all others."""
    target = db.query(VisionConfig).filter(VisionConfig.id == config_id).first()
    if not target:
        return None
    db.query(VisionConfig).filter(VisionConfig.status == 1).update({VisionConfig.status: 0})
    target.status = 1
    db.commit()
    db.refresh(target)
    return target


def create_and_activate_vision(db: Session, provider: str, model_name: str,
                               prompt: str, changed_by: str) -> VisionConfig:
    """Insert a new active vision row and deactivate all previous rows (atomic)."""
    db.query(VisionConfig).filter(VisionConfig.status == 1).update({VisionConfig.status: 0})
    vc = VisionConfig(
        provider=provider,
        model_name=model_name,
        prompt=prompt,
        status=1,
        created_by=changed_by,
    )
    db.add(vc)
    db.commit()
    db.refresh(vc)
    return vc

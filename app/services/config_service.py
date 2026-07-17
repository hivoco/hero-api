"""Helpers for the live pipeline_config: active-row lookup, default seed, job snapshot."""

from typing import Optional
from sqlalchemy.orm import Session

from app.models.pipeline_config import PipelineConfig

# Snapshot fields copied from the active config onto each job at creation time.
SNAPSHOT_FIELDS = (
    "photo_provider",
    "photo_model",
    "video_provider_1",
    "video_provider_2",
    "video_model",
)

# A sensible starter config so the system is runnable before an admin saves one.
DEFAULT_CONFIG = dict(
    is_active=True,
    version_label="v1-default",
    photo_provider="google",
    photo_model="nano-banana-pro",
    photo_quality="2K",
    photo_prompts={
        "_note": "Keyed by job.stage_key, e.g. english_mountainpeaks_challenge1_father_daughter",
    },
    photo_count=2,
    video_provider_1="seedance",
    video_provider_2="kling",
    video_model="seedance-v2",
    video_quality="720p",
    video_prompts={
        "_note": "Keyed by job.stage_key; image-to-video prompt per stage",
    },
    video_duration_sec=5,
    stitch_pattern={
        "_note": "Keyed by job.stage_key; ordered sequence of clips to stitch",
    },
    music_url=None,
    endcard_url=None,
    max_retry=3,
    stuck_after_minutes=15,
    notes="Auto-seeded default configuration.",
    created_by="system",
)


def get_active_config(db: Session) -> Optional[PipelineConfig]:
    """Return the single active pipeline_config row, or None."""
    return (
        db.query(PipelineConfig)
        .filter(PipelineConfig.is_active == True)  # noqa: E712
        .order_by(PipelineConfig.id.desc())
        .first()
    )


def ensure_default_config(db: Session) -> PipelineConfig:
    """Seed a default active config if none exists (so jobs can snapshot a config_id)."""
    active = get_active_config(db)
    if active:
        return active
    config = PipelineConfig(**DEFAULT_CONFIG)
    db.add(config)
    db.commit()
    db.refresh(config)
    print(f"🌱 Seeded default pipeline_config id={config.id}")
    return config


def snapshot_config_onto_job(job, config: PipelineConfig) -> None:
    """Copy config_id + provider/model/quality from the active config onto a job.

    NOT called at submit any more — those columns stay NULL until the worker
    picks the job up (the frontend supplies none of them). Kept for the worker.

    ⚠️ `pipeline_config.video_provider_1/_2` are ('seedance','kling') while
    `jobs.video_provider` is ENUM('kie','segmind') — the two no longer share a
    value domain, so the provider is intentionally NOT copied here. Reconcile
    those enums before wiring it up.
    """
    job.config_id = config.id
    job.photo_provider = config.photo_provider
    job.photo_model = config.photo_model
    job.video_model = config.video_model
    job.quality = config.video_quality

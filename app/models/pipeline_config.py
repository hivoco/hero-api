from sqlalchemy import Column, BigInteger, String, Integer, Boolean, Text, DateTime, Enum, JSON
from app.core.database import Base
from app.core.timezone import get_ist_now

PHOTO_PROVIDERS = ("segmind", "kie", "google")
VIDEO_PROVIDERS = ("seedance", "kling")


class PipelineConfig(Base):
    """Live, admin-edited pipeline settings. Exactly one row is_active=1 at a time.

    Jobs snapshot the active row's id (jobs.config_id) plus the provider/model/quality
    fields at creation time, so a mid-flight admin change never affects in-flight jobs.
    """

    __tablename__ = "pipeline_config"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    is_active = Column(Boolean, default=False, nullable=False)
    version_label = Column(String(32))

    # Photo stage
    photo_provider = Column(Enum(*PHOTO_PROVIDERS, name="photo_provider_enum"), nullable=False)
    photo_model = Column(String(64), nullable=False)
    photo_quality = Column(String(16), nullable=False)
    photo_prompts = Column(JSON, nullable=False)        # { stage_key -> prompt template }
    photo_count = Column(Integer, default=2, nullable=False)

    # Video stage (image-to-video; two clips, providers may differ)
    video_provider_1 = Column(Enum(*VIDEO_PROVIDERS, name="video_provider_1_enum"), nullable=False)
    video_provider_2 = Column(Enum(*VIDEO_PROVIDERS, name="video_provider_2_enum"), nullable=False)
    video_model = Column(String(64), nullable=False)
    video_quality = Column(String(16), nullable=False)
    video_prompts = Column(JSON, nullable=False)        # { stage_key -> i2v prompt }
    video_duration_sec = Column(Integer, default=5, nullable=False)

    # Stitch stage
    stitch_pattern = Column(JSON, nullable=False)       # { stage_key -> ordered sequence }
    music_url = Column(Text)
    endcard_url = Column(Text)

    # Retry / TAT tunables (read by the in-DB watchdog)
    max_retry = Column(Integer, default=3, nullable=False)
    stuck_after_minutes = Column(Integer, default=15, nullable=False)

    # Meta
    notes = Column(String(255))
    created_by = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=get_ist_now, nullable=False)

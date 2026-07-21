from sqlalchemy import Column, BigInteger, String, Integer, Boolean, Text, DateTime, Enum, JSON
from app.core.database import Base
from app.core.timezone import get_ist_now

# Photo stage
PHOTO_PROVIDERS = ("segmind", "kie", "google", "openai")
PHOTO_QUALITIES = ("512px", "1K", "2K", "4K", "low", "medium", "high", "auto")

# Video stage. The two image-to-video clips use ('seedance','kling'); the
# `video_provider` / `grok_provider` routing enums use ('kie','segmind').
VIDEO_PROVIDERS = ("seedance", "kling")
GROK_PROVIDERS = ("kie", "segmind")
VIDEO_QUALITIES = ("720p", "1080p")


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
    photo_provider = Column(Enum(*PHOTO_PROVIDERS, name="photo_provider_enum"), nullable=True)
    photo_model = Column(String(64), nullable=False)
    photo_quality = Column(Enum(*PHOTO_QUALITIES, name="photo_quality_enum"), nullable=False)
    photo_size = Column(String(16), nullable=False, default="1440x2560")
    photo_prompt = Column(Text, nullable=False)         # single text prompt for the photo stage
    photo_count = Column(Integer, default=3, nullable=False)

    # Video stage (image-to-video; two clips, providers may differ)
    video_provider = Column(Enum(*GROK_PROVIDERS, name="video_provider_enum"), nullable=True)
    video_model = Column(String(64), nullable=False)
    grok_provider = Column(Enum(*GROK_PROVIDERS, name="grok_provider_enum"), nullable=False, default="kie")
    video_quality = Column(Enum(*VIDEO_QUALITIES, name="video_quality_enum"), nullable=False)
    video_prompts = Column(JSON, nullable=False)        # { stage_key -> i2v prompt }
    video_duration_sec = Column(Integer, default=5, nullable=False)
    video_provider_1 = Column(Enum(*VIDEO_PROVIDERS, name="video_provider_1_enum"), nullable=False, default="seedance")
    video_provider_2 = Column(Enum(*VIDEO_PROVIDERS, name="video_provider_2_enum"), nullable=False, default="kling")

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

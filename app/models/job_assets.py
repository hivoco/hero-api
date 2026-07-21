from sqlalchemy import Column, BigInteger, Text, DateTime
from app.core.database import Base
from app.core.timezone import get_ist_now


class JobAssets(Base):
    """Per-job asset URLs, written incrementally as the pipeline advances (1:1 with jobs)."""

    __tablename__ = "job_assets"

    job_id = Column(BigInteger, primary_key=True)
    selfie_url = Column(Text)         # enqueue: combined child + parent selfie
    photo_url_1 = Column(Text)        # photo stage
    photo_url_2 = Column(Text)        # photo stage
    photo_url_3 = Column(Text)        # photo stage
    photo_url_4 = Column(Text)        # photo stage
    video_url_1 = Column(Text)        # video stage (i2v from photo_1) = U1
    video_url_2 = Column(Text)        # video stage (i2v from photo_2) = U2
    video_url_3 = Column(Text)        # video stage (i2v from photo_3) = U3
    video_url_4 = Column(Text)        # video stage (i2v from photo_4) = U4
    audio_url = Column(Text)          # optional voiced-name / music track
    final_video_url = Column(Text)    # stitch stage
    error = Column(Text)              # last failure message
    created_at = Column(DateTime, default=get_ist_now, nullable=False)
    updated_at = Column(DateTime, default=get_ist_now, onupdate=get_ist_now, nullable=False)

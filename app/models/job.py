from sqlalchemy import Column, BigInteger, String, Integer, DateTime, Enum
from app.core.database import Base
from app.core.timezone import get_ist_now

# ── Domain enums ──────────────────────────────────────────────────────
PARENT_ROLES = ("father", "mother")
CHILD_ROLES = ("son", "daughter")
# Must match the frontend carousel titles (and the DB `jobs.world` enum).
WORLDS = ("Mountain Peaks", "Jungle Valley", "Coastal Kingdom")

JOB_STATUSES = (
    "wait",              # awaiting OTP verification
    "process_stop",      # held for manual review / paused
    "unverified",        # OTP verified but selfie was never photo-validated (admin had validation off)
    "queued",            # ready for the worker to pick up
    "photo_processing", "photo_done",
    "video_processing", "video_done",
    "stitching", "uploaded",
    "sent",
    "failed",
)
FAILED_STAGES = ("photo", "video", "stitch", "delivery")

PHOTO_PROVIDERS = ("segmind", "kie", "google")
VIDEO_PROVIDERS = ("seedance", "kling")

# The eight story slugs (must match the frontend `src/lib/stories.ts`). Stored
# verbatim in jobs.challenge — that column is the chosen story now, not a number.
STORIES = (
    "dragon-eggs", "magical-herb", "golden-crown", "fairy-rescue",
    "lost-kitten", "tired-dragon", "lost-puppy", "snowman",
)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(36), nullable=False)

    # Story inputs (collected on the form)
    child_name = Column(String(120), nullable=True)   # no longer collected on the form
    parent_name = Column(String(120), nullable=False)
    # Roles are derived from the validated selfie, so they're NULL exactly when
    # the photo wasn't validated (admin had photo validation OFF at submit) — that
    # NULL is what makes such a job "unverified" once OTP is verified. Keep this
    # invariant (validated ⟺ roles set); if roles ever become user-entered while
    # validation is off, reintroduce an explicit flag instead.
    parent_role = Column(Enum(*PARENT_ROLES, name="parent_role_enum"), nullable=True)
    child_role = Column(Enum(*CHILD_ROLES, name="child_role_enum"), nullable=True)
    world = Column(Enum(*WORLDS, name="world_enum"), nullable=True)   # world screen removed
    challenge = Column(Enum(*STORIES, name="challenge_enum"), nullable=False)  # chosen story slug
    language = Column(String(32), nullable=False, default="English")
    city = Column(String(120))

    # State machine
    status = Column(Enum(*JOB_STATUSES, name="job_status_enum"), nullable=False, default="wait")
    retry_count = Column(Integer, nullable=False, default=0)
    locked_by = Column(String(64))
    locked_at = Column(DateTime)
    failed_stage = Column(Enum(*FAILED_STAGES, name="failed_stage_enum"))
    last_error_code = Column(String(64))

    # Pipeline snapshot (which config rendered this film)
    config_id = Column(BigInteger, nullable=False)
    photo_provider = Column(Enum(*PHOTO_PROVIDERS, name="job_photo_provider_enum"))
    photo_model = Column(String(64))
    video_provider_1 = Column(Enum(*VIDEO_PROVIDERS, name="job_video_provider_1_enum"))
    video_provider_2 = Column(Enum(*VIDEO_PROVIDERS, name="job_video_provider_2_enum"))
    video_model = Column(String(64))
    quality = Column(String(16))

    # Compliance (DPDP — per-job, version-snapshotted)
    consent_version = Column(String(32), nullable=False)
    consent_ts = Column(DateTime, nullable=False)

    # Attribution
    ip_address = Column(String(45))   # client IP at submit (IPv4/IPv6)
    utm_source = Column(String(128))
    utm_medium = Column(String(128))
    utm_campaign = Column(String(128))

    created_at = Column(DateTime, default=get_ist_now, nullable=False)
    updated_at = Column(DateTime, default=get_ist_now, onupdate=get_ist_now, nullable=False)

    @property
    def stage_key(self) -> str:
        """Compose the prompt/asset key the worker uses to look up config blobs:
        e.g. english_dragon-eggs_father_daughter  (world removed; challenge = story slug)
        """
        rel = f"{self.parent_role}_{self.child_role}"
        return f"{self.language}_{self.challenge}_{rel}".lower()

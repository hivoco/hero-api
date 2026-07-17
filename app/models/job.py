from sqlalchemy import Column, BigInteger, String, Integer, DateTime, Enum
from app.core.database import Base
from app.core.timezone import get_ist_now

# ── Domain enums ──────────────────────────────────────────────────────
PARENT_ROLES = ("father", "mother")
CHILD_ROLES = ("son", "daughter")

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

PHOTO_PROVIDERS = ("segmind", "kie", "google", "openai")
VIDEO_PROVIDERS = ("kie", "segmind")

# Languages the DB enum accepts (jobs.language) — lowercase, matched
# case-insensitively by the column's *_ci collation.
LANGUAGES = (
    "english", "hindi", "bengali", "kannada",
    "tamil", "telugu", "malayalam", "marathi", "gujarati",
)

# The eight story slugs (must match the frontend `src/lib/stories.ts`). Stored
# verbatim in jobs.story — that column is the chosen story.
STORIES = (
    "dragon-eggs", "magical-herb", "golden-crown", "fairy-rescue",
    "lost-kitten", "tired-dragon", "lost-puppy", "snowman",
)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(36), nullable=False)

    # Story inputs (collected on the form)
    child_name = Column(String(120), nullable=True)   # not collected on the form → always NULL
    parent_name = Column(String(120), nullable=False)
    # Roles are derived from the validated selfie, so they're NULL exactly when
    # the photo wasn't validated (admin had photo validation OFF at submit) — that
    # NULL is what makes such a job "unverified" once OTP is verified. Keep this
    # invariant (validated ⟺ roles set); if roles ever become user-entered while
    # validation is off, reintroduce an explicit flag instead.
    parent_role = Column(Enum(*PARENT_ROLES, name="parent_role_enum"), nullable=True)
    child_role = Column(Enum(*CHILD_ROLES, name="child_role_enum"), nullable=True)
    # The chosen story slug (a plain varchar in the DB; validated against STORIES).
    story = Column(String(64), nullable=False)
    language = Column(String(32), nullable=False, default="english")
    city = Column(String(120))   # derived from the client IP (GeoIP) — the form has no city field

    # State machine
    status = Column(Enum(*JOB_STATUSES, name="job_status_enum"), nullable=False, default="wait")
    retry_count = Column(Integer, nullable=False, default=0)
    locked_by = Column(String(64))     # worker-owned → NULL at submit
    locked_at = Column(DateTime)       # worker-owned → NULL at submit
    failed_stage = Column(Enum(*FAILED_STAGES, name="failed_stage_enum"))
    last_error_code = Column(String(64))   # worker-owned → NULL at submit

    # Pipeline snapshot — NOT set at submit (the frontend has none of this).
    # The worker fills these in when it picks the job up.
    config_id = Column(BigInteger, nullable=True)
    photo_provider = Column(Enum(*PHOTO_PROVIDERS, name="job_photo_provider_enum"))
    photo_model = Column(String(64))
    video_provider = Column(Enum(*VIDEO_PROVIDERS, name="job_video_provider_enum"))
    video_model = Column(String(64))
    quality = Column(String(16))

    # Compliance (DPDP — per-job, version-snapshotted). Nullable because rows
    # predating the column exist; every new submit writes both.
    consent_version = Column(String(32), nullable=True)
    consent_ts = Column(DateTime, nullable=True)

    # Attribution
    ip_address = Column(String(45))   # client IP at submit (IPv4/IPv6)
    utm_source = Column(String(128))  # NULL unless present in the URL
    utm_medium = Column(String(128))
    utm_campaign = Column(String(128))

    created_at = Column(DateTime, default=get_ist_now, nullable=False)
    updated_at = Column(DateTime, default=get_ist_now, onupdate=get_ist_now, nullable=False)

    @property
    def stage_key(self) -> str:
        """Compose the prompt/asset key the worker uses to look up config blobs:
        e.g. english_dragon-eggs_father_daughter
        """
        rel = f"{self.parent_role}_{self.child_role}"
        return f"{self.language}_{self.story}_{rel}".lower()

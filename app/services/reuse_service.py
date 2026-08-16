"""Reuse a previously-rendered video for a repeat request.

Before a job enters the queue, we check whether the *same person* (same user /
mobile number) has already produced a finished video for the *same relationship*
(same parent_role + child_role). If so, we don't re-render: we clone that older
job's render (its generation snapshot + all job_assets outputs) onto the new job,
keep the new request's identity (name + the freshly-uploaded selfie), lock it to
Krishna for audit, and the caller sets its status to `unverified` (the same
status used when a photo wasn't validated — now also the "reused render" bucket).
"""

from sqlalchemy.orm import Session

from app.core.timezone import get_ist_now
from app.models.job import Job
from app.models.job_assets import JobAssets

LOCKED_BY = "Krishna"

# jobs columns copied from the old render → the new job. `parent_name` (the
# "name") is deliberately excluded and kept from the new request; so are
# user_id / roles / city / ip / utm_* / consent_* / created_at (identity &
# attribution of the current request).
_JOB_COPY_COLS = (
    "child_name",
    "story",
    "language",
    "config_id",
    "photo_provider",
    "photo_model",
    "video_provider",
    "video_model",
    "quality",
)

# job_assets columns copied from the old render. `selfie_url` is excluded so the
# new (just-uploaded) original photo is kept.
_ASSET_COPY_COLS = (
    "photo_url_1",
    "photo_url_2",
    "photo_url_3",
    "photo_url_4",
    "video_url_1",
    "video_url_2",
    "video_url_3",
    "video_url_4",
    "audio_url",
    "final_video_url",
)


def reuse_previous_render(db: Session, job: Job) -> bool:
    """If a prior finished render exists for this user + relationship, clone it
    onto `job` (columns + assets), lock it to Krishna, and clear failure fields.
    Does NOT set the status — the caller sets it to "unverified" on True.

    Returns True if a render was reused, False otherwise (caller then queues).
    """
    # A relationship is required to match on; NULL roles can't be matched.
    if job.parent_role is None or job.child_role is None:
        return False

    prior = (
        db.query(Job)
        .join(JobAssets, JobAssets.job_id == Job.id)
        .filter(
            Job.user_id == job.user_id,
            Job.id != job.id,
            Job.parent_role == job.parent_role,
            Job.child_role == job.child_role,
            JobAssets.final_video_url.isnot(None),
        )
        .order_by(Job.id.desc())
        .first()
    )
    if prior is None:
        return False

    prior_assets = db.query(JobAssets).filter(JobAssets.job_id == prior.id).first()
    if prior_assets is None or not prior_assets.final_video_url:
        return False

    # Copy the render snapshot onto the new job (keep parent_name + attribution).
    for col in _JOB_COPY_COLS:
        setattr(job, col, getattr(prior, col))

    job.locked_by = LOCKED_BY
    job.locked_at = get_ist_now()
    job.failed_stage = None
    job.last_error_code = None
    job.updated_at = get_ist_now()

    # Copy the render outputs onto the new job's assets, keeping its own selfie.
    cur_assets = db.query(JobAssets).filter(JobAssets.job_id == job.id).first()
    if cur_assets is None:
        cur_assets = JobAssets(job_id=job.id)
        db.add(cur_assets)
    for col in _ASSET_COPY_COLS:
        setattr(cur_assets, col, getattr(prior_assets, col))

    print(f"♻️  Job {job.id} reused render from job {prior.id} "
          f"(user {job.user_id}, {job.parent_role}/{job.child_role}) → unverified")
    return True

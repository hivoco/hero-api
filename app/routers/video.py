import logging
import os
import random
from uuid import uuid4
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Form, File, UploadFile, Request, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import hash_phone, encrypt_phone
from app.core.otp import generate_otp, hash_otp, send_otp, send_thank_you
from app.core.config import settings
from app.core.s3 import upload_fileobj_to_s3
from app.core.timezone import get_ist_now
from app.core.redis import RateLimiter, Cache, FeatureFlags
from app.core.geoip import city_from_ip
from app.routers.photo_validation import verify_validation_token
from app.services.settings_service import (
    get_max_videos_per_user, get_unlimited_numbers, get_held_numbers,
    get_allow_multiple_requests, get_enabled_stories,
)

from app.models.user import User
from app.models.user_verification import UserVerification
from app.models.user_otp import UserOTP
from app.models.job import Job, PARENT_ROLES, CHILD_ROLES
from app.models.job_assets import JobAssets

router = APIRouter(prefix="/api/v1/video", tags=["video"])

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# The per-user video limit, the unlimited-numbers whitelist, and the held-numbers
# list are now admin-editable runtime settings (Backend Config page). They are read
# via settings_service, which serves them from an in-process cache (no DB hit per
# request). Defaults / seed live in app.services.settings_service.

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# `allow_multiple_requests` is an admin-editable runtime setting (read per-request
# via settings_service, env default ALLOW_MULTIPLE_REQUESTS). When True, a phone
# number can submit unlimited times — skips both the "one video in flight" guard
# and the per-user cap. When False, MAX_VIDEOS_PER_USER (whitelist-bypassed) is
# enforced.


def _client_ip(request: Request) -> str | None:
    """Best-effort client IP: first hop of X-Forwarded-For (behind a proxy/ALB), else peer."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None


def _clean_number(mobile_number: str) -> str:
    n = mobile_number.strip().replace("+", "").replace(" ", "").replace("-", "")
    if n.startswith("91") and len(n) == 12:
        n = n[2:]
    return n


def _validate_inputs(parent_name, parent_role, child_role):
    if not parent_name or not parent_name.strip():
        raise HTTPException(status_code=400, detail="Parent name is required.")
    # Roles are now picked by the user on the form (not derived from the photo),
    # so they're always required and must be valid enum values.
    if parent_role not in PARENT_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid parent_role. Must be one of: {', '.join(PARENT_ROLES)}")
    if child_role not in CHILD_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid child_role. Must be one of: {', '.join(CHILD_ROLES)}")
    # The story is no longer user-chosen — it's assigned at random per job
    # (see submit), so there's nothing to validate here.


def _upload_selfie(photo: UploadFile, user_id: str, job_id: int) -> str:
    ext = os.path.splitext(photo.filename)[1].lower()
    # Stored under the worker's data prefix so the pipeline picks it up.
    key = f"hero_worker_data/raw_images/{user_id}_{job_id}{ext}"
    print(f"📤 Uploading selfie to S3: {key}")
    url = upload_fileobj_to_s3(photo.file, key, photo.content_type)
    print(f"✅ Selfie uploaded: {url}")
    return url


@router.get("/tc-status")
def tc_status(
    mobile_number: str = Query(..., min_length=10, max_length=15),
    db: Session = Depends(get_db),
):
    """Has this number already accepted the campaign T&C? Lets the details form
    pre-tick the consent box for a returning user. Unknown numbers simply
    return false."""
    user = db.query(User).filter(User.phone_hash == hash_phone(mobile_number)).first()
    return {"tc_accepted": bool(user and user.tc_accepted)}


@router.post("/submit")
async def submit_video_form(
    request: Request,
    mobile_number: str = Form(...),
    child_name: str = Form(""),
    parent_name: str = Form(...),
    parent_role: str = Form(""),
    child_role: str = Form(""),
    language: str = Form("English"),
    city: str = Form(""),
    consent_accepted: bool = Form(...),
    utm_source: str = Form(""),
    utm_medium: str = Form(""),
    utm_campaign: str = Form(""),
    photo: UploadFile = File(...),
    validation_token: str = Form(""),
    db: Session = Depends(get_db),
):
    # ── Rate limits ──────────────────────────────────────────────────
    allowed_global, _ = RateLimiter.check_global_limit("video_submit_global", max_requests=2000000, window_seconds=60)
    if not allowed_global:
        raise HTTPException(status_code=503, detail="Server is busy. Please try again in a few seconds.",
                            headers={"Retry-After": "5"})

    allowed, _ = RateLimiter.check_rate_limit(mobile_number.strip(), "video_submit", max_requests=5, window_seconds=300)
    if not allowed:
        retry_after = RateLimiter.get_remaining_time(mobile_number.strip(), "video_submit")
        raise HTTPException(status_code=429, detail=f"Too many requests. Please try again in {retry_after} seconds.",
                            headers={"Retry-After": str(retry_after)})

    # ── Photo validation token (skipped when admin turns validation off) ──
    # When validation is on, the selfie was checked client-side and carries a
    # token, and the derived roles are required. When off, there's no photo check
    # → no token and blank roles persist as NULL, which is what marks the job an
    # unverified photo and decides its post-OTP status.
    photo_validation_on = FeatureFlags.is_enabled("photo_validation", default=True)
    if photo_validation_on:
        if not verify_validation_token(validation_token):
            raise HTTPException(status_code=400, detail="Photo validation required. Please validate your photo before submitting.")

    # ── Field validation ─────────────────────────────────────────────
    if not mobile_number or len(mobile_number.strip()) < 10:
        raise HTTPException(status_code=400, detail="Invalid mobile number. Please provide a valid 10-digit number.")
    _validate_inputs(parent_name, parent_role, child_role)
    # Roles come from the form now, so they're always set.
    parent_role_val = parent_role or None
    child_role_val = child_role or None
    # child_name isn't collected on the form → persist as NULL.
    child_name_val = child_name.strip() or None
    # Story is not user-chosen — each job gets a random story from the admin-
    # enabled pool (Backend Config). One enabled → pinned; none → all 8.
    story_val = random.choice(get_enabled_stories())
    if not consent_accepted:
        raise HTTPException(status_code=400, detail="You must accept the consent terms to continue.")
    if not photo.filename:
        raise HTTPException(status_code=400, detail="No photo uploaded. Please upload a photo.")
    if os.path.splitext(photo.filename)[1].lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

    # ── User lookup / creation ───────────────────────────────────────
    phone_hash = hash_phone(mobile_number)
    cleaned_number = _clean_number(mobile_number)
    user = db.query(User).filter(User.phone_hash == phone_hash).first()

    if (not get_allow_multiple_requests() and user and user.video_count >= get_max_videos_per_user()
            and cleaned_number not in get_unlimited_numbers()):
        raise HTTPException(status_code=403, detail="You have already generated the maximum number of videos.")

    if not user:
        user = User(
            id=str(uuid4()),
            phone_hash=phone_hash,
            phone_encrypted=encrypt_phone(mobile_number),
            video_count=0,
            tc_accepted=True,   # submit is gated on consent_accepted above
        )
        db.add(user)
        db.flush()
    elif not user.tc_accepted:
        # Sticky: once accepted, a returning user gets the box pre-ticked.
        user.tc_accepted = True

    verification = db.query(UserVerification).filter_by(user_id=user.id).first()
    if not verification:
        verification = UserVerification(user_id=user.id, is_verified=False, verification_method="otp")
        db.add(verification)
        db.flush()

    consent_ts = get_ist_now()
    client_ip = _client_ip(request)
    # City: use what the form sent, else derive it from the client IP via the
    # offline GeoIP DB (best-effort — None if it can't be resolved).
    resolved_city = (city.strip() or None) if city else None
    if not resolved_city:
        resolved_city = city_from_ip(client_ip)

    def build_job(status: str) -> Job:
        # Only what the frontend actually gives us (+ server-derived ip/city).
        # The pipeline snapshot — config_id, photo_provider, photo_model,
        # video_provider, video_model, quality — is deliberately left NULL and
        # filled in by the worker. So are child_name, locked_by, locked_at and
        # last_error_code. UTM stays NULL unless it came in on the URL.
        return Job(
            user_id=user.id,
            child_name=child_name_val,
            parent_name=parent_name.strip(),
            parent_role=parent_role_val,
            child_role=child_role_val,
            story=story_val,
            language=(language or "english").strip(),
            city=resolved_city,
            status=status,
            consent_version=settings.CONSENT_VERSION,
            consent_ts=consent_ts,
            ip_address=client_ip,
            utm_source=utm_source or None,
            utm_medium=utm_medium or None,
            utm_campaign=utm_campaign or None,
        )

    # ── Unverified user: create 'wait' job + send OTP ────────────────
    if not verification.is_verified:
        existing_job = db.query(Job).filter(Job.user_id == user.id, Job.status == "wait").first()
        if existing_job:
            otp = generate_otp()
            logger.info("OTP issued for user %s", user.id)
            db.add(UserOTP(id=str(uuid4()), user_id=user.id, otp_hash=hash_otp(otp),
                           expires_at=get_ist_now() + timedelta(minutes=settings.OTP_EXPIRY_MINUTES),
                           attempts=0, is_used=False))
            db.commit()
            send_otp(mobile_number, otp)
            return {"status": "otp_sent", "job_id": existing_job.id, "message": "OTP sent. Please verify to process your video."}

        try:
            job = build_job("wait")
            db.add(job)
            db.flush()
            url = _upload_selfie(photo, user.id, job.id)
            db.add(JobAssets(job_id=job.id, selfie_url=url))

            otp = generate_otp()
            logger.info("OTP issued for user %s", user.id)
            db.add(UserOTP(id=str(uuid4()), user_id=user.id, otp_hash=hash_otp(otp),
                           expires_at=get_ist_now() + timedelta(minutes=settings.OTP_EXPIRY_MINUTES),
                           attempts=0, is_used=False))
            db.commit()
            send_otp(mobile_number, otp)
            return {"status": "otp_sent", "job_id": job.id, "message": "OTP sent. Please verify to process your video."}
        except HTTPException:
            db.rollback()
            raise
        except Exception as e:
            print(f"❌ Error in video submission: {str(e)}")
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to process your request: {str(e)}")

    # ── Verified user: reject if a job is still in flight ────────────
    # (skipped while allow_multiple_requests is on, so testing can submit freely)
    if not get_allow_multiple_requests():
        cached_job_id = Cache.get_pending_video(user.id)
        if cached_job_id:
            return {"status": "pending", "job_id": int(cached_job_id),
                    "message": "Your previous video is still being processed. Please wait before creating a new one."}

        pending_job = db.query(Job).filter(Job.user_id == user.id, Job.status.notin_(["sent", "failed"])).first()
        if pending_job:
            Cache.set_pending_video(user.id, str(pending_job.id))
            return {"status": "pending", "job_id": pending_job.id,
                    "message": "Your previous video is still being processed. Please wait before creating a new one."}

    # ── Verified user: create a fresh queued job ─────────────────────
    # OTP is already verified here, so whether the photo was validated decides the
    # status: not validated → "unverified"; otherwise held → "process_stop", else queued.
    if not photo_validation_on:
        initial_status = "unverified"
    elif cleaned_number in get_held_numbers():
        initial_status = "process_stop"
    else:
        initial_status = "queued"
    try:
        job = build_job(initial_status)
        db.add(job)
        db.flush()
        url = _upload_selfie(photo, user.id, job.id)
        db.add(JobAssets(job_id=job.id, selfie_url=url))
        user.video_count += 1
        db.commit()

        Cache.set_pending_video(user.id, str(job.id))
        try:
            send_thank_you(mobile_number, job.parent_name)
        except Exception as e:
            logger.warning("Failed to send thank you message: %s", str(e))

        return {"status": "video_created", "job_id": job.id, "message": "Your video is being processed."}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        print(f"❌ Error in video creation: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to process video request: {str(e)}")

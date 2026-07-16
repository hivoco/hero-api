# Hero Destini 125 — Backend (FastAPI)

"Meri Kahani, Mera Hero" — a personalised AI film campaign. A user uploads one
combined photo of a **child + parent**, picks a story (world + challenge + names
+ roles), verifies their WhatsApp number via OTP, and the pipeline turns the
selfie into **2 photos → 2 videos → a stitched film** delivered on WhatsApp.

This service is the hub: auth/OTP, photo validation, job creation, the
admin/jobs APIs, the versioned pipeline config, and reporting. The actual
photo/video/stitch rendering is done by a separate worker that picks up
`queued` jobs (MySQL lock-and-advance) and writes asset URLs back.

## Stack
FastAPI · SQLAlchemy · MySQL 8 · Redis · S3 (boto3) · Groq vision · WhatsApp (MessagingHub)

## Layout
```
app/
  core/        config, database, redis, security, s3, otp, admin_auth, timezone
  models/      user, user_verification, user_otp, pipeline_config, config_audit, job, job_assets
  routers/     video (submit), auth (otp), photo_validation, jobs (admin), config (pipeline), admin_auth
  services/    config_service (active config + seed + snapshot), otp_service
  workers/     photo_queue_worker
  main.py      app wiring, startup checks, default-config seed
sql/schema.sql canonical MySQL bootstrap (tables + in-DB watchdog EVENT)
```

## Setup
```bash
cd backend
python3.10 -m venv .venv          # Python 3.10 (pinned in .python-version)
.venv/bin/python -m pip install -r requirements.txt

cp .env.example .env        # then fill in real values
# Generate a Fernet key:
#   .venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Generate the admin bcrypt hash:
#   .venv/bin/python -c "import bcrypt; print(bcrypt.hashpw(b'YOUR_PW', bcrypt.gensalt()).decode())"

# Create the database schema (tables + watchdog event):
mysql -h HOST -u USER -p hero_destini < sql/schema.sql
# On RDS, also set event_scheduler = ON in the parameter group (see notes in schema.sql).

./start_server.sh           # uvicorn on 127.0.0.1:8000  (docs at /docs in dev)
```

The first startup seeds a default active `pipeline_config` row so jobs can
snapshot a `config_id` immediately; tune it from the admin **Pipeline Config** page.

## Key endpoints
| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/photo-validation/check_photo` | Validate the combined child+parent photo (Groq) → returns a signed token |
| POST | `/api/v1/video/submit` | Submit the form + photo; sends OTP / creates the job |
| POST | `/api/v1/auth/verify-otp` · `/resend-otp` | OTP verification |
| POST | `/api/v1/admin/login` | Admin JWT login |
| GET/PATCH | `/api/v1/jobs/...` | Admin: list/detail/update/fields/video-url/send-video, reports/*, settings/photo-validation |
| GET/POST | `/api/v1/config/...` | Admin: active/list/audit, create+activate, rollback, pause/resume |

## Photo validation
Groq vision classifies the combined photo. Unlike the closeup blueprint, it
**accepts a child and multiple faces** — it only rejects unclear / no-face /
obstructed / NSFW / screenshot images. Admins can toggle validation off (it also
auto-disables if Groq is fully saturated).

## Job lifecycle
`wait → (queued | process_stop) → photo_processing → photo_done → video_processing → video_done → stitching → uploaded → sent` (or `failed` with `failed_stage`).
The in-DB watchdog (`sql/schema.sql`) rewinds stuck `*_processing` rows and fails them past `max_retry`, surviving an all-EC2-down outage.

## Worker (separate, not in this repo yet)
The renderer should: lock a `queued` job (`FOR UPDATE SKIP LOCKED`), read its
`config_id` snapshot + `stage_key` (e.g. `english_dragonslayer_challenge1_father_daughter`)
to look up prompts in `pipeline_config`, write asset URLs to `job_assets`, and
advance status. The optional `photo_queue_worker` here only handles burst photo
validation: `python -m app.workers.photo_queue_worker`.
# hero-api

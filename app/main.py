import boto3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.routers import video, auth, photo_validation, jobs, config, vision, settings as settings_router, admin_auth
from app.core.redis import RedisClient
from app.core.config import settings


@asynccontextmanager
async def lifespan(_app: FastAPI):
    print("\n" + "=" * 50)
    print("  Starting Hero Destini API...")
    print("=" * 50)
    print(f"  Environment: {settings.APP_ENV}")
    print("-" * 50)

    try:
        from sqlalchemy import text
        from app.core.database import engine
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("  [OK]   MySQL")
    except Exception as e:
        print(f"  [FAIL] MySQL     - {e}")

    try:
        RedisClient.get_client()
        RedisClient._client.ping()
        print(f"  [OK]   Redis     ({settings.REDIS_HOST}:{settings.REDIS_PORT})")
    except Exception as e:
        print(f"  [FAIL] Redis     - {e}")

    try:
        from app.core.s3 import s3_client  # noqa: F401
        sts = boto3.client(
            "sts",
            region_name=settings.AWS_REGION,
            **({"aws_access_key_id": settings.AWS_ACCESS_KEY_ID,
                "aws_secret_access_key": settings.AWS_SECRET_ACCESS_KEY}
               if settings.AWS_ACCESS_KEY_ID else {}),
        )
        identity = sts.get_caller_identity()
        mode = "IAM Role" if ":assumed-role/" in identity["Arn"] else "Access Key"
        print(f"  [OK]   S3        ({settings.AWS_S3_BUCKET} via {mode})")
    except Exception as e:
        print(f"  [FAIL] S3        - {e}")

    if settings.YELLOW_API_KEY and settings.YELLOW_BOT_ID:
        print(f"  [OK]   WhatsApp  (Yellow.ai bot {settings.YELLOW_BOT_ID}, sender {settings.YELLOW_SENDER})")
    else:
        print("  [FAIL] WhatsApp  - Yellow.ai not configured (set YELLOW_* in .env)")

    # Seed a default pipeline config so jobs can snapshot a config_id out of the box
    try:
        from app.core.database import SessionLocal
        from app.services.config_service import ensure_default_config
        from app.services.vision_service import ensure_default_vision
        from app.services.settings_service import ensure_default_settings
        db = SessionLocal()
        try:
            cfg = ensure_default_config(db)
            vc = ensure_default_vision(db)
            ensure_default_settings(db)
            print(f"  [OK]   Pipeline config active (id={cfg.id}), Vision config active (id={vc.id}), App settings seeded")
        finally:
            db.close()
    except Exception as e:
        print(f"  [WARN] Config seed skipped - {e}")

    # IP -> city DB (~130 MB, not in git). Downloads once in the background on a
    # fresh server; never blocks boot. Until it lands, jobs.city stays NULL.
    try:
        import os as _os
        from app.core.geoip import ensure_db_async, _DB_PATH
        if _os.path.isfile(_DB_PATH):
            print(f"  [OK]   GeoIP     ({_os.path.getsize(_DB_PATH) / 1e6:.0f} MB)")
        else:
            print("  [..]   GeoIP     (missing — downloading in background)")
        ensure_db_async()
    except Exception as e:
        print(f"  [WARN] GeoIP skipped - {e}")

    print("-" * 50)
    print("  Hero Destini API is ready!")
    print("=" * 50 + "\n")
    yield

    print("\nShutting down Hero Destini API...")
    try:
        RedisClient.close()
    except Exception:
        pass


is_production = settings.APP_ENV == "production"

app = FastAPI(
    title="Hero Destini 125 — Meri Kahani, Mera Hero API",
    lifespan=lifespan,
    docs_url=None if is_production else "/docs",
    redoc_url=None if is_production else "/redoc",
)

# FastAPI is the single source of CORS headers. If a reverse proxy also adds
# CORS, remove it there (two layers → duplicate Access-Control-Allow-Origin,
# which browsers reject) — do NOT disable this middleware, or direct/local access
# (no proxy) loses CORS entirely.
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=[
#         "https://herodestini.in",
#         "https://www.herodestini.in",
#         "https://admin.herodestini.in",
#         # thefirstimpression.ai deployment — campaign frontend + admin dashboard.
#         # (The API's own origin needs no entry; it never calls itself cross-origin.)
#         "https://hero.thefirstimpression.ai",
#         "https://hero-admin-dashboard.thefirstimpression.ai",
#         "http://localhost:3000",   # campaign frontend (dev)
#         "http://localhost:3001",   # campaign frontend (dev, alt)
#         "http://localhost:8100",   # admin dashboard (dev)
#     ],
#     allow_credentials=True,
#     # Explicitly list every method so the preflight's Access-Control-Allow-Methods
#     # always advertises PATCH/DELETE (some proxies/versions don't expand "*").
#     allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
#     allow_headers=["*"],
#     expose_headers=["*"],
# )


@app.get("/")
def health_check():
    return {"status": True}


@app.get("/api/v1/settings/photo-validation-status")
def photo_validation_status():
    """Public endpoint: whether photo validation is currently required (no auth)."""
    from app.core.redis import FeatureFlags
    return {"enabled": FeatureFlags.is_enabled("photo_validation", default=True)}


app.include_router(video.router)
app.include_router(auth.router)
app.include_router(photo_validation.router)
app.include_router(jobs.router)
app.include_router(jobs.public_router)  # no-auth endpoints (e.g. send-video)
app.include_router(config.router)
app.include_router(vision.router)
app.include_router(settings_router.router)
app.include_router(admin_auth.router)

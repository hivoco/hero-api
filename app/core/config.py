from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str

    # Phone privacy
    PHONE_HASH_SALT: str
    FERNET_KEY: str

    # OTP
    OTP_EXPIRY_MINUTES: int = 5
    # Minutes a user must wait before requesting a fresh OTP. On resend the
    # previous code is invalidated. Keep the frontend countdown in sync.
    OTP_RESEND_COOLDOWN_MINUTES: int = 4

    # AWS / S3
    AWS_REGION: str
    AWS_S3_BUCKET: str
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None

    # Groq API Keys (comma-separated for multiple keys -> more capacity)
    # Example: "key1,key2,key3" for 3x photo-validation throughput
    GROQ_API_KEYS: str

    # Vision provider keys — used when the admin switches the vision provider
    # (vision_config.provider) to 'openai' (ChatGPT) or 'google' (Gemini).
    OPENAI_API_KEY: Optional[str] = None
    GOOGLE_API_KEY: Optional[str] = None

    # Redis
    REDIS_HOST: str
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: Optional[str] = None
    REDIS_DB: int = 0

    # WhatsApp via Yellow.ai engagements API (all secrets live in .env)
    YELLOW_API_URL: str = "https://cloud.yellow.ai/api/engagements/notifications/v2/push"
    YELLOW_BOT_ID: str
    YELLOW_API_KEY: str
    YELLOW_SENDER: str
    # Approved Yellow.ai WhatsApp template IDs.
    YELLOW_OTP_TEMPLATE: str = "otp_message_v1"
    YELLOW_CONFIRM_TEMPLATE: str = "destini_tech_act_wait_v1"
    YELLOW_FAILED_TEMPLATE: str = "failed_message"
    YELLOW_VIDEO_TEMPLATE: str = "destini_tech_act_video_v1"

    # App environment (development / production)
    APP_ENV: str = "development"

    # DPDP consent version stamped onto every job at creation time
    CONSENT_VERSION: str = "v1-dpdp-2026"

    # Max videos a single phone number may generate (whitelist bypasses this)
    MAX_VIDEOS_PER_USER: int = 2
    # When True, a phone number can submit unlimited times — the per-user cap
    # (MAX_VIDEOS_PER_USER) AND the "one video in flight" guard are skipped.
    # Set False to enforce those limits (whitelist still bypasses the cap).
    ALLOW_MULTIPLE_REQUESTS: bool = True

    # Admin auth
    ADMIN_USERNAME: str
    ADMIN_PASSWORD_HASH: str
    # Super-admin — the only role allowed to edit the config / vision / backend
    # settings pages. Defaults keep the server bootable if the env vars are
    # absent; override in .env for a different credential.
    SUPERADMIN_USERNAME: str = "super-admin"
    SUPERADMIN_PASSWORD_HASH: str = "$2b$12$HDQm3szjobE2po0pxZ9rUODj/ypa58pmRpry1mKSp1KrMh3lFPJRe"
    JWT_SECRET_KEY: str

    # Internal API key (server-to-server, e.g. the worker calling admin APIs)
    INTERNAL_API_KEY: str

    # API key that guards the public send-video endpoint (POST
    # /api/v1/jobs/{id}/send-video). Callers pass it as the X-API-Key header.
    SEND_VIDEO_API_KEY: str = ""

    class Config:
        env_file = ".env"

    @property
    def groq_api_keys_list(self) -> list[str]:
        """Parse comma-separated Groq API keys into a list."""
        return [key.strip() for key in self.GROQ_API_KEYS.split(",") if key.strip()]


settings = Settings()

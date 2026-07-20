import hashlib
import logging
import secrets
import httpx

from app.core.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def generate_otp() -> str:
    """Generate a real random 6-digit OTP."""
    return str(secrets.randbelow(900000) + 100000)


def hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode()).hexdigest()


def _format_phone(mobile_number: str) -> str:
    """Normalise to the 10-digit number Yellow.ai expects (no country code)."""
    phone = mobile_number.strip().replace("+", "").replace(" ", "").replace("-", "")
    if phone.startswith("91") and len(phone) == 12:
        phone = phone[2:]
    elif phone.startswith("0") and len(phone) == 11:
        phone = phone[1:]
    return phone


def _send_yellow(mobile_number: str, template_id: str, params: dict, label: str) -> bool:
    """Push a WhatsApp template notification through the Yellow.ai engagements API."""
    if not template_id:
        logger.warning("Yellow.ai %s skipped — no template configured", label)
        return False

    phone = _format_phone(mobile_number)
    notification = {
        "type": "whatsapp",
        "sender": settings.YELLOW_SENDER,
        "templateId": template_id,
    }
    # Some templates (e.g. the failed-message one) take no params — omit the key
    # entirely rather than sending an empty object.
    if params:
        notification["params"] = params
    payload = {"userDetails": {"number": phone}, "notification": notification}
    try:
        response = httpx.post(
            settings.YELLOW_API_URL,
            params={"bot": settings.YELLOW_BOT_ID},
            json=payload,
            headers={
                "x-api-key": settings.YELLOW_API_KEY,
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )
        logger.info("Yellow.ai %s response [%s]: %s", label, response.status_code, response.text)
        # Yellow.ai's push API returns 202 Accepted ({"msgId": ...}) on success,
        # so accept any 2xx — not just 200/201.
        if 200 <= response.status_code < 300:
            return True
        logger.warning("Yellow.ai %s failed [%s]: %s", label, response.status_code, response.text)
        return False
    except Exception as e:
        logger.error("Yellow.ai %s error: %s", label, str(e))
        return False


def send_otp(mobile_number: str, otp: str) -> bool:
    """Send the OTP to the user via WhatsApp (Yellow.ai template `otp_message_v1`)."""
    return _send_yellow(mobile_number, settings.YELLOW_OTP_TEMPLATE, {"1": otp}, "OTP")


def send_thank_you(mobile_number: str, parent_name: str = "") -> bool:
    """Send the confirmation message once an entry is submitted and OTP-verified
    (Yellow.ai template `destini_tech_act_wait_v1`, param 1 = parent name)."""
    return _send_yellow(
        mobile_number,
        settings.YELLOW_CONFIRM_TEMPLATE,
        {"1": parent_name or "there"},
        "confirmation",
    )


def send_failed_message(mobile_number: str) -> bool:
    """Notify the user when video generation has failed (Yellow.ai template
    `failed_message` — takes no params)."""
    return _send_yellow(mobile_number, settings.YELLOW_FAILED_TEMPLATE, {}, "failed")


def send_video(mobile_number: str, video_url: str, parent_name: str = "") -> bool:
    """Deliver the finished video via the Yellow.ai media template
    `destini_tech_act_video_v1` (media.mediaLink = the S3 video URL,
    param 1 = parent name)."""
    return _send_yellow(
        mobile_number,
        settings.YELLOW_VIDEO_TEMPLATE,
        {"media": {"mediaLink": video_url}, "1": parent_name or "there"},
        "video",
    )

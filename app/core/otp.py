import hashlib
import logging
import secrets
import httpx

from app.core.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def generate_otp() -> str:
    # Dev mode (no WhatsApp): always issue the fixed OTP so testers can verify.
    if not settings.WHATSAPP_ENABLED:
        return settings.DEV_OTP
    return str(secrets.randbelow(900000) + 100000)


def hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode()).hexdigest()


def _format_phone(mobile_number: str) -> str:
    """Ensure the phone number carries the 91 country prefix for WhatsApp."""
    phone = mobile_number.strip().replace("+", "").replace(" ", "").replace("-", "")
    if len(phone) == 10:
        phone = "91" + phone
    elif len(phone) == 12 and phone.startswith("91"):
        pass  # already formatted
    return phone


def _send_template(phone: str, payload: dict, label: str) -> bool:
    # WhatsApp disabled (no account yet): skip the API call entirely.
    if not settings.WHATSAPP_ENABLED:
        logger.info("[DEV] WhatsApp disabled — skipping %s to %s (OTP is fixed: %s)",
                    label, phone, settings.DEV_OTP)
        return True
    try:
        response = httpx.post(
            settings.WHATSAPP_API_URL,
            json=payload,
            headers={
                "X-API-KEY": settings.WHATSAPP_API_KEY,
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )
        logger.info("WhatsApp %s response [%s]: %s", label, response.status_code, response.text)
        if response.status_code in (200, 201):
            return True
        logger.warning("WhatsApp %s failed [%s]: %s", label, response.status_code, response.text)
        return False
    except Exception as e:
        logger.error("WhatsApp %s error: %s", label, str(e))
        return False


def send_otp(mobile_number: str, otp: str) -> bool:
    """Send the OTP to the user via WhatsApp (template with code body + copy-code button)."""
    phone = _format_phone(mobile_number)
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": settings.WHATSAPP_OTP_TEMPLATE,
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": otp}],
                },
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": 0,
                    "parameters": [{"type": "text", "text": otp}],
                },
            ],
        },
    }
    return _send_template(phone, payload, "OTP")


def send_thank_you(mobile_number: str) -> bool:
    """Send the confirmation/thank-you message once the request is accepted."""
    phone = _format_phone(mobile_number)
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": settings.WHATSAPP_THANKYOU_TEMPLATE,
            "language": {"code": "en"},
        },
    }
    return _send_template(phone, payload, "thank_you")


def send_failed_message(mobile_number: str) -> bool:
    """Notify the user when video generation has failed."""
    phone = _format_phone(mobile_number)
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": settings.WHATSAPP_FAILED_TEMPLATE,
            "language": {"code": "en"},
        },
    }
    return _send_template(phone, payload, "failed")


def send_video(mobile_number: str, video_url: str) -> bool:
    """Deliver the finished video to the user via a WhatsApp video-header template."""
    phone = _format_phone(mobile_number)
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": settings.WHATSAPP_VIDEO_TEMPLATE,
            "language": {"code": "en"},
            "components": [
                {
                    "type": "header",
                    "parameters": [
                        {"type": "video", "video": {"link": video_url}}
                    ],
                }
            ],
        },
    }
    return _send_template(phone, payload, "video")

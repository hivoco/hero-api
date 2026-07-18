"""Verify the active vision model can actually see an image, and re-enable photo
validation if it can.

Photo validation auto-disables itself when the vision model errors (e.g. a Groq
"model not found / no access" 404). After you fix access — enable Llama 4 vision
on the Groq account, accept model terms / add billing, or drop in a Groq key
that has it — run this to confirm it works and turn validation back on.

    # uses the key(s) in .env
    .venv/bin/python scripts/check_vision.py

    # test a specific key without editing .env first
    GROQ_TEST_KEY=gsk_xxx .venv/bin/python scripts/check_vision.py
"""
import base64
import os
import sys

# Make `app` importable when run directly from the backend dir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _test_jpeg() -> bytes:
    """A small but real JPEG — some vision models reject a 1x1 pixel as
    'invalid image data', so build a proper little image."""
    import io
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (160, 160), (210, 210, 210))
    d = ImageDraw.Draw(im)
    d.ellipse([40, 30, 120, 110], fill=(120, 90, 70))   # a vaguely face-ish blob
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


def main() -> int:
    from app.core.database import SessionLocal
    from app.services.vision_service import get_active_vision, ensure_default_vision
    from app.routers.photo_validation import analyze_photo, to_data_url
    from app.core.redis import FeatureFlags

    db = SessionLocal()
    try:
        vc = get_active_vision(db) or ensure_default_vision(db)
        print(f"Active vision config → provider={vc.provider!r} model={vc.model_name!r}")

        override = os.getenv("GROQ_TEST_KEY")
        if override and vc.provider == "groq":
            # analyze_photo pulls keys from settings; temporarily force this one.
            from app.core import config as cfg
            cfg.settings.GROQ_API_KEYS = override
            print("(testing with GROQ_TEST_KEY from the environment)")

        data_url = to_data_url(_test_jpeg(), "image/jpeg")
        result = analyze_photo(data_url, vc.provider, vc.model_name, vc.prompt)

        if result is None:
            print("\n❌ Vision call FAILED — the model still can't be reached.")
            print("   Fix access first (see the notes), then re-run this.")
            return 1

        print("\n✅ Vision call SUCCEEDED — the model can see images.")
        FeatureFlags.set_flag("photo_validation", True)
        print("✅ Re-enabled the photo_validation feature flag.")
        print("   New selfies will be validated again.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

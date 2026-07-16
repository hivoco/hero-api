"""Photo Validation Queue Worker.

Processes queued photo-validation requests (structured Groq analysis) when
capacity frees up. Run as a separate process:
    python -m app.workers.photo_queue_worker
"""

import asyncio
import base64
from starlette.concurrency import run_in_threadpool

from app.core.redis import GroqKeyManager, PhotoValidationQueue
from app.core.config import settings
from app.core.database import SessionLocal
from app.routers.photo_validation import analyze_photo, build_result
from app.services.vision_service import get_active_vision, ensure_default_vision


def _active_vision():
    db = SessionLocal()
    try:
        vc = get_active_vision(db) or ensure_default_vision(db)
        return vc.provider, vc.model_name, vc.prompt
    finally:
        db.close()


async def process_single_item(item: dict) -> bool:
    validation_id = item["validation_id"]
    data_url = item["image_data"]
    print(f"🔄 Processing validation {validation_id}")
    PhotoValidationQueue.set_status(validation_id, "processing")

    if GroqKeyManager.get_total_remaining() == 0:
        print(f"⏸️ No capacity, re-queuing {validation_id}")
        PhotoValidationQueue.set_status(validation_id, "queued", position=1)
        return False

    try:
        provider, model_name, prompt = _active_vision()
        analysis = await run_in_threadpool(analyze_photo, data_url, provider, model_name, prompt)
        if analysis is None:
            PhotoValidationQueue.set_result(validation_id, {
                "valid": False, "reason": "Validation service error",
                "message": "Image validation failed. Please try again.", "label": None,
            })
            return True
        raw = base64.b64decode(data_url.split(",", 1)[1])
        PhotoValidationQueue.set_result(validation_id, build_result(raw, analysis))
        print(f"✅ Completed {validation_id}: valid={build_result(raw, analysis)['valid']}")
        return True
    except Exception as e:
        print(f"❌ Error processing {validation_id}: {e}")
        PhotoValidationQueue.set_result(validation_id, {
            "valid": False, "reason": "Validation error",
            "message": "Image validation failed. Please try again.", "label": None,
        })
        return True


async def worker_loop():
    print("🚀 Photo validation worker started")
    print(f"📊 Total API keys: {len(settings.groq_api_keys_list)}")
    while True:
        try:
            if GroqKeyManager.get_total_remaining() == 0:
                retry_after = GroqKeyManager.get_retry_after()
                print(f"⏳ No capacity, waiting {retry_after}s...")
                await asyncio.sleep(retry_after)
                continue
            item = PhotoValidationQueue.dequeue()
            if not item:
                await asyncio.sleep(1)
                continue
            await process_single_item(item)
            await asyncio.sleep(0.1)
        except KeyboardInterrupt:
            print("\n🛑 Worker stopped")
            break
        except Exception as e:
            print(f"❌ Worker error: {e}")
            await asyncio.sleep(5)


def run_worker():
    asyncio.run(worker_loop())


if __name__ == "__main__":
    run_worker()

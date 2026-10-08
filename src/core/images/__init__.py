"""IMAGES: ИИ-картинки колоды — SiliconFlow, модель Z-Image-Turbo (ТЗ 3.6, D-067).

Промпт = смысл слайда (image_query плана) + внешность людей по языку колоды
(prompts/v2/images/appearance.yaml) + стиль темы и общие правила (style.yaml): хвост
одинаков у всех картинок колоды. Картинки генерируются параллельно с CONTENT, у каждой
свой таймаут; не успела или ошибка — слайд получает вариант без картинки, колода не ждёт.
Файл обрезается под пропорции слота (cover-crop), сжимается (JPEG) и встраивается в PPTX.
Стоимость — этап «images» в DeckUsage (decks.cost_rub, расход клиента API).

Pexels не используется (D-006 обновлено в сессии 4).
"""

import asyncio
import base64
import hashlib
import io
import logging
import time
from dataclasses import dataclass
from functools import lru_cache

import httpx
from PIL import Image

from config import settings
from core.llm import prompts as P

logger = logging.getLogger(__name__)

PROVIDER = "siliconflow"


@dataclass
class ImageResult:
    slide_id: str
    asset_id: str | None = None
    data: bytes | None = None
    prompt: str = ""
    error: str | None = None
    ms: int = 0
    cost_rub: float = 0.0


@lru_cache(maxsize=1)
def config() -> dict:
    return P.data("images/config.yaml")


def model_id() -> str:
    return settings.siliconflow_model or config()["model"]


def enabled() -> bool:
    return bool(settings.siliconflow_api_key)


def per_deck() -> int:
    return int(settings.images_per_deck or config()["per_deck"])


def build_prompt(query: str, theme_id: str, language: str) -> str:
    style = P.data("images/style.yaml")
    appearance = P.data("images/appearance.yaml")
    subject = " ".join(query.split()).rstrip(" .")
    people = appearance.get(language) or appearance["en"]
    theme = style["themes"].get(theme_id, style["default"])
    return f"{subject}. {people}. Style: {theme}. {style['rules']}"


def cover_crop(data: bytes, aspect: float) -> bytes:
    """Обрезка по центру под пропорции слота (ширина / высота), длинная сторона ≤ max_side, JPEG."""
    cfg = config()
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    if w / h > aspect:
        nw = int(round(h * aspect))
        img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = int(round(w / aspect))
        img = img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    img.thumbnail((int(cfg["max_side"]), int(cfg["max_side"])))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=int(cfg["jpeg_quality"]), optimize=True)
    return out.getvalue()


def asset_id(prompt: str, seed: int) -> str:
    return "img_" + hashlib.sha1(f"{prompt}|{seed}".encode()).hexdigest()[:8]


def _image_ref(payload: dict) -> tuple[str | None, str | None]:
    """→ (url, base64) из ответа SiliconFlow: {"images": [{"url"}]} или в духе OpenAI {"data": [...]}."""
    for key in ("images", "data"):
        items = payload.get(key) or []
        if items and isinstance(items[0], dict):
            return items[0].get("url"), items[0].get("b64_json")
    return None, None


class SiliconFlowClient:
    """POST {base}/images/generations → URL картинки (живёт час) → скачивание."""

    def __init__(self, http: httpx.AsyncClient | None = None):
        self._http = http

    async def generate(self, prompt: str, seed: int, timeout: float) -> bytes:
        body = {"model": model_id(), "prompt": prompt, "image_size": config()["image_size"], "batch_size": 1,
                "seed": seed % 9_999_999_999}
        headers = {"Authorization": f"Bearer {settings.siliconflow_api_key}"}
        own = self._http is None
        http = self._http or httpx.AsyncClient(timeout=timeout)
        try:
            r = await http.post(settings.siliconflow_base_url.rstrip("/") + "/images/generations", json=body,
                                headers=headers, timeout=timeout)
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            url, b64 = _image_ref(r.json())
            if b64:
                return base64.b64decode(b64)
            if not url:
                raise RuntimeError("no image in response")
            img = await http.get(url, timeout=timeout)
            img.raise_for_status()
            return img.content
        finally:
            if own:
                await http.aclose()


_client: SiliconFlowClient | None = None


def get_client() -> SiliconFlowClient:
    global _client
    if _client is None:
        _client = SiliconFlowClient()
    return _client


@dataclass
class ImageJob:
    slide_id: str
    query: str
    aspect: float


async def generate_one(job: ImageJob, theme_id: str, language: str, seed: int,
                       client: SiliconFlowClient | None = None) -> ImageResult:
    prompt = build_prompt(job.query, theme_id, language)
    timeout = float(config()["timeout_seconds"])
    started = time.monotonic()
    result = ImageResult(slide_id=job.slide_id, prompt=prompt)
    try:
        raw = await asyncio.wait_for((client or get_client()).generate(prompt, seed, timeout), timeout=timeout)
        result.data = await asyncio.to_thread(cover_crop, raw, job.aspect)
        result.asset_id = asset_id(prompt, seed)
        result.cost_rub = float(config()["price_rub"])
    except asyncio.TimeoutError:
        result.error = "timeout"
    except Exception as e:  # noqa: BLE001 — любая ошибка провайдера: слайд без картинки
        result.error = str(e)[:200] or e.__class__.__name__
    result.ms = int((time.monotonic() - started) * 1000)
    if result.error:
        logger.warning("Image failed — slide without image",
                       extra={"slide_id": job.slide_id, "error": result.error, "duration_ms": result.ms})
    else:
        logger.info("Image generated", extra={"slide_id": job.slide_id, "duration_ms": result.ms})
    return result


def start(jobs: list[ImageJob], theme_id: str, language: str, seed: int,
          client: SiliconFlowClient | None = None) -> dict[str, asyncio.Task]:
    """Запускает генерацию картинок колоды (не больше concurrency одновременно) → задачи по slide_id."""
    semaphore = asyncio.Semaphore(max(1, int(config()["concurrency"])))

    async def limited(job: ImageJob, i: int) -> ImageResult:
        async with semaphore:
            return await generate_one(job, theme_id, language, seed + i, client)

    return {job.slide_id: asyncio.create_task(limited(job, i)) for i, job in enumerate(jobs)}

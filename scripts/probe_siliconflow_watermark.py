"""Зонд маркировки «AI 生成» на картинках SiliconFlow (сессия 6, D-079).

SiliconFlow ставит на картинки Z-Image-Turbo надпись «AI 生成» (по правилам маркировки ИИ-контента).
Скрипт генерирует картинки нескольких размеров и сюжетов (светлый, тёмный, пёстрый фон) и печатает
в лог: принят ли размер, фактический размер файла, и base64 кадров — всей картинки (уменьшенной)
и четырёх углов в полном разрешении. По ним измеряется полоса, которую срезает
core.images.strip_marking (правило — prompts/v2/images/config.yaml, marking).

    SILICONFLOW_API_KEY=… python scripts/probe_siliconflow_watermark.py [--sizes 1024x1024,1024x1120] [--out DIR]
    SILICONFLOW_API_KEY=… python scripts/probe_siliconflow_watermark.py --before-after [--out DIR]

--before-after: 5 картинок продового размера (image_size из config.yaml) разных сюжетов, каждая проходит
core.images.cover_crop под пропорции одного из слотов layouts/ — в лог уходят «до» (как пришла от SiliconFlow),
«после» (как встанет в слайд) и правый нижний угол обеих в полном разрешении. Для PR и docs/design/review/.

Запускается в CI (workflow «siliconflow-probe», ключ — секрет репозитория); локально — если есть сеть до
api.siliconflow.com. Каждая картинка ≈ $0.005.
"""

import argparse
import asyncio
import base64
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402
from PIL import Image  # noqa: E402

PROMPTS = [
    "bright minimalist office with white walls and a laptop on a light wooden desk, soft daylight",
    "night city skyline with dark blue sky and warm windows, cinematic",
    "busy farmers market with colorful fruit stalls and people, natural light",
]
SIZES = ["1024x1024", "1024x1120", "1024x1152", "960x1280", "768x1024", "1280x1440", "1328x1328"]
CORNER = (0.30, 0.16)        # доля ширины и высоты угла, который печатается в полном разрешении
LINE = 8000
# Сюжеты «до/после»: светлый, тёмный, пёстрый, с текстурой в нижнем углу и с логотипом на технике
BEFORE_AFTER = [
    "bright minimalist office with white walls and a laptop on a light wooden desk, soft daylight",
    "night city skyline with dark blue sky and warm windows, cinematic",
    "busy farmers market with colorful fruit stalls and people, natural light",
    "surgeons in an operating room looking at an endoscopy monitor, clinical blue light",
    "close-up of a coffee cup on a dark slate table next to a smartphone with a brand logo",
]
# Слоты картинок в layouts/ (ширина / высота): cover_split_image, text_image_split, image_numbered
SLOTS = {"cover_split_image": 864 / 936, "text_image_split": 856 / 936, "image_numbered": 800 / 936}


def emit(tag: str, img: Image.Image, fmt: str = "PNG") -> None:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **({"quality": 90} if fmt == "JPEG" else {}))
    b64 = base64.b64encode(buf.getvalue()).decode()
    print(f"@@BEGIN {tag} {fmt} {img.size[0]}x{img.size[1]} {len(b64)}")
    for i in range(0, len(b64), LINE):
        print("@@" + b64[i:i + LINE])
    print(f"@@END {tag}", flush=True)


def corners(img: Image.Image) -> dict[str, Image.Image]:
    w, h = img.size
    cw, ch = int(w * CORNER[0]), int(h * CORNER[1])
    return {"br": img.crop((w - cw, h - ch, w, h)), "bl": img.crop((0, h - ch, cw, h)),
            "tr": img.crop((w - cw, 0, w, ch)), "tl": img.crop((0, 0, cw, ch))}


async def one(http: httpx.AsyncClient, key: str, base: str, model: str, size: str, prompt: str, seed: int,
              out: Path | None) -> dict:
    body = {"model": model, "prompt": prompt, "image_size": size, "batch_size": 1, "seed": seed}
    r = await http.post(base.rstrip("/") + "/images/generations", json=body,
                        headers={"Authorization": f"Bearer {key}"}, timeout=60)
    info = {"size": size, "seed": seed, "prompt": prompt[:40], "status": r.status_code}
    if r.status_code >= 400:
        info["error"] = r.text[:300]
        return info
    payload = r.json()
    items = payload.get("images") or payload.get("data") or []
    url = items[0].get("url") if items else None
    raw = (await http.get(url, timeout=60)).content if url else base64.b64decode(items[0]["b64_json"])
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    info.update(actual=f"{img.size[0]}x{img.size[1]}", bytes=len(raw))
    tag = f"{size}_s{seed}"
    if out:
        out.mkdir(parents=True, exist_ok=True)
        img.save(out / f"{tag}.png")
    small = img.copy()
    small.thumbnail((512, 512))
    emit(f"{tag}_full", small, "JPEG")
    for name, crop in corners(img).items():
        emit(f"{tag}_{name}", crop, "PNG")
    return info


async def fetch(http: httpx.AsyncClient, key: str, base: str, model: str, size: str, prompt: str,
                seed: int) -> bytes:
    body = {"model": model, "prompt": prompt, "image_size": size, "batch_size": 1, "seed": seed}
    r = await http.post(base.rstrip("/") + "/images/generations", json=body,
                        headers={"Authorization": f"Bearer {key}"}, timeout=60)
    r.raise_for_status()
    items = (r.json().get("images") or r.json().get("data") or [])
    url = items[0].get("url") if items else None
    return (await http.get(url, timeout=60)).content if url else base64.b64decode(items[0]["b64_json"])


async def before_after(out: Path | None) -> int:
    from config import settings
    from core import images as IMG
    key = settings.siliconflow_api_key
    if not key:
        print("SILICONFLOW_API_KEY не задан")
        return 2
    model = settings.siliconflow_model or "Tongyi-MAI/Z-Image-Turbo"
    size = IMG.config()["image_size"]
    slots = list(SLOTS.items())
    results = []
    async with httpx.AsyncClient() as http:
        for i, prompt in enumerate(BEFORE_AFTER, 1):
            slot, aspect = slots[(i - 1) % len(slots)]
            info = {"n": i, "size": size, "slot": slot, "prompt": prompt[:50]}
            try:
                raw = await fetch(http, key, settings.siliconflow_base_url, model, size, prompt, 2000 + i)
                before = Image.open(io.BytesIO(raw)).convert("RGB")
                after = Image.open(io.BytesIO(IMG.cover_crop(raw, aspect))).convert("RGB")
                info.update(before=f"{before.size[0]}x{before.size[1]}", after=f"{after.size[0]}x{after.size[1]}",
                            after_aspect=round(after.size[0] / after.size[1], 3), slot_aspect=round(aspect, 3))
                if out:
                    out.mkdir(parents=True, exist_ok=True)
                    before.save(out / f"{i}_before.png")
                    after.save(out / f"{i}_after.jpg", quality=92)
                for name, img in (("before", before), ("after", after)):
                    small = img.copy()
                    small.thumbnail((640, 640))
                    emit(f"{i}_{name}", small, "JPEG")
                    emit(f"{i}_{name}_br", corners(img)["br"], "PNG")
            except Exception as e:  # noqa: BLE001
                info["error"] = f"{type(e).__name__}: {e}"[:300]
            results.append(info)
            print("@@RESULT " + json.dumps(info, ensure_ascii=False), flush=True)
    print("@@SUMMARY " + json.dumps(results, ensure_ascii=False))
    return 0


async def main(sizes: list[str], out: Path | None) -> int:
    from config import settings
    key = settings.siliconflow_api_key
    if not key:
        print("SILICONFLOW_API_KEY не задан")
        return 2
    model = settings.siliconflow_model or "Tongyi-MAI/Z-Image-Turbo"
    results = []
    async with httpx.AsyncClient() as http:
        for i, size in enumerate(sizes):
            prompt = PROMPTS[i % len(PROMPTS)]
            try:
                results.append(await one(http, key, settings.siliconflow_base_url, model, size, prompt, 1000 + i, out))
            except Exception as e:  # noqa: BLE001
                results.append({"size": size, "error": f"{type(e).__name__}: {e}"[:300]})
            print("@@RESULT " + json.dumps(results[-1], ensure_ascii=False), flush=True)
    print("@@SUMMARY " + json.dumps(results, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default=",".join(SIZES))
    ap.add_argument("--out", type=Path)
    ap.add_argument("--before-after", action="store_true")
    args = ap.parse_args()
    if args.before_after:
        sys.exit(asyncio.run(before_after(args.out)))
    sys.exit(asyncio.run(main(args.sizes.split(","), args.out)))

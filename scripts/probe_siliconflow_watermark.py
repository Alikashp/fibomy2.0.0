"""Зонд маркировки «AI 生成» на картинках SiliconFlow (сессия 6, D-079).

SiliconFlow ставит на картинки Z-Image-Turbo надпись «AI 生成» (по правилам маркировки ИИ-контента).
Скрипт генерирует картинки нескольких размеров и сюжетов (светлый, тёмный, пёстрый фон) и печатает
в лог: принят ли размер, фактический размер файла, и base64 кадров — всей картинки (уменьшенной)
и четырёх углов в полном разрешении. По ним измеряется полоса, которую срезает
core.images.strip_marking (правило — prompts/v2/images/config.yaml, marking).

    SILICONFLOW_API_KEY=… python scripts/probe_siliconflow_watermark.py [--sizes 1024x1024,1024x1120] [--out DIR]

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
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.sizes.split(","), args.out)))

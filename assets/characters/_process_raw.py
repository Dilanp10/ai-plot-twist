"""
Convierte las fotos crudas en assets/characters/raw/ a 512x512 WebP <=80KB.
Ejecutar desde la raíz del repo:
    python assets/characters/_process_raw.py
"""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

SLUGS = [
    "messi",
    "bad-bunny",
    "merlina",
    "cr7",
    "john-wick",
    "franchella",
    "ibai",
    "putin",
    "dua-lipa",
    "angelina-jolie",
]

RAW_DIR = Path(__file__).parent / "raw"
OUT_DIR = Path(__file__).parent
TARGET = (512, 512)
MAX_KB = 80


def center_crop_square(img: Image.Image) -> Image.Image:
    w, h = img.size
    s = min(w, h)
    left = (w - s) // 2
    top = max(0, (h - s) // 3)   # bias slightly upward to favor face over torso
    top = min(top, h - s)
    return img.crop((left, top, left + s, top + s))


def process(slug: str) -> None:
    # Accept .jpg / .jpeg / .png / .webp
    for ext in ("jpg", "jpeg", "png", "webp"):
        src = RAW_DIR / f"{slug}.{ext}"
        if src.exists():
            break
    else:
        print(f"  SKIP  {slug}  — archivo no encontrado en raw/")
        return

    out = OUT_DIR / f"{slug}.webp"
    with Image.open(src) as img:
        img = img.convert("RGB")
        img = center_crop_square(img)
        img = img.resize(TARGET, Image.LANCZOS)

        quality = 88
        while quality >= 55:
            img.save(out, "WEBP", quality=quality, method=6)
            kb = out.stat().st_size / 1024
            if kb <= MAX_KB:
                break
            quality -= 5

    kb = out.stat().st_size / 1024
    print(f"  OK    {slug}.webp  ({kb:.1f} KB)  q={quality}")


def main() -> None:
    print(f"Procesando {len(SLUGS)} personajes...\n")
    for slug in SLUGS:
        process(slug)
    print("\nListo. Archivos en assets/characters/")


if __name__ == "__main__":
    main()

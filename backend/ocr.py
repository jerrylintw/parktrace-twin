"""Optical character recognition from a single browser snapshot.

Only decoded pixels are held in memory; no uploaded photo is stored.
For accuracy, let the user crop the plate inside the browser first.
"""
import base64
import io
import re
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 3_000_000


def recognize(data_url):
    if not isinstance(data_url, str) or ',' not in data_url:
        raise ValueError('請使用攝影機拍照或選擇圖片')
    prefix, payload = data_url.split(',', 1)
    if not prefix.startswith('data:image/') or len(payload) > (MAX_IMAGE_BYTES * 4 // 3 + 200):
        raise ValueError('僅支援 3MB 以下的圖片')
    try:
        raw = base64.b64decode(payload, validate=True)
        if len(raw) > MAX_IMAGE_BYTES:
            raise ValueError('圖片大小超過 3MB')
        img = Image.open(io.BytesIO(raw))
        img.load()
        if img.width * img.height > 8_000_000:
            raise ValueError('圖片像素過大')
        img = img.convert('RGB')
    except (ValueError, UnidentifiedImageError, OSError) as exc:
        raise ValueError('圖片無法讀取或格式錯誤') from exc
    try:
        import pytesseract
        from pytesseract import TesseractNotFoundError
    except ImportError as exc:
        raise RuntimeError('OCR 尚未安裝，請先安裝 requirements.txt') from exc

    img.thumbnail((1600, 1200))
    mono = ImageOps.autocontrast(ImageOps.grayscale(img))
    sharp = ImageEnhance.Contrast(mono.filter(ImageFilter.SHARPEN)).enhance(2)
    variants = [mono, sharp, sharp.point(lambda p: 255 if p > 140 else 0)]
    outputs = []
    config = '--oem 3 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-'
    try:
        for variant in variants:
            outputs.append(pytesseract.image_to_string(variant, lang='eng', config=config).strip())
    except TesseractNotFoundError as exc:
        raise RuntimeError('找不到 Tesseract 執行檔，請查看 README 的安裝步驟') from exc
    candidates = []
    for line in outputs:
        normalized = re.sub(r'[^A-Z0-9-]', '', line.upper())
        # Unhyphenated AXX1234 is an often encountered OCR output.
        match = re.search(r'([A-Z0-9]{2,4})-?([A-Z0-9]{3,4})', normalized)
        if match:
            plate = f'{match.group(1)}-{match.group(2)}'
            if plate not in candidates:
                candidates.append(plate)
    return {'candidates': candidates[:5], 'raw': outputs[:3],
            'warning': 'OCR 是輔助辨識，請人工核對後再按進場。台灣車牌拍攝請靠近並裁切車牌。'}

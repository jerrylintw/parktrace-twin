"""Camera-photo plate OCR with conservative candidate extraction.

The browser captures/crops the frame locally and sends only one image for OCR.
OCR never changes a parking visit without the visitor's explicit confirmation.
"""
import base64
import io
import re
from collections import Counter
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 3_000_000
MAX_PIXELS = 8_000_000

# Expected common plate patterns. Explicit alphanumeric-with-dash format
# remains a lower-priority fallback for legacy/simulation examples.
_STANDARD = re.compile(r'(?<![A-Z0-9])([A-Z]{2,3})\s*[-]?\s*([0-9OILSBZ]{3,4})(?![A-Z0-9])')
_REVERSED = re.compile(r'(?<![A-Z0-9])([0-9]{3,4})\s*[-]?\s*([A-Z]{2,3})(?![A-Z0-9])')
_LEGACY = re.compile(r'(?<![A-Z0-9])([A-Z0-9]{2,4})\s*-\s*([A-Z0-9]{3,4})(?![A-Z0-9])')
_DIGIT_MISREAD = str.maketrans({'O':'0', 'I':'1', 'L':'1', 'S':'5', 'B':'8', 'Z':'2'})


def candidate_plates(ocr_text):
    """Return plate suggestions, favoring letters+digits before fallback formats.

    OCR text can be unhyphenated (e.g. ABC1234) or contain lookalike digits.
    Pattern boundaries prevent silently taking part of a long random string.
    """
    text = str(ocr_text or '').upper()
    text = text.replace('—', '-').replace('–', '-').replace('－', '-').replace('_', '-')
    text = re.sub(r'[^A-Z0-9\s-]', ' ', text)
    matches = []
    for pattern in (_STANDARD, _REVERSED, _LEGACY):
        for match in pattern.finditer(text):
            first, second = match.groups()
            if pattern is _STANDARD:
                second = second.translate(_DIGIT_MISREAD)
            plate = first + '-' + second
            if plate not in matches:
                matches.append(plate)
        if matches:
            return matches
    return []


def _variants(img):
    gray = ImageOps.autocontrast(ImageOps.grayscale(img))
    if gray.width < 950:
        factor = min(3, 950 / gray.width)
        gray = gray.resize((round(gray.width * factor), round(gray.height * factor)), Image.Resampling.LANCZOS)
    gray = ImageOps.expand(gray, border=16, fill=255)
    sharp = ImageEnhance.Contrast(gray.filter(
        ImageFilter.UnsharpMask(radius=1.3, percent=170, threshold=2))).enhance(1.6)
    binary = sharp.point(lambda pixel: 255 if pixel > 145 else 0)
    return gray, sharp, binary


def _load_image(data_url):
    if not isinstance(data_url, str) or ',' not in data_url:
        raise ValueError('請使用筆電攝影機拍照或上傳圖片')
    prefix, payload = data_url.split(',', 1)
    if not re.fullmatch(r'data:image/(?:png|jpeg|webp);base64', prefix, re.I):
        raise ValueError('僅支援 JPG、PNG 或 WebP 圖片')
    if len(payload) > MAX_IMAGE_BYTES * 4 // 3 + 200:
        raise ValueError('圖片大小超過 3MB')
    try:
        raw = base64.b64decode(payload, validate=True)
        if len(raw) > MAX_IMAGE_BYTES:
            raise ValueError('圖片大小超過 3MB')
        with Image.open(io.BytesIO(raw)) as source:
            if source.width * source.height > MAX_PIXELS:
                raise ValueError('圖片像素過大（上限 800 萬像素）')
            source.load()
            return source.convert('RGB')
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError('圖片無法讀取、格式錯誤或大小超過限制') from exc


def recognize(data_url):
    img = _load_image(data_url)
    try:
        import pytesseract
        from pytesseract import TesseractNotFoundError, TesseractError
    except ImportError as exc:
        raise RuntimeError('OCR 套件未安裝，請先安裝 requirements.txt') from exc

    # Four fast, complementary passes; an extra sparse-text pass for full photos.
    img.thumbnail((1600, 1200))
    gray, sharp, binary = _variants(img)
    passes = [(gray, 7), (sharp, 7), (binary, 7), (gray, 11)]
    text_outputs = []
    vote = Counter()
    base_config = '-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-'
    try:
        for variant, psm in passes:
            output = pytesseract.image_to_string(
                variant, lang='eng', config=f'--oem 3 --psm {psm} {base_config}').strip()
            text_outputs.append(output[:120])
            for plate in candidate_plates(output):
                vote[plate] += 1
        # If none matched, try a different text-line segmentation only once.
        if not vote:
            output = pytesseract.image_to_string(sharp, lang='eng', config=f'--oem 3 --psm 6 {base_config}').strip()
            text_outputs.append(output[:120])
            for plate in candidate_plates(output):
                vote[plate] += 1
    except TesseractNotFoundError as exc:
        raise RuntimeError('伺服器找不到 Tesseract OCR，請查看 README 安裝指南') from exc
    except TesseractError as exc:
        raise RuntimeError('OCR 引擎無法處理照片，請更換清晰的車牌照片') from exc

    ordered = [plate for plate, _ in vote.most_common(5)]
    return {
        'candidates': ordered,
        'votes': {plate: vote[plate] for plate in ordered},
        'raw': text_outputs,
        'warning': 'OCR 候選車牌不代表已確認。請核對照片並手動送出進場或出場。',
    }

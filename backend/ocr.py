"""Camera-assisted OCR: detect/rectify a plate, then read letters and digits.

A conventional OCR engine is fallible, especially on stylized, reflective or
screen-photographed plates. Results are *suggestions*: never register a visit
without the user's explicit visual confirmation.
"""
import base64
import io
import re
from collections import Counter

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 3_000_000
MAX_PIXELS = 8_000_000

_STANDARD = re.compile(r'(?<![A-Z0-9])([A-Z]{2,3})\s*[-]?\s*([0-9OILSBZ]{3,4})(?![A-Z0-9])')
_REVERSED = re.compile(r'(?<![A-Z0-9])([0-9]{3,4})\s*[-]?\s*([A-Z]{2,3})(?![A-Z0-9])')
_LEGACY = re.compile(r'(?<![A-Z0-9])([A-Z0-9]{2,4})\s*-\s*([A-Z0-9]{3,4})(?![A-Z0-9])')
_DIGIT_MISREAD = str.maketrans({'O': '0', 'I': '1', 'L': '1', 'S': '5', 'B': '8', 'Z': '2'})


def candidate_plates(ocr_text):
    """Extract complete plate-looking tokens without guessing unreadable letters."""
    text = str(ocr_text or '').upper()
    text = text.replace('—', '-').replace('–', '-').replace('－', '-').replace('_', '-')
    text = re.sub(r'[^A-Z0-9\s-]', ' ', text)
    for pattern in (_STANDARD, _REVERSED, _LEGACY):
        matches = []
        for match in pattern.finditer(text):
            left, right = match.groups()
            if pattern is _STANDARD:
                right = right.translate(_DIGIT_MISREAD)
            plate = left + '-' + right
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
            return ImageOps.exif_transpose(source).convert('RGB')
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError('圖片無法讀取、格式錯誤或大小超過限制') from exc


def _rectify_plate(image):
    """Locate a light, low-saturation rectangular plate and perspective-correct.

    Purely geometric/color based (no ML download or external service). Return
    None for uncertain images so the regular OCR route remains available.
    """
    import cv2
    import numpy as np

    arr = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    if arr.shape[1] > 1400:
        scale = 1400 / arr.shape[1]
        arr = cv2.resize(arr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    height, width = arr.shape[:2]
    hsv = cv2.cvtColor(arr, cv2.COLOR_BGR2HSV)
    # Bright, mostly neutral plates against the darker/saturated vehicle.
    neutral = cv2.inRange(hsv, (0, 0, 115), (180, 80, 255))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (
        max(7, round(width * .025) | 1), max(5, round(height * .025) | 1)))
    neutral = cv2.morphologyEx(neutral, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(neutral, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    ranked = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if not (.015 * width * height <= area <= .84 * width * height):
            continue
        rect = cv2.minAreaRect(contour)
        w, h = rect[1]
        if min(w, h) < 38 or not 1.65 <= max(w, h) / min(w, h) <= 5.5:
            continue
        hull = cv2.convexHull(contour)
        perimeter = cv2.arcLength(hull, True)
        corners = None
        for fraction in (.025, .035, .045, .055):
            polygon = cv2.approxPolyDP(hull, fraction * perimeter, True)
            if len(polygon) == 4 and cv2.isContourConvex(polygon):
                corners = polygon.reshape(4, 2).astype('float32')
                break
        if corners is None:
            continue
        ranked.append((area, corners))
    if not ranked:
        return None

    corners = max(ranked, key=lambda item: item[0])[1]
    # Order TL, TR, BR, BL; expand gently so left/right characters aren't cut.
    total = corners.sum(axis=1)
    difference = corners[:, 1] - corners[:, 0]
    ordered = np.array((corners[np.argmin(total)], corners[np.argmin(difference)],
                        corners[np.argmax(total)], corners[np.argmax(difference)]), dtype='float32')
    center = ordered.mean(axis=0)
    ordered = center + (ordered - center) * 1.025
    target = np.array(((0, 0), (1000, 0), (1000, 420), (0, 420)), dtype='float32')
    return cv2.warpPerspective(arr, cv2.getPerspectiveTransform(ordered, target),
                               (1000, 420), borderMode=cv2.BORDER_CONSTANT,
                               borderValue=(240, 240, 240))


def _segmented_read(rectified, ocr_fn):
    """Constrain letter and digit recognition separately; no hardcoded plates.

    This is important with reflective/outlined digits where whole-line OCR may
    read an '8' as 'E' or '3'. Only accept the complete 2-3 / 4 pattern.
    """
    import cv2

    region = rectified[70:370, :]
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    high_contrast = cv2.adaptiveThreshold(gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 10)

    def read(part, whitelist, psm):
        padded = cv2.copyMakeBorder(part, 16, 16, 16, 16,
                                   cv2.BORDER_CONSTANT, value=255)
        return ocr_fn(padded, psm, whitelist).replace(' ', '').replace('-', '').strip().upper()

    plates = []
    # Camera distance and perspective change the exact location of the dash.
    # Read the numeric run at three overlapping starts and require a majority,
    # rather than trusting a single Tesseract read of an outlined 8.
    for letters, prefix_end, digit_starts in (
            (3, 405, (430, 405, 355)),
            (2, 340, (375, 345, 325))):
        prefix = read(high_contrast[:, :prefix_end], 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 7)
        if not re.fullmatch(r'[A-Z]{%d}' % letters, prefix):
            continue
        digits_vote = Counter()
        for start in digit_starts:
            digits = read(high_contrast[:, start:], '0123456789', 7)
            if re.fullmatch(r'[0-9]{4}', digits):
                digits_vote[digits] += 1
        if digits_vote:
            best_digits, count = digits_vote.most_common(1)[0]
            if count >= 1:  # low-agreement suggestions remain explicitly unconfirmed
                plates.append((prefix + '-' + best_digits, 3 if count >= 2 else 2))
                break
    return plates


def recognize(data_url):
    img = _load_image(data_url)
    try:
        import pytesseract
        from pytesseract import TesseractNotFoundError, TesseractError
        import cv2
    except ImportError as exc:
        raise RuntimeError('OCR 套件未安裝，請先安裝 requirements.txt') from exc

    img.thumbnail((1600, 1200))
    vote = Counter()
    raw = []
    # A common runner for whole-line and segmented OCR. Restricting character
    # classes improves recognition on noisy/screen-photographed vehicle plates.
    def run(image, psm, whitelist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-'):
        output = pytesseract.image_to_string(image, lang='eng',
            config=f'--oem 3 --psm {psm} -c tessedit_char_whitelist={whitelist}').strip()
        raw.append(output[:100])
        return output

    try:
        plate_image = _rectify_plate(img)
        if plate_image is not None:
            gray = cv2.cvtColor(plate_image, cv2.COLOR_BGR2GRAY)
            adaptive = cv2.adaptiveThreshold(gray, 255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 10)
            for image in (adaptive, cv2.createCLAHE(clipLimit=2, tileGridSize=(8, 8)).apply(gray)):
                text = run(image, 7)
                for plate in candidate_plates(text):
                    vote[plate] += 1
            for plate, weight in _segmented_read(plate_image, run):
                vote[plate] += weight  # independently constrained fields
        # Original image provides a fallback when geometry is unreliable.
        gray, sharp, binary = _variants(img)
        for variant, psm in ((gray, 7), (sharp, 7), (binary, 7), (gray, 11)):
            for plate in candidate_plates(run(variant, psm)):
                vote[plate] += 1
        if not vote:
            for plate in candidate_plates(run(sharp, 6)):
                vote[plate] += 1
    except TesseractNotFoundError as exc:
        raise RuntimeError('伺服器找不到 Tesseract OCR，請查看 README 安裝指南') from exc
    except TesseractError as exc:
        raise RuntimeError('OCR 引擎無法處理照片，請更換清晰的車牌照片') from exc

    ordered = [plate for plate, _ in vote.most_common(5)]
    return {
        'candidates': ordered,
        'votes': {plate: vote[plate] for plate in ordered},
        'raw': raw[:12],
        'detected_plate': plate_image is not None,
        'warning': '候選車牌仍可能有誤；請核對照片再手動確認進出場。',
    }

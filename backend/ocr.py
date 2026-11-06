"""Camera-assisted OCR: detect/rectify a plate, then read letters and digits.

A conventional OCR engine is fallible, especially on stylized, reflective or
screen-photographed plates. Results are *suggestions*: never register a visit
without the user's explicit visual confirmation.
"""
import base64
import io
import re
from collections import Counter

MAX_FRAMES = 5
MAX_BATCH_PIXELS = 18_000_000

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


def _character_boxes(rectified):
    """Find actual glyph boundaries using connected components, not X ratios.

    A 3-letter / 4-digit plate normally yields seven tall foreground islands.
    The dash is deliberately excluded because it is much shorter. If segmentation
    is uncertain we return [] and keep the whole-line OCR fallback.
    """
    import cv2
    import numpy as np

    gray = cv2.cvtColor(rectified, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    top, bottom = round(height * .12), round(height * .9)
    band = gray[top:bottom]
    ink = cv2.adaptiveThreshold(band, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY_INV, 41, 10)
    ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3)))
    contours, _ = cv2.findContours(ink, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if not (width * .018 < w < width * .22 and
                h > (bottom - top) * .27 and
                x > width * .015 and x + w < width * .985):
            continue
        if cv2.contourArea(contour) < width * height * .001:
            continue
        boxes.append((x, y + top, w, h))
    boxes.sort(key=lambda b: b[0])
    if len(boxes) not in (6, 7):
        return []
    # Reject blobs on unrelated text lines, borders and perspective artifacts.
    midpoints = sorted(y + h / 2 for _, y, _, h in boxes)
    median_y = midpoints[len(midpoints) // 2]
    if any(abs(y + h / 2 - median_y) > height * .22 for _, y, _, h in boxes):
        return []
    return boxes


def _segmented_read(rectified, ocr_fn):
    """Separate the letter and digit runs by detected glyph positions.

    Glyph contours are only used to locate the *groups*: OCR still reads the
    full text in each group, preserving context and avoiding single-character
    errors. An ambiguous segmentation gracefully falls back to whole-line OCR.
    """
    import cv2

    boxes = _character_boxes(rectified)
    if not boxes:
        return []
    gray = cv2.cvtColor(rectified, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    gaps = [boxes[i + 1][0] - (boxes[i][0] + boxes[i][2])
            for i in range(len(boxes) - 1)]
    # A hyphen or a wider inter-group gap should fall between glyph 2/3 and 3/4.
    valid_splits = [i for i in (2, 3) if len(boxes) - i in (3, 4)]
    split = max(valid_splits, key=lambda i: gaps[i - 1])
    if len(boxes) == 7 and split != 3:
        return []
    if len(boxes) == 6 and split == 3:
        # Often a missing final digit on a 3+4 plate; don't award strong
        # segmentation votes to a truncated 3+3 token. Whole-line OCR can
        # still recognize genuine older 3+3 plates as a fallback.
        return []

    # Trim border/reflection outliers: use median glyph bounds rather than
    # the tallest blob, which can touch a screen or plate frame.
    begins = sorted(y for _, y, _, _ in boxes)
    ends = sorted(y + h for _, y, _, h in boxes)
    top = max(0, begins[len(begins) // 2] - 15)
    bottom = min(height, ends[len(ends) // 2] + 23)
    def group(start, end):
        x0 = max(0, boxes[start][0] - 12)
        x1 = min(width, boxes[end - 1][0] + boxes[end - 1][2] + 12)
        return gray[top:bottom, x0:x1]

    letters_image, digits_image = group(0, split), group(split, len(boxes))
    letters_count, digits_count = split, len(boxes) - split
    def read(image, psm, whitelist):
        padded = cv2.copyMakeBorder(image, 22, 22, 22, 22,
                                   cv2.BORDER_CONSTANT, value=255)
        return re.sub(r'[^A-Z0-9]', '', ocr_fn(padded, psm, whitelist).upper())

    letter_votes = Counter()
    for psm in (6, 7):
        token = read(letters_image, psm, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ')
        if re.fullmatch(r'[A-Z]{%d}' % letters_count, token):
            letter_votes[token] += 1
    digit_votes = Counter()
    for image, psm in ((digits_image, 8),
                       (cv2.adaptiveThreshold(digits_image, 255,
                        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                        cv2.THRESH_BINARY, 41, 10), 8)):
        token = read(image, psm, '0123456789')
        if re.fullmatch(r'[0-9]{%d}' % digits_count, token):
            digit_votes[token] += 1
    if not letter_votes or not digit_votes:
        return []
    letters, lc = letter_votes.most_common(1)[0]
    digits, dc = digit_votes.most_common(1)[0]
    # Two different group OCR configurations supporting the same answer is
    # useful within one photo, but never equivalent to two independent frames.
    return [(letters + '-' + digits, 1 + lc + dc)]


def _image_quality(img):
    """Coarse diagnostics, not a calibrated recognition probability."""
    import cv2
    import numpy as np

    frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)
    if frame.shape[1] > 750:
        frame = cv2.resize(frame, (750, round(frame.shape[0] * 750 / frame.shape[1])))
    focus = float(cv2.Laplacian(frame, cv2.CV_64F).var())
    tips = []
    if focus < 35:
        tips.append('畫面可能模糊；請穩定鏡頭並重新對焦')
    if float(np.mean(frame > 248)) > .38:
        tips.append('照片有較大片過曝區域，請避開反光')
    if float(np.mean(frame < 32)) > .65:
        tips.append('畫面偏暗，建議補光')
    return {'sharpness': round(focus, 1), 'tips': tips}


def _recognize_image(img, run):
    import cv2

    img.thumbnail((1600, 1200))
    votes = Counter()
    plate_image = _rectify_plate(img)
    if plate_image is not None:
        gray = cv2.cvtColor(plate_image, cv2.COLOR_BGR2GRAY)
        adaptive = cv2.adaptiveThreshold(gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 10)
        for image in (adaptive, cv2.createCLAHE(clipLimit=2, tileGridSize=(8, 8)).apply(gray)):
            for plate in candidate_plates(run(image, 7)):
                votes[plate] += 1
        for plate, weight in _segmented_read(plate_image, run):
            votes[plate] += weight
    # Fall back to the original image if no plate contour or glyph boundary is found.
    gray, sharp, binary = _variants(img)
    for variant, psm in ((gray, 7), (sharp, 7), (binary, 7), (gray, 11)):
        for plate in candidate_plates(run(variant, psm)):
            votes[plate] += 1
    if not votes:
        for plate in candidate_plates(run(sharp, 6)):
            votes[plate] += 1
    return votes, plate_image is not None, _image_quality(img)


def _summarize_frames(results):
    """One vote per independent frame, unlike correlated OCR variants."""
    frame_support = Counter()
    total_scores = Counter()
    readable_frames = 0
    for frame in results:
        scores = frame['votes']
        total_scores.update(scores)
        if not scores:
            continue
        max_score = max(scores.values())
        winners = [plate for plate, value in scores.items() if value == max_score]
        if len(winners) != 1:
            continue  # ties do not manufacture additional evidence
        frame_support[winners[0]] += 1
        readable_frames += 1
    all_candidates = sorted(total_scores, key=lambda p: (-frame_support[p], -total_scores[p], p))[:5]
    top = all_candidates[0] if all_candidates else None
    support = frame_support[top] if top else 0
    runner_up = max((v for p, v in frame_support.items() if p != top), default=0)
    frames = len(results)
    if frames >= 3 and support >= 3 and support / frames >= .75 and support - runner_up >= 2:
        confidence = 'high'
    elif frames >= 2 and support >= 2 and support / frames >= .5 and support > runner_up:
        confidence = 'medium'
    else:
        confidence = 'low'
    tips = list(dict.fromkeys(tip for frame in results for tip in frame['quality']['tips']))[:3]
    if frames == 1:
        tips.insert(0, '只有單張照片，無法由不同影格交叉驗證')
    elif support < 2:
        tips.insert(0, '不同影格沒有取得一致結果，請重新對焦再辨識')
    return {
        'candidates': all_candidates,
        'votes': {plate: total_scores[plate] for plate in all_candidates},
        'frame_support': {plate: frame_support[plate] for plate in all_candidates},
        'frames_total': frames,
        'frames_with_clear_vote': readable_frames,
        'confidence': confidence,
        'review_required': True,
        'quality_tips': tips,
        'detected_plate': any(frame['detected'] for frame in results),
        'warning': '信心等級是多影格一致性提示，不是辨識正確率；請人工逐字核對照片。',
    }


def recognize(data_url=None, *, images=None):
    """Recognize a single photo or up to five genuinely separate camera frames."""
    if images is None:
        images = [data_url]
    if not isinstance(images, list) or not 1 <= len(images) <= MAX_FRAMES:
        raise ValueError('請提供 1 至 5 張不同時間拍攝的圖片')
    # Identical photo bytes are not independent evidence. Count each exact
    # duplicate once even if a client submits the same picture five times.
    requested_frames = len(images)
    images = list(dict.fromkeys(images)) if all(isinstance(x, str) for x in images) else images
    duplicate_frames = requested_frames - len(images)
    photos = [_load_image(item) for item in images]
    if sum(img.width * img.height for img in photos) > MAX_BATCH_PIXELS:
        raise ValueError('連拍圖片總像素超過限制')
    try:
        import pytesseract
        from pytesseract import TesseractNotFoundError, TesseractError
        import cv2  # Check OpenCV separately for clear dependency error.
    except ImportError as exc:
        raise RuntimeError('OCR 套件未安裝，請先安裝 requirements.txt') from exc

    raw = []
    def run(image, psm, whitelist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-'):
        output = pytesseract.image_to_string(image, lang='eng',
            config=f'--oem 3 --psm {psm} -c tessedit_char_whitelist={whitelist}').strip()
        if len(raw) < 24:
            raw.append(output[:100])
        return output

    results = []
    try:
        for image in photos:
            votes, detected, quality = _recognize_image(image, run)
            results.append({'votes': votes, 'detected': detected, 'quality': quality})
    except TesseractNotFoundError as exc:
        raise RuntimeError('伺服器找不到 Tesseract OCR，請查看 README 安裝指南') from exc
    except TesseractError as exc:
        raise RuntimeError('OCR 引擎無法處理照片，請更換清晰的車牌照片') from exc
    summary = _summarize_frames(results)
    summary['raw'] = raw  # Diagnostic snippets, not an accuracy metric.
    summary['frames_received'] = requested_frames
    if duplicate_frames:
        summary['quality_tips'].append(f'{duplicate_frames} 張完全相同的照片未重複計票')
    return summary

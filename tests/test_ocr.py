"""Regression tests for camera-assisted OCR candidate normalization."""
import base64
import io
import unittest
from unittest.mock import patch
from PIL import Image

from backend.ocr import candidate_plates, recognize, _rectify_plate, _character_boxes, _segmented_read, _summarize_frames


def photo_data_url():
    image = Image.new('RGB', (480, 160), 'white')
    out = io.BytesIO()
    image.save(out, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode('ascii')


class OcrTests(unittest.TestCase):
    def test_plain_unhyphenated_plate(self):
        self.assertEqual(candidate_plates('ABC1234'), ['ABC-1234'])
        self.assertEqual(candidate_plates('AB1234'), ['AB-1234'])

    def test_common_ocr_confusions_only_in_number_part(self):
        self.assertEqual(candidate_plates('ABC-I234'), ['ABC-1234'])
        self.assertEqual(candidate_plates('ABS-12B4'), ['ABS-1284'])

    def test_not_a_partial_match_in_long_token(self):
        self.assertEqual(candidate_plates('XXXXABC1234XXXX'), [])

    def test_candidates_sorted_by_agreement(self):
        # Four OCR variants; first three agree and one differs.
        with patch('pytesseract.image_to_string', side_effect=[
                'ABC1234', 'ABC-1234', 'ABC-I234', 'XYZ9876']):
            result = recognize(photo_data_url())
        self.assertEqual(result['candidates'][0], 'ABC-1234')
        self.assertEqual(result['votes']['ABC-1234'], 3)


    def test_perspective_detection_finds_light_plate(self):
        # Synthetic license plate against a saturated car body. Does not embed
        # any photo or plate number from a real visitor.
        from PIL import ImageDraw
        image = Image.new('RGB', (1000, 570), '#116c50')
        ImageDraw.Draw(image).polygon([(110, 135), (860, 180), (810, 450), (90, 400)], fill='white')
        plate = _rectify_plate(image)
        self.assertIsNotNone(plate)
        self.assertEqual(plate.shape[:2], (420, 1000))

    def test_frame_consensus_does_not_count_variants_as_frames(self):
        from collections import Counter
        def frame(plate, score):
            return {'votes': Counter({plate: score}), 'detected': True,
                    'quality': {'tips': []}}
        sample = [frame('VVV-9999', 4), frame('VVV-9999', 3),
                  frame('MVV-9999', 5), frame('VVV-9999', 2),
                  frame('VVV-9999', 5)]
        result = _summarize_frames(sample)
        self.assertEqual(result['candidates'][0], 'VVV-9999')
        self.assertEqual(result['frame_support']['VVV-9999'], 4)
        self.assertEqual(result['confidence'], 'high')
        self.assertTrue(result['review_required'])
        # A high vote count for ONE frame must not mean high confidence.
        self.assertEqual(_summarize_frames([frame('MVV-9999', 99)])['confidence'], 'low')

    def test_multi_frame_tie_remains_low_confidence(self):
        from collections import Counter
        data = [{'votes': Counter({candidate: 4}), 'detected': True,
                 'quality': {'tips': []}}
                for candidate in ('VVV-9999', 'MVV-9999')]
        result = _summarize_frames(data)
        self.assertEqual(result['confidence'], 'low')
        self.assertEqual(result['frames_total'], 2)

    def test_five_frames_batch_validates_input(self):
        with self.assertRaises(ValueError):
            recognize(images=[])
        with self.assertRaises(ValueError):
            recognize(images=[photo_data_url()] * 6)
        with self.assertRaises(ValueError):
            recognize(images='not-a-list')
        result = recognize(images=[photo_data_url(), photo_data_url()])
        self.assertEqual(result['frames_received'], 2)
        self.assertEqual(result['frames_total'], 1)  # duplicate image bytes are one frame
        self.assertEqual(result['confidence'], 'low')
        self.assertTrue(result['review_required'])

    def test_dynamic_glyph_boundaries_and_group_ocr(self):
        import cv2
        import numpy as np
        # Intentionally move glyph boundaries away from hard-coded 405/430 X cuts.
        plate = np.full((420, 1000, 3), 250, dtype=np.uint8)
        for glyph, x in zip('VVV9999', (80, 205, 330, 520, 630, 740, 850)):
            cv2.putText(plate, glyph, (x, 283), cv2.FONT_HERSHEY_SIMPLEX,
                        3.8, (9, 9, 9), 9, cv2.LINE_AA)
        boxes = _character_boxes(plate)
        self.assertEqual(len(boxes), 7)
        self.assertGreater(boxes[0][0], 45)
        # Full-run OCR groups preserve context; the test can supply the
        # recognizer result without being dependent on platform fonts.
        def fake_read(_image, _psm, whitelist):
            return 'VVV' if whitelist.startswith('ABC') else '9999'
        self.assertEqual(_segmented_read(plate, fake_read)[0][0], 'VVV-9999')

    def test_no_invented_high_confidence_on_single_frame(self):
        with patch('pytesseract.image_to_string', return_value='VVV-9999'):
            result = recognize(photo_data_url())
        self.assertEqual(result['confidence'], 'low')
        self.assertEqual(result['frames_total'], 1)
        self.assertIn('單張', result['quality_tips'][0])

    def test_reject_unsupported_format(self):
        with self.assertRaises(ValueError):
            recognize('data:text/plain;base64,SGVsbG8=')


if __name__ == '__main__':
    unittest.main()

"""Regression tests for camera-assisted OCR candidate normalization."""
import base64
import io
import unittest
from unittest.mock import patch
from PIL import Image

from backend.ocr import candidate_plates, recognize


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

    def test_reject_unsupported_format(self):
        with self.assertRaises(ValueError):
            recognize('data:text/plain;base64,SGVsbG8=')


if __name__ == '__main__':
    unittest.main()

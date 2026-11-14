import io
import struct
import unittest
from unittest.mock import patch

from PIL import Image, UnidentifiedImageError
from source_helpers import load_module


class BackendImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.images = load_module('util.image_io')

    def test_png_pixels_and_alpha_survive(self):
        output = io.BytesIO()
        Image.new('RGBA', (2, 2), (10, 20, 30, 40)).save(output, format='PNG')
        with self.images.open_backend_image(output.getvalue()) as image:
            self.assertEqual(image.getpixel((0, 0)), (10, 20, 30, 40))

    def test_psd_is_rejected_before_decoding(self):
        psd = (b'8BPS' + struct.pack('>H', 1) + bytes(6)
               + struct.pack('>HIIHH', 3, 1, 1, 8, 3)
               + struct.pack('>IIIH', 0, 0, 0, 0) + bytes([255, 0, 0]))
        with self.assertRaises(UnidentifiedImageError):
            self.images.open_backend_image(psd)

    def test_oversize_header_rejected_before_pixel_load(self):
        with patch.object(self.images.Image, 'open') as opened:
            opened.return_value.size = (100_000, 100_000)
            with self.assertRaises(ValueError):
                self.images.open_backend_image(b'fixture')
            opened.return_value.load.assert_not_called()
            opened.return_value.close.assert_called_once()

    def test_invalid_image_is_rejected(self):
        with self.assertRaises(UnidentifiedImageError):
            self.images.open_backend_image(b'not an image')


class ColorMatchFallbackTests(unittest.TestCase):
    def test_unknown_method_keeps_a_valid_fallback(self):
        import numpy as np
        color = load_module('util.color_match')
        source = np.full((2, 2, 3), .25, dtype=np.float32)
        result = color.color_match_single(source, source, method='old-saved-method')
        self.assertEqual(result.shape, source.shape)
        self.assertTrue(np.isfinite(result).all())

"""Bounded decoding for image bytes returned by the inference server."""
import io

from PIL import Image

BACKEND_IMAGE_FORMATS = ('PNG', 'JPEG', 'WEBP')
MAX_BACKEND_IMAGE_PIXELS = 64 * 1024 * 1024
MAX_BACKEND_IMAGE_BYTES = 256 * 1024 * 1024


def open_backend_image(data):
    """Decode only expected raster formats, checking dimensions before load.

    The caller owns the returned image and may use it as a context manager.
    Pillow's plugin allowlist is applied before any format-specific decoding.
    """
    if not data or len(data) > MAX_BACKEND_IMAGE_BYTES:
        raise ValueError('Backend image is empty or exceeds the byte limit')
    image = Image.open(io.BytesIO(data), formats=BACKEND_IMAGE_FORMATS)
    try:
        width, height = image.size
        if width <= 0 or height <= 0 or width * height > MAX_BACKEND_IMAGE_PIXELS:
            raise ValueError('Backend image exceeds the pixel limit')
        image.load()
    except Exception:
        image.close()
        raise
    return image

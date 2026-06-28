"""Image filtering and processing utilities."""

import pathlib
import typing as tp

import cv2
import numpy as np
from numpy import ma

from . import geometry_utils

SUPPORTED_IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"]
SUPPORTED_MULTIPAGE_EXTENSIONS = [".pdf", ".tif", ".tiff"]


class UnsupportedImageError(ValueError):
    """Raised when an input file is not a supported image type."""


def _try_load_pdf_pages(path: pathlib.PurePath) -> tp.Optional[tp.List[np.ndarray]]:
    """Try to load all pages of a PDF as a list of images. Returns None if no
    PDF backend is available or the file isn't a valid PDF."""
    # Try pypdfium2 first (no native dependencies)
    try:
        import pypdfium2 as pdfium  # type: ignore
    except ImportError:
        pdfium = None  # type: ignore
    if pdfium is not None:
        try:
            doc = pdfium.PdfDocument(str(path))
            pages = []
            for i in range(len(doc)):
                page = doc[i]
                pil_image = page.render(scale=2).to_pil()
                pages.append(cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR))
            return pages
        except Exception:
            return None

    # Fall back to pdf2image (requires poppler)
    try:
        from pdf2image import convert_from_path  # type: ignore
    except ImportError:
        return None
    try:
        pil_images = convert_from_path(str(path), dpi=200)
        return [cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR) for img in pil_images]
    except Exception:
        return None


def _try_load_multipage_tiff(path: pathlib.PurePath) -> tp.Optional[tp.List[np.ndarray]]:
    """Try to load every page of a multi-page TIFF as a list of images."""
    try:
        # OpenCV can read all pages via imreadmulti
        ok, frames = cv2.imreadmulti(str(path))
    except cv2.error:
        return None
    if not ok or frames is None:
        return None
    if isinstance(frames, np.ndarray) and frames.ndim == 3:
        return [frames]
    return [f for f in frames]


def load_image_pages(path: pathlib.PurePath) -> tp.List[np.ndarray]:
    """Load an image file as one or more pages.

    For single-page images (PNG/JPG/etc.) this returns a one-element list.
    For multi-page TIFFs and PDFs it returns one image per page.

    Raises UnsupportedImageError if the file extension is not supported or the
    file cannot be read."""
    suffix = "".join(path.suffixes).lower()
    if suffix not in SUPPORTED_IMAGE_EXTENSIONS and suffix not in SUPPORTED_MULTIPAGE_EXTENSIONS:
        raise UnsupportedImageError(f"Unsupported file type: {suffix}")

    if suffix == ".pdf":
        pages = _try_load_pdf_pages(path)
        if pages is None or len(pages) == 0:
            raise UnsupportedImageError(
                "PDF support requires pypdfium2 or pdf2image (with poppler) installed.")
        return pages

    if suffix in (".tif", ".tiff"):
        pages = _try_load_multipage_tiff(path)
        if pages is None or len(pages) == 0:
            # Fall back to single-image read
            img = cv2.imread(str(path))
            if img is None:
                raise UnsupportedImageError(f"Could not read image: {path}")
            return [img]
        return pages

    img = cv2.imread(str(path))
    if img is None:
        raise UnsupportedImageError(f"Could not read image: {path}")
    return [img]


def convert_to_grayscale(image: np.ndarray,
                         save_path: tp.Optional[pathlib.PurePath] = None
                         ) -> np.ndarray:
    """Convert an image to grayscale.

    If `save_path` is provided, will save the resulting image to this location
    as "grayscale.jpg". Used for debugging purposes.
    """
    result = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if save_path:
        save_image(save_path / "grayscale.jpg", result)
    return result


def remove_hf_noise(image: np.ndarray,
                    save_path: tp.Optional[pathlib.PurePath] = None
                    ) -> np.ndarray:
    """Blur image slightly to remove high-frequency noise.

    If `save_path` is provided, will save the resulting image to this location
    as "noise_filtered.jpg". Used for debugging purposes.
    """
    # This value for sigma was chosen such that sigma=sqrt(2) for 2500px images.
    # Still needs more verification for low-res images.
    # NOTE: This assumes the image is roughly A4 paper shaped. If it this fails,
    # consider switching to using the mean of the image dimensions.
    sigma = min(get_dimensions(image)) * (5.6569e-4)
    result = cv2.GaussianBlur(image, (0, 0), sigmaX=sigma)
    if save_path:
        save_image(save_path / "noise_filtered.jpg", result)
    return result


def detect_edges(image: np.ndarray,
                 save_path: tp.Optional[pathlib.PurePath] = None
                 ) -> np.ndarray:
    """Detect edges in the image.

    If `save_path` is provided, will save the resulting image to this location
    as "edges.jpg". Used for debugging purposes.
    """
    low_threshold = 100
    result = cv2.Canny(image,
                       low_threshold,
                       low_threshold * 3,
                       L2gradient=True,
                       edges=3)
    if save_path:
        save_image(save_path / "edges.jpg", result)
    return result


def find_contours(edges: np.ndarray) -> np.ndarray:
    """Find the contours in an edge-detected image."""
    contours, _ = cv2.findContours(edges, cv2.RETR_TREE,
                                   cv2.CHAIN_APPROX_SIMPLE)
    return contours


def get_image(path: pathlib.PurePath,
              save_path: tp.Optional[pathlib.PurePath] = None) -> np.ndarray:
    """Returns the cv2 image located at the given path.

    For backwards compatibility this returns the first page of a multi-page
    image. Prefer `load_image_pages` for new code.

    If `save_path` is provided, will save the resulting image to this location
    as "original.jpg". Used for debugging purposes.
    """
    pages = load_image_pages(path)
    result = pages[0]
    if save_path:
        save_image(save_path / "original.jpg", result)
    return result


def save_image(path: pathlib.PurePath, image: np.ndarray):
    """Save the given image at the provided path. Path must be the full target
    including file extension."""
    cv2.imwrite(str(path), image)


def find_polygons(image: np.ndarray,
                  save_path: tp.Optional[pathlib.PurePath] = None
                  ) -> tp.List[geometry_utils.Polygon]:
    """Returns a list of polygons found in the image."""
    edges = detect_edges(image, save_path=save_path)
    all_contours = find_contours(edges)
    polygons = [
        geometry_utils.approx_poly(contour) for contour in all_contours
    ]
    return polygons


def get_dimensions(image: np.ndarray) -> tp.Tuple[int, int]:
    """Returns the dimensions of the image in `(width, height)` form."""
    return image.shape[0], image.shape[1]


def threshold(image: np.ndarray,
              save_path: tp.Optional[pathlib.PurePath] = None) -> np.ndarray:
    """Convert an image to pure black and white pixels by thresholding.

    If `save_path` is provided, will save the resulting image to this location
    as "thresholded.jpg". Used for debugging purposes.
    """
    gray_image = convert_to_grayscale(image)
    _, result = cv2.threshold(gray_image, 0, 255,
                              cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    if save_path:
        save_image(save_path / "thresholded.jpg", result)
    return result


def prepare_scan_for_processing(image: np.ndarray,
                                save_path: tp.Optional[pathlib.PurePath] = None
                                ) -> np.ndarray:
    """Shortcut to prepare an image for processing.

    If `save_path` is provided, will save the resulting image to this location
    as "prepared.jpg". Used for debugging purposes.
    """
    without_noise = remove_hf_noise(image, save_path=save_path)
    result = threshold(without_noise, save_path=save_path)
    return result


def get_fill_percent(matrix: tp.Union[np.ndarray, ma.MaskedArray]) -> float:
    """Get the percent of the pixels in the B&W matrix that are not white."""
    try:
        return 1 - (matrix.mean() / 255)
    except ZeroDivisionError:
        return 0


def dilate(image: np.ndarray,
           save_path: tp.Optional[pathlib.PurePath] = None) -> np.ndarray:
    """Dilate the image.

    If `save_path` is provided, will save the resulting image to this location
    as "dilated.jpg". Used for debugging purposes.
    """
    # Dilation is done with a static kernel size of 3 x 3 pixels. This means it
    # has a far more significant effect on smaller images, which helps to
    # counter the detail loss when Gaussian filtering small images that already
    # have too little detail.
    result = cv2.dilate(image, np.ones((3, 3), np.uint8), iterations=1)
    if save_path:
        save_image(save_path / "dilated.jpg", result)
    return result


def bw_to_bgr(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image.copy(), cv2.COLOR_GRAY2BGR)


def draw_polygons(image: np.ndarray,
                  polygons: tp.List[geometry_utils.Polygon],
                  full_save_path: tp.Optional[pathlib.PurePath] = None,
                  thickness: int = 1) -> np.ndarray:
    """Draw all the polygons on the image (for debugging) and return or save the result."""
    points = [np.array([[p.x, p.y] for p in poly], np.int32).reshape((-1, 1, 2)) for poly in polygons]
    result = cv2.polylines(bw_to_bgr(image), points, True, (0, 0, 255), thickness)
    if full_save_path:
        save_image(full_save_path, result)
    return result

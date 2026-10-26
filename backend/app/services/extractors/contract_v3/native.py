"""Read native PDF glyphs and render each page once for Layout and OCR."""

from __future__ import annotations

import fitz
from PIL import Image


class NativeReader:
    def read_page(
        self, page: fitz.Page, *, source: str, digest: str, page_no: int
    ) -> tuple[dict, dict, Image.Image, bytes]:
        raw = page.get_text("rawdict")
        # A searchable scan can carry invisible, stale OCR glyphs. Those are
        # provenance only: they must neither replace nor mask the rendered text.
        # Older PyMuPDF versions lack raw span alpha; use paint traces there.
        hidden_origins = {
            (c[0], round(c[2][0], 3), round(c[2][1], 3))
            for span in page.get_texttrace()
            if span["type"] == 3 or span.get("opacity", 1) == 0
            for c in span["chars"]
        }
        for block in raw["blocks"]:
            for key in list(block):
                if isinstance(block[key], bytes):
                    del block[key]
            for line in block.get("lines", []):
                for span in line["spans"]:
                    for char in span["chars"]:
                        char["native_visible"] = (
                            span["alpha"] > 0 if "alpha" in span else
                            (ord(char["c"]), round(char["origin"][0], 3), round(char["origin"][1], 3))
                            not in hidden_origins
                        )
        sample = {
            "id": f"{digest[:12]}-p{page_no}",
            "pdf": source,
            "page": page_no,
            "pdf_sha256": digest,
            "raster_images": page.get_image_info(),
            "coordinate_transform": {
                "pixel_to_native": list(fitz.Matrix(0.5, 0.5) * page.derotation_matrix),
                "native_to_pixel": list(page.rotation_matrix * fitz.Matrix(2, 2)),
                "dpi": 144,
            },
        }
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        return sample, raw, image, pix.tobytes("png")

    @staticmethod
    def has_native_chars(raw: dict) -> bool:
        return any(
            any(c.get("native_visible", True) for c in span.get("chars", []))
            for block in raw["blocks"]
            if block["type"] == 0
            for line in block["lines"]
            for span in line["spans"]
        )

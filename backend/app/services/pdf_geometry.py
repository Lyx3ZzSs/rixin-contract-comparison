"""Keep all comparison readers in PDF display coordinates without changing uploads.

PyMuPDF text uses unrotated PDF coordinates; rendered images and the browser use
/Rotate. Bake that transform into a task-owned calculation copy before any
reader runs. Native text, vectors, annotations, OCR crops and visual recall then
share one space. The upload and its signature bytes remain untouched.
"""

from pathlib import Path
import os
import tempfile

import fitz


def prepare_display_pdf(source: Path, destination: Path) -> tuple[Path, dict]:
    source = Path(source)
    audit = {"coordinate_space": "PDF_DISPLAY", "source_path": str(source), "pages": []}
    if not source.is_file():
        # The extractor owns missing-input diagnostics.
        return source, audit
    with fitz.open(source) as pdf:
        rotated = [p.number for p in pdf if p.rotation]
        if not rotated:
            return source, audit
        for index in rotated:
            page = pdf[index]
            audit["pages"].append(
                {
                    "page_no": index + 1,
                    "source_rotation": page.rotation,
                    "source_to_display": list(page.rotation_matrix),
                    "display_to_source": list(page.derotation_matrix),
                    "display_size": [page.rect.width, page.rect.height],
                }
            )
            # Unlike set_rotation(0), this preserves the visible page and text.
            page.remove_rotation()
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(suffix=".pdf", dir=destination.parent)
        os.close(fd)
        try:
            pdf.save(temporary, garbage=3, deflate=True)
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
    audit["calculation_path"] = str(destination)
    return destination, audit

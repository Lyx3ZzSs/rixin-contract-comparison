"""Re-read disputed credit-code glyphs from their own localized image evidence."""
from __future__ import annotations

import hashlib
import re
import tempfile
import unicodedata
from pathlib import Path

import fitz

from app.models import BBox
from app.services.field_observation import ObservationState


def compact(value: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)).upper()


def ambiguous_pair(left: str, right: str) -> bool:
    if not re.fullmatch(r'[A-Z0-9]{18}', left) or not re.fullmatch(r'[A-Z0-9]{18}', right):
        return False
    differences = [frozenset((a, b)) for a, b in zip(left, right) if a != b]
    return len(differences) == 1 and differences[0] in {
        frozenset('O0'), frozenset('I1'), frozenset('L1'), frozenset('S5'),
    }


def recover_disputed_credit_codes(pdf_path, region, peer, extractor) -> list[dict]:
    if region is None or peer is None:
        return []
    diagnostics = []
    for element in region.elements:
        if element.raw_ref.get('field_key') != 'credit_code':
            continue
        peers = [e for e in peer.elements if e.raw_ref.get('field_key') == 'credit_code'
                 and e.raw_ref.get('party_role') == element.raw_ref.get('party_role')]
        before = compact(element.text)
        if len(peers) != 1 or not ambiguous_pair(before, compact(peers[0].text)):
            continue
        evidence = element.raw_ref.get('value_evidence') or []
        # Native text is preserved. A counterpart only triggers inspection;
        # it never supplies a replacement value or selects an OCR candidate.
        if (not evidence or any(str(e.get('source', '')).startswith('native') for e in evidence)
                or not all(e.get('localized') and e.get('provenance_valid') for e in evidence)
                or element.raw_ref.get('rejected_contaminated_evidence')
                or not element.raw_ref.get('value_bbox')):
            continue
        diagnostic = {'page': element.page_no, 'party': element.raw_ref.get('party_role'),
                      'field': 'credit_code', 'method': 'DISPUTED_IDENTIFIER_LOCAL_OCR',
                      'before': element.text, 'status': 'NOT_RUN', 'readings': []}
        diagnostics.append(diagnostic)
        element.raw_ref['identifier_local_ocr'] = diagnostic
        try:
            with fitz.open(pdf_path) as document:
                page = document[element.page_no-1]
                box = BBox.model_validate(element.raw_ref['value_bbox'])
                clip = fitz.Rect(box.x0-3, box.y0-3, box.x1+3, box.y1+3) & page.rect
                if clip.is_empty:
                    diagnostic['status'] = 'INVALID_ROI'
                    continue
                for scale in (4.0, 6.0):
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False, annots=False)
                    with tempfile.NamedTemporaryFile(suffix='.png') as image:
                        pix.save(image.name)
                        digest = hashlib.sha256(Path(image.name).read_bytes()).hexdigest()
                        parsed = extractor.payload_to_document(extractor.predict(image.name, file_type=1), image.name)
                    blocks = [b for p in parsed.pages for b in p.blocks if b.text.strip()]
                    if (len(parsed.pages) != 1 or len(blocks) != 1
                            or blocks[0].confidence is None or blocks[0].confidence < .95):
                        diagnostic['status'] = 'AMBIGUOUS_LOCAL_READING'
                        break
                    b = blocks[0]; value = compact(b.text)
                    if value != before and not ambiguous_pair(before, value):
                        diagnostic['status'] = 'LOCAL_READING_CONFLICTS_WITH_EXISTING_VALUE'
                        break
                    width, height = parsed.pages[0].width, parsed.pages[0].height
                    if width <= 0 or height <= 0:
                        diagnostic['status'] = 'INVALID_OCR_GEOMETRY'
                        break
                    mapped = BBox(x0=clip.x0+b.bbox.x0/width*clip.width,
                                  y0=clip.y0+b.bbox.y0/height*clip.height,
                                  x1=clip.x0+b.bbox.x1/width*clip.width,
                                  y1=clip.y0+b.bbox.y1/height*clip.height)
                    diagnostic['readings'].append({'source': 'local_identifier_ocr', 'text': value,
                        'normalized_text': value, 'raw_text': b.text, 'page_no': element.page_no,
                        'bbox': mapped.model_dump(), 'confidence': b.confidence, 'field_key': 'credit_code',
                        'party_role': element.raw_ref.get('party_role'), 'localized': True,
                        'provenance_valid': True, 'render_scale': scale, 'input_sha256': digest})
        except Exception as exc:
            diagnostic.update(status='OCR_ERROR', error=type(exc).__name__)
        readings = diagnostic['readings']
        if diagnostic['status'] != 'NOT_RUN':
            continue
        if len(readings) != 2 or readings[0]['text'] != readings[1]['text']:
            diagnostic['status'] = 'LOCAL_READINGS_DISAGREE'
            continue
        # Scale agreement is a stability check, not independent recognizers.
        # Any genuine difference remaining after own-side recovery is compared
        # normally; this function never declares two documents equivalent.
        value = readings[0]['text']
        element.raw_ref['superseded_value_evidence'] = evidence
        element.raw_ref.update(value_evidence=readings, value_bbox=readings[0]['bbox'],
                               display_value_bbox=readings[0]['bbox'], value_bboxes=[readings[0]['bbox']],
                               content_recognition_status='CONFIRMED',
                               content_recognition_reason='CONSISTENT_LOCAL_IDENTIFIER_READINGS',
                               observation_state='OBSERVED_VALUE',
                               observation_reason='LOCALIZED_VALUE_EVIDENCE')
        element.text = value
        element.observation_state = ObservationState.OBSERVED_VALUE
        element.observation_reason = 'LOCALIZED_VALUE_EVIDENCE'
        diagnostic.update(status='RECOVERED', after=value)
    return diagnostics

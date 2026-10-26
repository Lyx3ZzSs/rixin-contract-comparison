Region comparison regression fixtures (2026-10-20)

huabei_forms.json captures prepared document pages 15, 26-30 from task
7e7650b0-7fc5-40d0-95fd-fb7197af47c4. It preserves the native/scan block types,
physical geometry, text, table structure and routing flags that reproduce the
five reported false positives. Document paths are replaced with fixture paths;
character-level OCR, unrelated pages and model diagnostics are omitted.

occluded_signing_row.json captures page 13 of SUIZHOU_CHANGYUAN from the
same-input full-corpus validation of this change. Printed signing labels are
partially occluded by seals. It guards against treating incomplete reading as
confirmed deletion during cross-representation row matching.

These are focused regression inputs, not replacements for the independent
15-contract Golden corpus. No Golden oracle was changed for this repair.

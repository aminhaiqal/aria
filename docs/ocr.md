# Phase F self-hosted OCR

## Scope

Phase F converts only archived PDFs already classified `ocr_required` into searchable evidence.
It does not retrieve live sources, replace official bytes, infer legal meaning, compare versions,
or clear warnings through manual overrides.

The JPDP execution boundary was exactly five source artifacts covering 56 pages. OCR runs in an
isolated concurrency-one Celery worker built with OCRmyPDF, Tesseract, and Malay/English language
data.

## Evidence lineage

```text
Official RawArtifact in R2
    |
    +---- OCRRun (profile + configuration hash + toolchain + page metrics)
    |       |
    |       +---- ArtifactDerivative -> searchable PDF in R2
    |       +---- ArtifactDerivative -> UTF-8 text sidecar in R2
    |
    +---- ArtifactObservation -> official URL and retrieval provenance

Searchable PDF -> ExtractionRun -> DocumentVersion -> NormalizedSection
                                         |
                                         +---- source artifact hash
                                         +---- derivative artifact hash
                                         +---- OCR run/profile/configuration
```

The original source remains the `VersionEvidence.raw_artifact` and
`NormalizedSection.source_artifact`. The derivative is recoverable through the extraction run and
is repeated in normalized metadata and section locators. This prevents OCR text from being
misrepresented as publisher-supplied text.

## Deterministic profile

The initial profile is `jpdp-msa-eng:1`:

- languages: `msa+eng`;
- automatic rotation and conservative deskew;
- skip pages that already contain text;
- no image optimization or aggressive cleanup;
- one OCR job at a time;
- searchable PDF plus UTF-8 sidecar;
- input/output page-count parity and non-empty recognized text required.

Configuration is hashed in full. Bump `ARIA_OCR_PROFILE_VERSION` when the OCR image, language data,
or processing options intentionally change.

## Operations

Inspect the bounded input set without processing it:

```bash
docker compose exec api python manage.py plan_ocr
```

Queue pending runs to the isolated worker:

```bash
docker compose exec api python manage.py queue_ocr
```

Run synchronously inside the OCR container for controlled maintenance:

```bash
docker compose exec ocr-worker python manage.py queue_ocr --sync
```

The planner creates/reuses deterministic run records. Repeating queueing after completion reports
five skipped runs and creates no new derivatives. Read-only inspection is available at:

| Resource | Endpoint |
| --- | --- |
| OCR runs | `/api/v1/ocr-runs/` |
| Artifact derivatives | `/api/v1/artifact-derivatives/` |
| Derived bytes | `/api/v1/artifacts/{id}/content/` |

## JPDP verified result

On 2026-08-04:

- five OCR runs succeeded and ten derivative records were created;
- all five source, searchable-PDF, and sidecar objects passed SHA-256 read-back checks in R2;
- input/output page counts matched at 19, 17, 5, 8, and 7;
- recognized non-whitespace character counts were 11,591, 13,240, 2,748, 5,232, and 7,223;
- the recorded toolchain was OCRmyPDF `14.0.1+dfsg1`, Tesseract `5.3.0`, `eng+msa`;
- 56 OCR-derived normalized sections retain both source and derivative lineage;
- replay queued zero OCR jobs and created no duplicate runs, derivatives, versions, or quality
  reports;
- the operational corpus became 19 current PDF versions and one current HTML version;
- extraction quality became 12 passed, 8 warnings, and 0 requiring review.

Warnings remain evidence for later review and are not automatically corrected by OCR.

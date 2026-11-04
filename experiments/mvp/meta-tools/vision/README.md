# Vision access probes

Discovery checked against official Meta documentation on 2026-11-01 UTC.
Only original CPU-drawn synthetic input is sent. This establishes access and
one-request latency, not segmentation accuracy or real-world photo utility.

## Planned calls and hard upper bound

| Probe | Model | Endpoint | Billing unit | Single call ceiling |
|---|---|---|---|---:|
| SAM | `sam-3.1` | `POST https://api.meta.ai/v1/responses` | One image segmented | $0.0025 |
| Muse edit | `muse-image-1.0` | `POST https://api.meta.ai/v1/images/edits` | `n=1` returned image | $0.0100 |

Two fixed calls, no automatic retries: **$0.0125 reserved maximum**. Failed or
timed-out calls retain their reservation pending reconciliation; a local receipt
prevents accidental resubmission. HTTP connect timeout is 20 s and read timeout
is 180 s. There is no GPU job, file-upload request, or separately billed search.
Prices are rate-derived; final billed charges require billing-page evidence.

The identical 256×256, 1,543-byte PNG is embedded as a base64 data URL in both
requests. SHA256: `1d95f3bf5c435e73173796667c9f61246ddbbca0ff1d8e4e4c8bf8c526deaa87`.
SAM's noun phrase is `red shipping container`. Muse edits the red container to
blue, uses low reasoning, and disables search and shell tools.

## Reproduce

```sh
UV_CACHE_DIR=/private/tmp/stowage-meta-tools-uv uv run python experiments/mvp/meta-tools/vision/probe_vision.py prep
# These next two commands each perform one paid request; run only within an authorized budget.
UV_CACHE_DIR=/private/tmp/stowage-meta-tools-uv uv run python experiments/mvp/meta-tools/vision/probe_vision.py sam
UV_CACHE_DIR=/private/tmp/stowage-meta-tools-uv uv run python experiments/mvp/meta-tools/vision/probe_vision.py muse
```

The script parses `/Users/anda/Projects/.env` inside Python, populates its process
environment, and sends the key only in the required HTTPS Authorization header.
It never takes credentials as arguments or writes headers. Only HTTP bodies,
request payloads, sanitized errors, timing, usage, and generated image bytes are
saved. Do not delete a receipt to bypass the no-retry guard.

## Official sources

- [Pricing and rate limits](https://dev.meta.ai/docs/pricing-rate-limits): SAM
  image segmentation $2.50/1,000 images; Muse $0.01 per returned generated image.
  Image token counts are informational; internal image-generation search is included.
- [SAM overview](https://dev.meta.ai/docs/sam/overview): non-streaming requests
  supported; output uses box/mask special-token text. Zero matches is valid.
- [Image generation](https://dev.meta.ai/docs/image-generation): `/images/edits`
  accepts JSON `images` with base64 data URLs and explicit `n`; low reasoning and
  disabled internal tools suit this minimal access probe.

## Results

Both calls succeeded on the account, after the English and Thai voice probes.

| Probe | UTC start 2026-11-01 | HTTP | Full HTTP request latency | Observed billing units | Rate-derived cost |
|---|---|---:|---:|---|---:|
| SAM | 01:23:38.108653 | 200 | 1.135770 s | `usage.images_processed=1` | $0.0025 |
| Muse edit | 01:23:47.027026 | 200 | 5.816966 s | One returned image | $0.0100 |

**Total: $0.0125**, two calls, no retries and no errors. These are published-rate
calculations from returned usage units, not a separately verified billing export.
Neither API response included a monetary charge. API latency includes HTTP
upload, service processing, and full response download, ending before local JSON
parsing, base64 decode, image saving, and validation. Local complete-work elapsed
was about 1.138 s for SAM and 8.432 s for Muse.

SAM reported `status=completed`, zero token counts, and one image processed. It
returned one box and one mask. The box is `(40,73)-(216,192)` in the 256×256 input,
consistent with the drawn container. Full special-token text and response are in
`sam-response.json`; receipt request id is
`3e67b7c9-3ce5-4e94-841e-5b46a7af6b1a`.

Muse returned one valid 1600×1600 WebP saved as `muse-output-0.webp`. Visual
inspection confirmed the container became blue and the simple scene remained
recognizable. `usage` reports 4,165 input / 407 output tokens, which are
informational and do not add token charges to the per-image fee. The response
JSON replaces its base64 image body with file metadata, preserving the exact
decoded bytes separately. Request id:
`0870bf70-de33-4583-85c9-3acae66139b4`.

## Verification and recommendation

- Python compilation passed before calls.
- Both original and edited images were visually inspected.
- Offline audit passed: both saved requests contain the exact same inline image;
  hashes agree; SAM completed with one image, box and mask; Muse returned exactly
  the requested `n=1`; rate-derived total is $0.0125.
- The two saved request bodies contain no credentials; Authorization is never
  logged. No `src/`, shared ledger, git, or GPU operations were performed here.

Photo access is cheap and available, but one synthetic success provides no
evidence about complex real-world image accuracy or deriving shipping
constraints. Build the English voice → displayed transcript → fine-tuned reader
path for 1.3 first, with typed fallback. Keep photo/SAM and image editing as optional
later demonstrations; Muse Image creates/edits pictures and is not a reader for
shipping instructions. Do not admit Thai based on this vision evidence.

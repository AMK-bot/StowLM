# Lossless rows archive

Original: `../rows.json`; 132,080,123 bytes.
SHA256: `6d3ffd91f22b3d06671b9ecbdefc07f4ebb5c0f271b62272b704a5b9a9dd6660`. The original remains unchanged.

Each ordered volume is an independent gzip member of at most 40,000,000
raw bytes. Every compressed volume is strictly below 50,000,000 bytes.
`manifest.json` records the byte size and SHA256 of the original, each compressed
volume and each uncompressed chunk. Archive creation streamed every decompressed
chunk through SHA256 and matched the original bytes exactly.

From this directory, reconstruct to a new file (never redirect over the original):

```sh
cat rows.json.part-*.gz | gzip -dc > rows.reconstructed.json
shasum -a 256 rows.reconstructed.json
wc -c < rows.reconstructed.json
```

Expected size: 132080123; expected SHA256: `6d3ffd91f22b3d06671b9ecbdefc07f4ebb5c0f271b62272b704a5b9a9dd6660`.
The four-digit volume names sort in manifest order for this archive. When using
an API, decompress each file in manifest order and concatenate the raw chunks.

From repository root, verify all volumes without creating a reconstructed file:

```sh
python3 experiments/mvp/pipeline/sealed/measurements/ext-1/archive_rows.py.txt verify experiments/mvp/pipeline/sealed/runs/ext-1/rows-archive/manifest.json
```

The archive contains the unchanged serialized rows; no metrics were recomputed.

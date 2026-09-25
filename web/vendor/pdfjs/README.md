# pdf.js Vendor Assets

Mozilla's pdf.js, version 6.3.289 of the `pdfjs-dist` package, used by
`web/document-grounding.js` to read the text out of a job description or resume
supplied as a PDF. Upstream is <https://github.com/mozilla/pdf.js>,
Apache-2.0; the license text is `web/vendor/LICENSE-apache-2.0.txt`.

## Files

- `pdf.min.mjs`: the API. Imported on demand the first time a PDF is chosen,
  so a lobby that never sees one never downloads it.
- `pdf.worker.min.mjs`: the parser, which pdf.js runs in a module Worker it
  starts itself from `GlobalWorkerOptions.workerSrc`.

Neither is committed. `scripts/fetch-vendor.sh` downloads both from the base URL
in `FETCH` and refuses any file whose SHA-256 does not match `SHA256SUMS`. The
pinned hashes were checked against the same two files inside the npm tarball
`https://registry.npmjs.org/pdfjs-dist/-/pdfjs-dist-6.3.289.tgz`, whose
`sha512` the registry publishes.

## What is deliberately missing

Only text is extracted, so nothing that draws a page is here: no `wasm/`
decoders (JBIG2, JPEG 2000, colour management), no `standard_fonts/`, no
`cmaps/`. A PDF whose text lives in embedded fonts with a `ToUnicode` map, which
is what word processors and LaTeX produce, including for CJK text, reads
without them. A PDF that only carries text as images (a scan) has nothing to
extract, and the lobby says so rather than guessing.

`isEvalSupported: false` keeps pdf.js off `new Function`, which the page's
Content-Security-Policy refuses anyway.

## Upgrading

Bump the version in `FETCH`, replace both hashes in `SHA256SUMS`, check them
against the npm tarball, and say in the commit message where the bytes came
from. The `.mjs` suffix is what pdf.js asks for by name, which is why
`content_type` in `src/web/assets.rs` maps it.

# OCR benchmark

Measured 2026-09-28 to pick the OCR engine anydoc-serve ships. Everything here is reproducible from `bench/`.

## Result

**Tesseract 5.5 (tesserocr 2.11), `tessdata_fast` 4.1.0 models, Sauvola binarization, language picked per page by
Tesseract's script detection, scan resolution passed explicitly.**

Measured through the server's own engine (`bench/server_check.py`, `bench/results-2026-09-28-server.log`), on the
same 12 pages:

| Engine | eng scan | jpn scan | tha scan | eng clean | jpn clean | tha clean | s/page, median | pages |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| anydoc-serve engine (as shipped) | 0.0% | 4.7% | 0.0% | 0.0% | 2.3% | 0.0% | 1.00 | 12 |

It was chosen from the benchmark row "Tesseract 5.5 tessdata_fast + Sauvola, language from script detection" (0.0% /
2.6% / 7.8% on the English / Japanese / Thai scans). The shipped numbers differ from that row for one reason, found
while testing the server: Tesseract needs the scan resolution. The benchmark handed it image files directly; the
server hands it decoded images, which carry no resolution, and on one 200 dpi Japanese test scan Tesseract then lost
whole phrases (13.8% CER, against 1.3 to 2.2% with any explicit value from 70 to 300 dpi). The server now always
passes the resolution: the render resolution for PDF pages (`OCR_DPI`), the image's own dpi tag for images, else
300. With the true 200 dpi, the Thai scans went from 7.8% to 0.0% and the Japanese scans from 2.6% to 4.7% (one of
the two Japanese scans got worse, 2.6% to 7.7%).

Why, from the numbers:

- **Latency.** RapidOCR (PaddleOCR models on ONNX Runtime) took 5.7 to 7.4 s per page, 6 to 7 times the chosen
  configuration; PP-OCRv6 medium took 64 s per page. A 50-page scan would take 5 to 6 minutes on RapidOCR small.
- **English.** Both RapidOCR configurations dropped or garbled whole lines of the English page (7.6% to 24.7% CER);
  every Tesseract configuration read English at 0.0 to 0.1%.
- **Where RapidOCR wins.** Japanese: PP-OCRv6 small read the Japanese scans at 0.2% CER against 4.7% for the shipped
  engine. It is the better engine for a Japanese-heavy workload where 7 s per page is acceptable. It is not in v1.
- **Sauvola binarization** is the single largest improvement: Tesseract's default global Otsu threshold lost half of
  every line on the unevenly lit Thai scan (33.4% CER); local Sauvola thresholding took it to 7.8% at no accuracy cost
  elsewhere.
- **One language per page beats all languages at once.** Loading `eng+jpn+tha` into one pass was far worse on clean
  Japanese in the Otsu run (12.9% vs 2.8%, 4.6x) and still worse with Sauvola (3.2% vs 2.5%, and 0.9% vs 0.0% on clean
  Thai). Script detection matched the page-language oracle on all 12 pages (it named the script correctly on every
  page) and also reports orientation, which the server uses to turn sideways and upside-down scans upright. It costs
  about 0.5 s per page over the oracle.
- **`tessdata_fast` over `tessdata` / `tessdata_best`.** With Sauvola, `tessdata_best` was no more accurate than `tessdata_fast` on any
  language and is 2.5x slower. Standard `tessdata` was better on the Japanese scans (1.1% vs 2.6%) but worse on clean Japanese (7.6% vs
  2.5%) and is 68 MB against 18 MB.

## Numbers

Character error rate (CER) per language, weighted by characters, and median seconds per page over all 12 pages.
"n/m" = not measured (see PP-OCRv6 medium below).

| Engine | eng scan | jpn scan | tha scan | eng clean | jpn clean | tha clean | s/page, median | pages |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract 5.5 tessdata_fast, page language | 0.0% | 2.8% | 33.4% | 0.0% | 2.8% | 0.0% | 0.36 | 12 |
| Tesseract 5.5 tessdata, page language | 0.0% | 1.3% | 33.4% | 0.0% | 7.8% | 0.0% | 0.47 | 12 |
| Tesseract 5.5 tessdata_best, page language | 0.0% | 2.4% | 33.9% | 0.0% | 8.6% | 0.0% | 1.37 | 12 |
| Tesseract 5.5 tessdata_fast, eng+jpn+tha | 0.1% | 3.0% | 35.0% | 0.0% | 12.9% | 0.0% | 1.11 | 12 |
| Tesseract 5.5 tessdata_fast + Sauvola, page language | 0.0% | 2.6% | 7.8% | 0.0% | 2.5% | 0.0% | 0.55 | 12 |
| Tesseract 5.5 tessdata + Sauvola, page language | 0.0% | 1.1% | 7.8% | 0.0% | 7.6% | 0.0% | 0.47 | 12 |
| Tesseract 5.5 tessdata_best + Sauvola, page language | 0.0% | 2.6% | 8.4% | 0.0% | 9.1% | 0.0% | 1.36 | 12 |
| Tesseract 5.5 tessdata_fast + Sauvola, eng+jpn+tha | 0.1% | 2.6% | 7.6% | 0.0% | 3.2% | 0.9% | 0.75 | 12 |
| Tesseract 5.5 tessdata_fast + Sauvola, language from script detection | 0.0% | 2.6% | 7.8% | 0.0% | 2.5% | 0.0% | 1.02 | 12 |
| Tesseract 5.5 tessdata_fast + Sauvola, script detection + eng | 0.0% | 2.6% | 7.9% | 0.0% | 3.5% | 0.0% | 1.10 | 12 |
| RapidOCR PP-OCRv5 mobile | 7.8% | 10.5% | 0.4% | 24.7% | 1.0% | 0.0% | 5.68 | 12 |
| RapidOCR PP-OCRv6 small | 7.6% | 0.2% | 1.5% | 8.1% | 0.2% | 10.7% | 7.39 | 12 |
| RapidOCR PP-OCRv6 medium | n/m | 0.0% | n/m | n/m | 0.0% | n/m | 63.69 | 2 |

## Method

- **Pages.** Six texts, two per language (`bench/texts.py`): the opening of Natsume Soseki's *I Am a Cat* and a
  Japanese invoice; two Thai paragraphs (a company notice with dates and a phone number, a paragraph about Thai
  tones); the Gettysburg Address and an English invoice. The public-domain texts are credited in `texts.py`; the rest
  were written for this benchmark.
- **Rendering** (`bench/render_samples.py`). A4 at 200 dpi (1654 x 2339 px), 12 pt, Noto Serif CJK JP for Japanese,
  TLWG Norasi for Thai, Noto Serif for English, laid out with libraqm so Thai marks stack correctly. Each page exists as
  a clean render and a degraded "scan": up to 1.2 degree skew, Gaussian blur, a lighting gradient across the page, ink
  fade, sensor noise, JPEG at quality 60. Synthetic pages give exact ground truth; they are easier than a real scanner
  in some ways (one font, no stains or bleed-through) and harder in others (heavy noise), so read the numbers as a
  comparison between engines, not as an accuracy promise for your documents.
- **CER** = Levenshtein distance / reference length, after NFKC normalization and removal of all whitespace (Japanese
  and Thai do not separate words with spaces, and line breaks depend on layout, not on the recognizer).
- **Latency.** Each engine is built once and warmed on one page per language, as the server runs it (models stay
  loaded). Each page is recognized 3 times; the median is recorded. Recognition time only, excluding image decoding.
- **Hardware.** `linux/amd64` Docker container (`bench/Dockerfile`, Python 3.12), pinned to 4 cores
  (`--cpuset-cpus=1-4`) of an Intel Core i5-10500T (Proxmox VM). ONNX Runtime 1.30.0 with default threading;
  tesserocr 2.11.0 wheel, which bundles libtesseract 5.5.1.
- **Engines.** Tesseract models from `tesseract-ocr/tessdata_fast`, `tessdata` and `tessdata_best` at tag 4.1.0.
  RapidOCR 3.9.2: PP-OCRv5 mobile (detector; `ch` recognizer for Japanese, `th` for Thai, `en` for English) and
  PP-OCRv6 small/medium (multilingual detector and recognizer for English and Japanese; PP-OCRv6 has no Thai
  recognizer, so Thai used PP-OCRv5 mobile `th` on the v6 detector).
- **PP-OCRv6 medium** was stopped after 2 of 12 pages: at 64 s per page it was out of the running on latency, and the
  full run would have taken another 35 minutes. Its two measured Japanese pages were read perfectly.
- **Raw per-page output:** `bench/results-2026-09-28.log` (every row comes from one continuous run).

## Reproduce

```bash
docker build -t anydoc-serve-bench bench/
docker run --rm --cpuset-cpus=1-4 -v $PWD/bench/out:/out anydoc-serve-bench | tee bench/out/log.txt
uv run --no-project python bench/summarize.py bench/out/log.txt
```

`BENCH_ENGINES=Sauvola` limits the run to engines whose name contains that text; `BENCH_REPEATS` sets repeats.

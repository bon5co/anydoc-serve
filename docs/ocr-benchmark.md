# OCR benchmark

Measured 2026-09-28 to pick the OCR engine anydoc-serve ships. Everything here is reproducible from `bench/`.

## Result

**Tesseract 5.5 (tesserocr 2.11), `tessdata_fast` 4.1.0 models, Sauvola binarization, every configured language in
one pass in the fixed order `jpn+tha+eng`, scan resolution passed explicitly. Script detection (OSD) is used only to
turn rotated pages upright.**

Measured through the server's own engine (`bench/server_check.py`, `bench/results-2026-09-28-server.log`), on the
same 20 pages as the table below:

| Engine | eng | jpn | tha | jpn+eng | tha+eng | jpn+tha+eng | review page | s/page, median | pages |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| anydoc-serve engine, scan | 0.3% | 12.3% | 2.6% | 3.1% | 2.8% | 4.3% | 1.7% | 1.12 | 20 |
| anydoc-serve engine, clean | 0.3% | 6.3% | 2.6% | 3.9% | 6.8% | 6.2% | 0.0% | 1.12 | 20 |

The server's median is about 0.6 s per page slower than the benchmark row it comes from (0.53 s) because it also runs
orientation detection on every page.

### Why this configuration

- **Mixed pages rule out one language per page.** The first release picked one language per page from Tesseract's
  script detection. On a page with a Japanese line and an English line it detected Latin and read the Japanese line
  as `BRB BS 2026-0928 Aste Sq 12,800`. Measured over the mixed pages, detection plus a single language scored 24.9% to
  47.6% CER, and detection plus English still scored 24.9% to 47.6% on the pages with Japanese. Reading every page with
  all configured languages removes the choice, so there is nothing to get wrong.
- **Order of the language string matters, a lot.** Tesseract treats the first language as primary. `eng+jpn+tha` read
  Thai at 81% CER (it spaced out and dropped Thai characters); `jpn+tha+eng` read the same Thai pages at 2.6%, equal to
  handing Tesseract the right language per page. Across all 20 pages, `jpn+tha+eng` without any oracle is within 0.4
  points of the page-language oracle: 3.4% against 3.0% overall, 4.3% against 3.4% on the mixed pages.
- **The weak spot is Japanese.** One of the two Japanese pages (an invoice with a large heading) scores 9.6% clean and
  22.6% as a scan: the heading `請求書` is misread, and `12,000円` becomes `12.000円`. The page-language oracle does no
  better on it (11.1% on the scans), so this is the Japanese `tessdata_fast` model, not the language mix.
- **`tessdata_fast` over `tessdata` / `tessdata_best`.** Standard `tessdata` fell apart on Thai and on mixed pages in
  every configuration measured (about 27% CER overall) and needs about twice the memory per worker (160 MiB against
  90 MiB for OSD plus the combined languages). `tessdata_best` was no more accurate than `tessdata_fast` and 2 to 3
  times slower.
- **Sauvola binarization** is still the largest single improvement: Tesseract's default global Otsu threshold lost
  half of every line on the unevenly lit Thai scan (15.1% CER); local Sauvola thresholding took it to 2.6%.

### RapidOCR (PaddleOCR models)

RapidOCR does **not** win on mixed pages. PP-OCRv6 small, the recommended configuration, scored:

- **Better on two-language pages**: jpn+eng 2.6% against 3.1 to 3.9%, tha+eng 0.0 to 0.4% against 2.8 to 6.8%;
- **Much worse on the three-script page**: 21.0% as a scan, 26.2% clean, against 4.3% and 6.2%. It runs one
  recognizer per page, and none of its recognizers reads Japanese, Thai and English together.

It also dropped or garbled lines of the English pages (7.6 to 8.1% against 0.3%). Where it clearly wins is pure
Japanese: 1.1% on the Japanese scans against 12.3% for the shipped engine.

The price is latency. PP-OCRv6 small took 3.44 s per page, 6.5 times the benchmark Tesseract row (0.53 s) and 3 times
the server as shipped (1.12 s). PP-OCRv6 medium took 34 s per page.

A Japanese-only workload that can wait 3.5 s per page would be better served by PP-OCRv6 small. It is not in v1.

## Numbers

Character error rate (CER), weighted by characters, per page group, and the median seconds per page over all
20 pages. Raw per-page output: `bench/results-2026-09-28.log`. Hypotheses: `bench/results-2026-09-28.json`
(PP-OCRv6 medium, run separately: `bench/results-2026-09-28-medium.json`).

"page language" rows are an oracle. They are handed the language of each page, which a server cannot know; the mixed
pages get their full language set in the benchmark's own order. They are the ceiling to compare against.

**Scans**

| Engine | eng | jpn | tha | jpn+eng | tha+eng | jpn+tha+eng | review page | s/page, median | pages |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract 5.5 tessdata_fast, page language | 0.3% | 7.4% | 15.1% | 3.5% | 100.0% | 100.0% | 1.7% | 0.37 | 20 |
| Tesseract 5.5 tessdata, page language | 0.0% | 19.4% | 92.5% | 8.3% | 100.0% | 96.2% | 21.7% | 0.48 | 20 |
| Tesseract 5.5 tessdata_best, page language | 0.1% | 10.6% | 14.8% | 3.9% | 100.0% | 92.9% | 1.7% | 1.10 | 20 |
| Tesseract 5.5 tessdata_fast, eng+jpn+tha | 0.2% | 15.1% | 92.2% | 4.4% | 100.0% | 100.0% | 3.3% | 0.52 | 20 |
| Tesseract 5.5 tessdata_fast + Sauvola, page language | 0.3% | 11.1% | 2.6% | 3.9% | 2.8% | 4.3% | 1.7% | 0.39 | 20 |
| Tesseract 5.5 tessdata + Sauvola, page language | 0.2% | 19.1% | 80.9% | 7.4% | 28.0% | 26.7% | 18.3% | 0.54 | 20 |
| Tesseract 5.5 tessdata_best + Sauvola, page language | 0.1% | 10.2% | 2.6% | 3.9% | 2.4% | 21.9% | 1.7% | 1.12 | 20 |
| Tesseract 5.5 tessdata_fast + Sauvola, eng+jpn+tha | 0.2% | 16.4% | 81.2% | 4.4% | 26.8% | 21.4% | 3.3% | 0.51 | 20 |
| **Tesseract 5.5 tessdata_fast + Sauvola, jpn+tha+eng (shipped)** | 0.3% | 12.6% | 2.6% | 3.9% | 2.8% | 4.3% | 1.7% | 0.53 | 20 |
| Tesseract 5.5 tessdata_fast + Sauvola, script detection, single language (v1 before this fix) | 0.3% | 11.1% | 2.6% | 26.2% | 43.2% | 45.2% | 20.0% | 1.00 | 20 |
| Tesseract 5.5 tessdata_fast + Sauvola, script detection + eng | 0.3% | 12.6% | 2.6% | 26.2% | 2.8% | 45.2% | 20.0% | 0.99 | 20 |
| Tesseract 5.5 tessdata_fast + Sauvola, script detection first, then all | 0.2% | 12.6% | 2.6% | 4.4% | 2.8% | 21.4% | 3.3% | 1.05 | 20 |
| Tesseract 5.5 tessdata + Sauvola, eng+jpn+tha | 0.5% | 23.8% | 81.0% | 10.5% | 28.0% | 24.8% | 21.7% | 0.70 | 20 |
| Tesseract 5.5 tessdata + Sauvola, script detection first, then all | 0.7% | 15.7% | 81.0% | 7.4% | 28.0% | 24.8% | 21.7% | 1.23 | 20 |
| RapidOCR PP-OCRv5 mobile | 8.0% | 11.0% | 0.6% | 2.6% | 1.2% | 22.9% | 6.7% | 3.58 | 20 |
| RapidOCR PP-OCRv6 small | 7.6% | 1.1% | 1.7% | 2.6% | 0.4% | 21.0% | 3.3% | 3.44 | 20 |
| RapidOCR PP-OCRv6 medium | 7.2% | 0.9% | 0.5% | 1.7% | 1.6% | 21.4% | 0.0% | 34.31 | 20 |

**Clean renders**

| Engine | eng | jpn | tha | jpn+eng | tha+eng | jpn+tha+eng | review page | s/page, median | pages |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract 5.5 tessdata_fast, page language | 0.2% | 6.2% | 2.6% | 3.1% | 2.8% | 6.2% | 1.7% | 0.37 | 20 |
| Tesseract 5.5 tessdata, page language | 0.1% | 19.8% | 80.9% | 10.9% | 26.8% | 26.2% | 16.7% | 0.48 | 20 |
| Tesseract 5.5 tessdata_best, page language | 0.1% | 10.4% | 2.6% | 5.7% | 4.4% | 21.9% | 1.7% | 1.10 | 20 |
| Tesseract 5.5 tessdata_fast, eng+jpn+tha | 0.2% | 23.4% | 80.9% | 3.9% | 27.2% | 21.9% | 3.3% | 0.52 | 20 |
| Tesseract 5.5 tessdata_fast + Sauvola, page language | 0.2% | 6.3% | 2.6% | 3.5% | 1.6% | 6.2% | 0.0% | 0.39 | 20 |
| Tesseract 5.5 tessdata + Sauvola, page language | 0.1% | 19.8% | 80.9% | 7.9% | 26.8% | 29.0% | 21.7% | 0.54 | 20 |
| Tesseract 5.5 tessdata_best + Sauvola, page language | 0.1% | 9.8% | 2.6% | 3.5% | 1.6% | 24.8% | 1.7% | 1.12 | 20 |
| Tesseract 5.5 tessdata_fast + Sauvola, eng+jpn+tha | 0.2% | 10.6% | 81.2% | 4.4% | 26.8% | 22.9% | 1.7% | 0.51 | 20 |
| **Tesseract 5.5 tessdata_fast + Sauvola, jpn+tha+eng (shipped)** | 0.3% | 6.3% | 2.6% | 3.9% | 6.8% | 6.2% | 0.0% | 0.53 | 20 |
| Tesseract 5.5 tessdata_fast + Sauvola, script detection, single language (v1 before this fix) | 0.2% | 6.3% | 2.6% | 24.9% | 44.0% | 47.6% | 21.7% | 1.00 | 20 |
| Tesseract 5.5 tessdata_fast + Sauvola, script detection + eng | 0.2% | 6.3% | 2.6% | 24.9% | 1.6% | 47.6% | 21.7% | 0.99 | 20 |
| Tesseract 5.5 tessdata_fast + Sauvola, script detection first, then all | 0.2% | 6.3% | 2.6% | 4.4% | 1.6% | 22.9% | 1.7% | 1.05 | 20 |
| Tesseract 5.5 tessdata + Sauvola, eng+jpn+tha | 0.3% | 18.1% | 80.9% | 13.1% | 26.8% | 21.4% | 18.3% | 0.70 | 20 |
| Tesseract 5.5 tessdata + Sauvola, script detection first, then all | 0.7% | 19.1% | 80.9% | 8.3% | 26.8% | 21.4% | 18.3% | 1.23 | 20 |
| RapidOCR PP-OCRv5 mobile | 24.4% | 1.7% | 0.2% | 1.7% | 0.0% | 23.8% | 0.0% | 3.58 | 20 |
| RapidOCR PP-OCRv6 small | 8.1% | 0.4% | 10.9% | 2.6% | 0.0% | 26.2% | 0.0% | 3.44 | 20 |
| RapidOCR PP-OCRv6 medium | 0.0% | 0.7% | 0.5% | 1.7% | 0.8% | 28.1% | 0.0% | 34.31 | 20 |

The 100% cells are rows without Sauvola that returned nothing for the Thai-bearing mixed scans.

## Method

- **Pages** (`bench/texts.py`).
  - Six single-language texts, two per language:
    - Japanese: the opening of Natsume Soseki's *I Am a Cat* and a Japanese invoice;
    - Thai: a company notice with dates and a phone number, and a paragraph about Thai tones;
    - English: the Gettysburg Address and an English invoice.
  - Three mixed pages: Japanese with English, Thai with English, and all three scripts together. Each is a short
    business document that switches script between lines and inside lines.
  - The "review page": the two-line Japanese and English image that exposed the defect (NotoSansCJK 48 px, 1400 x 300).
  - The public-domain texts are credited in `texts.py`. The rest were written for this benchmark.
- **Rendering** (`bench/render_samples.py`).
  - A4 at 200 dpi, 12 pt. Noto Serif CJK JP for Japanese and Latin, TLWG Norasi for Thai, Noto Serif for English
    pages. Laid out with libraqm so Thai marks stack correctly.
  - Each page exists as a clean render and a degraded "scan": up to 1.2 degree skew, Gaussian blur, a lighting
    gradient, ink fade, sensor noise, and JPEG at quality 60.
  - Synthetic pages give exact ground truth. Read the numbers as a comparison between engines, not as an accuracy
    promise for your documents.
- **CER** (`bench/metric.py`) = Levenshtein distance / reference length, after the normalization below.
  - Normalization steps:
    - NFC;
    - full-width ASCII folded to ASCII;
    - a line break between two non-Latin characters removed, any other line break made a space;
    - whitespace collapsed;
    - spaces between CJK characters dropped.
  - A space inside a Thai word **counts as an error**.
  - An earlier version of this benchmark used NFKC and removed all whitespace. That hid two real defects:
    - NFKC folds circled digits such as `⑳` to `20`, so a model that output them scored 0%;
    - stripping whitespace hid Thai read as spaced-out letters.
  - Every row here was re-measured with the corrected metric.
- **Latency.** Each engine is built once and warmed on every language, as the server runs it (models stay loaded).
  Each page is recognized 3 times (PP-OCRv6 medium: once) and the median is recorded. Recognition time only.
- **Hardware.** A `linux/amd64` Docker container (`bench/Dockerfile`, Python 3.12), pinned to 4 cores
  (`--cpuset-cpus=1-4`) of an Intel Core i5-10500T (Proxmox VM). ONNX Runtime 1.30.0; the tesserocr 2.11.0 wheel
  bundles libtesseract 5.5.1.
- **Engines.**
  - Tesseract models from `tesseract-ocr/tessdata_fast`, `tessdata` and `tessdata_best` at tag 4.1.0.
  - RapidOCR 3.9.2:
    - PP-OCRv5 mobile uses the `ch` recognizer for Japanese, `th` for Thai and `en` for English.
    - PP-OCRv6 small and medium use the multilingual recognizer. PP-OCRv6 has no Thai recognizer, so Thai pages
      use PP-OCRv5 mobile `th` on the v6 detector.
    - A mixed page uses the recognizer of its primary script (Thai for tha+eng, Japanese for the others).
- **Memory** per OCR worker, loaded models only: OSD plus one combined `jpn+tha+eng` with `tessdata_fast` is about
  90 MiB; standard `tessdata` is about 160 MiB.

## Reproduce

```bash
docker build -t anydoc-serve-bench bench/
mkdir -p bench/out
docker run --rm --cpuset-cpus=1-4 -u $(id -u):$(id -g) -v $PWD/bench/out:/out anydoc-serve-bench | tee bench/out/log.txt
uv run --no-project python bench/summarize.py bench/out/log.txt
```

`BENCH_ENGINES=Sauvola` limits the run to engines whose name contains that text. `BENCH_REPEATS` sets the number of
repeats.

To measure the server's own engine on the rendered samples:

```bash
docker run --rm --cpuset-cpus=1-4 -v $PWD/bench:/bench -v $PWD/bench/out:/samples anydoc-serve:dev \
  python /bench/server_check.py /samples
```

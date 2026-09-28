"""Run the benchmark pages through the server's own OCR engine (anydoc_serve.ocr).

The benchmark compares engines; this checks that the shipped configuration
(script detection, Sauvola, explicit resolution, orientation) reads the same
pages as well as the benchmark row it was chosen from. Run it in the server
image with the rendered samples mounted:

    docker run --rm --cpuset-cpus=1-4 -v $PWD/bench:/bench -v SAMPLES:/samples \
        anydoc-serve python /bench/server_check.py /samples
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import unicodedata
from pathlib import Path

from PIL import Image

from anydoc_serve.ocr import TesseractEngine


def norm(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", s) if not ch.isspace())


def cer(ref: str, hyp: str) -> float:
    """Same metric as ocr_bench.py, without rapidfuzz (not in the server image)."""
    a, b = norm(ref), norm(hyp)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] / max(1, len(a))


samples = Path(sys.argv[1])
engine = TesseractEngine(["eng", "jpn", "tha"])
engine.warm_up()
for m in json.loads((samples / "manifest.json").read_text()):
    image = Image.open(samples / m["file"])
    image.load()
    engine.recognize(image, 200)  # warm this language path
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        result = engine.recognize(image, 200)
        times.append(time.perf_counter() - t0)
    print(
        f"{'anydoc-serve engine (as shipped)':50s} {m['file']:22s} CER {cer(m['text'], result.text):.3f}  "
        f"{statistics.median(times):.2f}s  lang={result.lang}",
        flush=True,
    )

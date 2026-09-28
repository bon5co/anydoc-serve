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
from pathlib import Path

from PIL import Image

from anydoc_serve.ocr import TesseractEngine

sys.path.insert(0, str(Path(__file__).parent))
from metric import cer  # noqa: E402



samples = Path(sys.argv[1])
engine = TesseractEngine(["eng", "jpn", "tha"])
engine.warm_up()
rows = []
for m in json.loads((samples / "manifest.json").read_text()):
    image = Image.open(samples / m["file"])
    image.load()
    dpi = None if m["lang"] == "review" else 200  # untagged PNG: the server assumes 300, as here
    engine.recognize(image, dpi)  # warm this language path
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        result = engine.recognize(image, dpi)
        times.append(time.perf_counter() - t0)
    print(
        f"{'anydoc-serve engine (as shipped)':50s} {m['file']:22s} CER {cer(m['text'], result.text):.3f}  "
        f"{statistics.median(times):.2f}s  lang={result.lang}",
        flush=True,
    )
    rows.append({"file": m["file"], "lang": m["lang"], "variant": m["variant"], "text": m["text"], "hyp": result.text})
if len(sys.argv) > 2:
    Path(sys.argv[2]).write_text(json.dumps(rows, ensure_ascii=False, indent=1))

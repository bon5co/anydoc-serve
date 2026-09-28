"""Turn the benchmark log into the Markdown tables in docs/ocr-benchmark.md.

    uv run --no-project python bench/summarize.py bench/results-2026-09-28.log

Reads the per-page lines ocr_bench.py prints (engine, file, CER, seconds), so
a run stopped early still yields the rows it measured. CER per language is
weighted by the reference character count of each page.
"""

from __future__ import annotations

import re
import statistics
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from texts import PAGES  # noqa: E402

LINE = re.compile(r"^(?P<engine>.+?)\s+(?P<file>(eng|jpn|tha)-p\d-(clean|scan)\.(png|jpg))\s+CER (?P<cer>[\d.]+)\s+(?P<s>[\d.]+)s(\s+.*)?$")


def chars(file: str) -> int:
    lang, page = file[:3], int(file[5])
    return len("".join(c for c in unicodedata.normalize("NFKC", PAGES[lang][page - 1]) if not c.isspace()))


def main(path: str) -> None:
    rows: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    for line in Path(path).read_text().splitlines():
        m = LINE.match(line.strip())
        if m:
            rows[m["engine"].strip()].append((m["file"], float(m["cer"]), float(m["s"])))

    def cell(items, lang: str, variant: str) -> str:
        sel = [(f, c) for f, c, _ in items if f.startswith(lang) and variant in f]
        if not sel:
            return "n/m"
        return f"{100 * sum(c * chars(f) for f, c in sel) / sum(chars(f) for f, _ in sel):.1f}%"

    print("| Engine | eng scan | jpn scan | tha scan | eng clean | jpn clean | tha clean | s/page, median | pages |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for engine, items in rows.items():
        cells = [cell(items, lang, v) for v in ("scan", "clean") for lang in ("eng", "jpn", "tha")]
        seconds = statistics.median(s for _, _, s in items)
        print(f"| {engine} | {' | '.join(cells)} | {seconds:.2f} | {len(items)} |")


if __name__ == "__main__":
    main(sys.argv[1])

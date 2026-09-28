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
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from metric import norm  # noqa: E402
from texts import MIXED, PAGES, REVIEW_PAGE  # noqa: E402

TEXTS = {**PAGES, **MIXED, "review": ["\n".join(REVIEW_PAGE)]}
LINE = re.compile(
    r"^(?P<engine>.+?)\s+(?P<file>(?P<lang>[a-z-]+)-p(?P<page>\d)-(?P<variant>clean|scan)\.(png|jpg))"
    r"\s+CER (?P<cer>[\d.]+)\s+(?P<s>[\d.]+)s(\s+.*)?$"
)
COLUMNS = [("eng", "eng"), ("jpn", "jpn"), ("tha", "tha"), ("mix-je", "jpn+eng"), ("mix-te", "tha+eng"),
           ("mix-jte", "jpn+tha+eng"), ("review", "review page")]  # fmt: skip


def chars(lang: str, page: int) -> int:
    return len(norm(TEXTS[lang][page - 1]))


def main(path: str) -> None:
    rows: dict[str, list[re.Match[str]]] = defaultdict(list)
    for line in Path(path).read_text().splitlines():
        m = LINE.match(line.strip())
        if m and m["lang"] in TEXTS:
            rows[m["engine"].strip()].append(m)

    def cell(items: list[re.Match[str]], lang: str, variant: str) -> str:
        sel = [m for m in items if m["lang"] == lang and m["variant"] == variant]
        if not sel:
            return "n/m"
        weight = [chars(m["lang"], int(m["page"])) for m in sel]
        return f"{100 * sum(float(m['cer']) * w for m, w in zip(sel, weight)) / sum(weight):.1f}%"

    for variant in ("scan", "clean"):
        print(f"**{variant}**\n")
        print("| Engine | " + " | ".join(title for _, title in COLUMNS) + " | s/page, median | pages |")
        print("| --- |" + " ---: |" * (len(COLUMNS) + 2))
        for engine, items in rows.items():
            cells = [cell(items, lang, variant) for lang, _ in COLUMNS]
            seconds = statistics.median(float(m["s"]) for m in items)
            print(f"| {engine} | {' | '.join(cells)} | {seconds:.2f} | {len(items)} |")
        print()


if __name__ == "__main__":
    main(sys.argv[1])

"""Character error rate, shared by the benchmark, server_check and the tests.

CER = Levenshtein(reference, hypothesis) / len(reference), after:

1. NFC, then full-width ASCII forms (U+FF01-FF5E) and the ideographic space
   mapped to ASCII. Not NFKC: NFKC also folds circled digits (U+2460...) into
   plain ones, and it hid a model that wrote "12,800円" as "⑫,⑧00 円".
2. Line breaks: a break between two non-Latin characters (Thai or Japanese
   wrapped mid-word) is removed; any other break becomes a space.
3. Whitespace runs become one space, and a space with Japanese on both sides
   is removed (Japanese does not separate words; Tesseract's model emits them).

Spaces inside Thai words, which an earlier all-whitespace-removing metric
could not see, therefore count as errors.
"""

from __future__ import annotations

import re
import unicodedata

_FULLWIDTH = {cp: cp - 0xFEE0 for cp in range(0xFF01, 0xFF5F)} | {0x3000: 0x20}


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return 0x3000 <= cp <= 0x30FF or 0x3400 <= cp <= 0x9FFF or 0xF900 <= cp <= 0xFAFF or 0xFF00 <= cp <= 0xFFEF


def _non_latin(ch: str) -> bool:
    return ord(ch) > 0x2FF and not ch.isspace()


def norm(text: str) -> str:
    text = unicodedata.normalize("NFC", text).translate(_FULLWIDTH)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    out = ""
    for line in lines:
        if out:
            out += "" if _non_latin(out[-1]) and _non_latin(line[0]) else " "
        out += line
    out = re.sub(r"\s+", " ", out)
    return _drop_cjk_spaces(out)


def _drop_cjk_spaces(text: str) -> str:
    chars = list(text)
    for i in range(1, len(chars) - 1):
        if chars[i] == " " and _is_cjk(chars[i - 1]) and _is_cjk(chars[i + 1]):
            chars[i] = ""
    return "".join(chars)


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(reference: str, hypothesis: str) -> float:
    ref, hyp = norm(reference), norm(hypothesis)
    return levenshtein(ref, hyp) / max(1, len(ref))

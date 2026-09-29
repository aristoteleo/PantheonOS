"""Fetch abstracts and the first figure of each benchmark paper for benchmarks.tex.

    python fetch_benchmark_figs.py        # writes benchmarks_figs/<key>.{png,jpg} and papers.json

Figures come from arXiv's HTML rendering (x1.png / x2.png) when the paper has one; they are
third-party material kept out of git for that reason -- run this before building the PDF.
The overview map (overview_map.png) is ours and is committed.
"""
import html
import json
import os
import re
import time
import urllib.request

PAPERS = {
    "alphaevolve67": "2511.02864", "horizonmath": "2603.15617", "simpletes": "2604.19341",
    "alebench": "2506.09050", "algotune": "2507.15887", "frontiereng": "2604.12290",
    "kernelbenchx": "2605.04956", "metalsci": "2605.09708", "evotrace": "2605.20086",
    "mlebench": "2410.07095", "llm4ad": "2412.17287", "codeevolve": "2510.14150",
    "kernelbench": "2502.10517", "formalconj": "2605.13171",
}
UA = {"User-Agent": "Mozilla/5.0 (benchmark survey)"}


def get(url, binary=False):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read() if binary else r.read().decode("utf-8", "replace")


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    figs = os.path.join(here, "benchmarks_figs")
    os.makedirs(figs, exist_ok=True)
    out = {}
    for key, aid in PAPERS.items():
        rec = {"id": aid}
        try:
            page = get(f"https://arxiv.org/abs/{aid}")
            t = re.search(r'<meta name="citation_title" content="(.*?)"', page)
            a = re.search(r'<meta name="citation_abstract" content="(.*?)"', page, re.S)
            d = re.search(r'<meta name="citation_date" content="(.*?)"', page)
            rec["title"] = html.unescape(t.group(1)) if t else "?"
            rec["abstract"] = html.unescape(a.group(1)).strip() if a else "?"
            rec["date"] = d.group(1) if d else "?"
        except Exception as e:  # noqa: BLE001
            rec["error"] = str(e)[:80]
        for v in ("", "v1", "v2"):
            for fig in ("x1.png", "x2.png", "x1.jpg"):
                try:
                    data = get(f"https://arxiv.org/html/{aid}{v}/{fig}", binary=True)
                except Exception:  # noqa: BLE001
                    continue
                if len(data) > 15000 and (data[:4] == b"\x89PNG" or data[:3] == b"\xff\xd8\xff"):
                    ext = "png" if data[:4] == b"\x89PNG" else "jpg"
                    open(os.path.join(figs, f"{key}.{ext}"), "wb").write(data)
                    rec["figure"] = f"benchmarks_figs/{key}.{ext}"
                    break
            if "figure" in rec:
                break
            time.sleep(0.5)
        out[key] = rec
        print(f"{key:14s} {aid}  fig={'yes' if 'figure' in rec else 'no '}  {rec.get('title', '?')[:70]}")
        time.sleep(1.0)
    json.dump(out, open(os.path.join(figs, "papers.json"), "w"), indent=1)

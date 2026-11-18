#!/usr/bin/env python3
"""Deterministic benchmark for the markdownify pre-collapse ancestor check.

Baseline vs optimized are the SAME source tree; the only difference is that
`bench.py --impl baseline` reverse-applies `patch.diff` to a scratch copy of
the `markdownify/` package before importing it, while `--impl optimized`
imports the tree as committed.

Each scale is measured with `time.perf_counter()`: 3 warmups then 5 timed
repetitions. Every timed sample is written to `benchmark/raw/<impl>-<scale>-<i>.json`.
Aggregates (median + p25/p75 + relative stdev) are written to
`benchmark/<impl>.json`.

Corpus
------
- small / medium / large: real HTML documents already shipped on this machine
  (system documentation). These are real input files, not generated noise.
- fixtures: the string literals that the upstream test-suite uses as its HTML
  inputs, extracted from `tests/test_*.py` with `ast` (i.e. upstream's own real
  fixtures).
"""

import argparse
import ast
import hashlib
import json
import pathlib
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from resource import getrusage, RUSAGE_SELF

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEED = 20261119
WARMUP = 3
REPS = 5

CORPUS_DOCS = {
    "small": "/usr/share/doc/nodejs/api/console.html",
    "medium": "/usr/share/doc/libexpat1-dev/expat.html/reference.html",
    "large": "/usr/share/doc/libjs-underscore/index.html",
}

SCALES = ["small", "medium", "large", "fixtures"]


def read_text(path):
    return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")


def fixture_corpus():
    """Collect the HTML string literals the upstream tests actually feed in."""
    parts = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "<" in node.value:
                parts.append(node.value)
    return "\n".join(parts)


def load_corpus():
    corpus = {}
    for scale, path in CORPUS_DOCS.items():
        src = path if pathlib.Path(path).is_file() else None
        corpus[scale] = {
            "source": src or "missing",
            "sha256": hashlib.sha256(read_text(src).encode("utf-8")).hexdigest() if src else "",
            "html": read_text(src) if src else "",
        }
    fx = fixture_corpus()
    corpus["fixtures"] = {
        "source": "upstream tests/test_*.py string literals (ast-extracted)",
        "sha256": hashlib.sha256(fx.encode("utf-8")).hexdigest(),
        "html": fx,
    }
    return corpus


def build_impl(impl):
    """Return (markdownify module, scratch dir). Baseline reverse-applies the patch."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="mdbench-%s-" % impl))
    shutil.copytree(ROOT / "markdownify", tmp / "markdownify")
    if impl == "baseline":
        patch = (ROOT / "patch.diff").read_bytes()
        proc = subprocess.run(
            ["patch", "-R", "-p1", "-d", str(tmp)],
            input=patch,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if proc.returncode != 0:
            # Dependency-free fallback: revert the exact edited line.
            target = tmp / "markdownify" / "__init__.py"
            text = target.read_text(encoding="utf-8")
            text = text.replace(
                "        # parent_tags already carries every ancestor tag name, so this is\n"
                "        # equivalent to find_parent('pre') without re-walking the tree per node.\n"
                "        if node.name == 'pre' or 'pre' in parent_tags:",
                "        if node.name == 'pre' or node.find_parent('pre'):",
            )
            target.write_text(text, encoding="utf-8")
    sys.path.insert(0, str(tmp))
    import markdownify

    return markdownify, tmp


def environment():
    info = {"python": sys.version.split()[0], "seed": SEED, "warmup": WARMUP, "reps": REPS}
    try:
        lines = subprocess.run(["lscpu"], stdout=subprocess.PIPE, text=True).stdout.splitlines()
        info["cpu_model"] = next(
            (line.split(": ", 1)[1].strip() for line in lines if line.startswith("Model name:")), "unknown"
        )
    except Exception:
        info["cpu_model"] = "unknown"
    try:
        meminfo = pathlib.Path("/proc/meminfo").read_text(encoding="utf-8")
        info["memory_kb"] = next(
            int(line.split()[1]) for line in meminfo.splitlines() if line.startswith("MemTotal:")
        )
    except Exception:
        info["memory_kb"] = None
    info["platform"] = sys.platform
    return info


def aggregate(samples_ms):
    ordered = sorted(samples_ms)
    q25, median, q75 = statistics.quantiles(ordered, n=4, method="inclusive")
    mean = statistics.fmean(ordered)
    return {
        "n": len(ordered),
        "median_ms": median,
        "p25_ms": q25,
        "p75_ms": q75,
        "mean_ms": mean,
        "rel_stdev": statistics.pstdev(ordered) / mean if mean else 0.0,
        "samples_ms": ordered,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", choices=["baseline", "optimized"], required=True)
    ap.add_argument("--out-dir", default=str(ROOT / "benchmark"))
    args = ap.parse_args()

    out = pathlib.Path(args.out_dir)
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    module, tmp = build_impl(args.impl)
    corpus = load_corpus()

    results = {}
    for scale in SCALES:
        entry = corpus[scale]
        html = entry["html"]
        for _ in range(WARMUP):
            module.markdownify(html)
        samples = []
        for i in range(REPS):
            t0 = time.perf_counter()
            module.markdownify(html)
            t1 = time.perf_counter()
            ms = (t1 - t0) * 1000.0
            samples.append(ms)
            sample = {
                "impl": args.impl,
                "scale": scale,
                "index": i,
                "ms": ms,
                "bytes": len(html.encode("utf-8")),
                "corpus_source": entry["source"],
                "corpus_sha256": entry["sha256"],
            }
            with open(raw_dir / ("%s-%s-%d.json" % (args.impl, scale, i)), "w") as fh:
                json.dump(sample, fh, indent=2)
        results[scale] = aggregate(samples)

    doc = {
        "impl": args.impl,
        "command": "python3 benchmark/bench.py --impl %s" % args.impl,
        "environment": environment(),
        "patch": "patch.diff (%s)" % ("applied" if args.impl == "optimized" else "reverse-applied"),
        "scales": results,
        "peak_rss_kb": getrusage(RUSAGE_SELF).ru_maxrss,
    }
    with open(out / (args.impl + ".json"), "w") as fh:
        json.dump(doc, fh, indent=2)

    shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps({k: {kk: round(vv, 4) if isinstance(vv, float) else vv for kk, vv in v.items() if kk != "samples_ms"} for k, v in results.items()}, indent=2))
    print("peak_rss_kb=%d" % getrusage(RUSAGE_SELF).ru_maxrss)


if __name__ == "__main__":
    main()

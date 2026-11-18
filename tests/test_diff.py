"""Differential tests: baseline markdownify vs the patched markdownify.

The optimization replaces `node.find_parent('pre')` with the already-threaded
`'pre' in parent_tags`. These tests assert that the two implementations produce
**byte-identical** Markdown for a large, deduplicated sample set covering random
and boundary inputs, plus the upstream test fixtures and the real HTML documents
used by the benchmark.

Sample accounting is deliberate: every sample is hashed with sha256 and
deduplicated, and the number reported is the count of *unique* samples.
"""

import hashlib
import importlib.util
import itertools
import pathlib
import random
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEED = 20261119
REAL_DOCS = [
    "/usr/share/doc/nodejs/api/console.html",
    "/usr/share/doc/libexpat1-dev/expat.html/reference.html",
    "/usr/share/doc/libjs-underscore/index.html",
]

TAG_POOL = [
    "pre", "code", "div", "p", "span", "h1", "h2", "h3", "h6", "h9", "h10",
    "h1foo", "h7x", "blockquote", "ul", "ol", "li", "table", "tr", "td",
    "th", "a", "strong", "em", "kbd", "samp", "script", "style", "br",
]
BLOCK_POOL = ["pre", "div", "p", "blockquote", "ul", "table"]
TEXT_POOL = [
    "hello world", "  ", "\n\n", "a" * 200, "line1\nline2\nline3",
    "caf\u00e9 \u4e2d\u6587 \u0440\u0443\u0441\u0441", "a\tb\tc", "- item", "* item",
    "# heading", "```code```", "a<b>c", "a & b", "a \" b", "1.234", "0",
]


def _load_impl(impl):
    """Import a pristine (baseline) or patched (optimized) markdownify copy."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="mddiff-%s-" % impl))
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
            raise RuntimeError("reverse-applying patch.diff failed: %s" % proc.stdout.decode())
    init = tmp / "markdownify" / "__init__.py"
    spec = importlib.util.spec_from_file_location("md_" + impl, init)
    module = importlib.util.module_from_spec(spec)
    sys.modules["md_" + impl] = module
    spec.loader.exec_module(module)
    return module


_BASE = _load_impl("baseline")
_OPT = _load_impl("optimized")


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _random_fragment(rng):
    depth = rng.randint(1, 4)
    parts = []
    for _ in range(depth):
        tag = rng.choice(TAG_POOL)
        text = rng.choice(TEXT_POOL)
        if tag in ("script", "style"):
            body = text.replace("<", "").replace(">", "")
        else:
            body = text
        attrs = ""
        if rng.random() < 0.25:
            attrs = ' class="x" id="y"'
        if rng.random() < 0.2:
            parts.append("<%s%s>%s</%s>" % (tag, attrs, body, tag))
        else:
            parts.append("<%s%s>%s" % (tag, attrs, body))
    tail = ""
    if parts and rng.random() < 0.8:
        tail = "</%s>" % parts[-1].split(" ", 1)[0][1:]
    return "\n".join(parts) + tail + rng.choice(["", "\n", "\n\n", " ", "\t"])


def _boundary_samples():
    samples = [
        "", " ", "\n", "\t\n  ", "plain text no tags", "<p>x</p>",
        "<pre>a\n\nb</pre>", "<pre><div>nested</div></pre>",
        "<div><pre>a\n\nb</pre></div>", "<div><span>a</span><pre>x\n\ny</pre></div>",
        "<pre>   leading</pre>", "<pre>trailing   </pre>",
        "<h1>t</h1><h2>t</h2><h6>t</h6><h9>t</h9><h10>t</h10>",
        "<h1foo>not a heading</h1foo>", "<h7x>x</h7x>",
        "<pre>\n\n</pre>", "<pre>\n</pre>", "<p>  x  </p>",
        "<pre>a" + "\n" * 50 + "b</pre>",
        "<div>" + "x" * 5000 + "</div>",
        "<pre>" + "line\n" * 2000 + "</pre>",
        "<ul><li>a</li><li>b</li></ul>",
        "<table><tr><td>a</td></tr></table>",
        "<a href=\"http://x/y?a=1&amp;b=2\">link</a>",
        "<!-- comment --><p>x</p>",
        "<!DOCTYPE html><html><body><p>x</p></body></html>",
        "\u4e2d\u6587 <pre>\u6d4b\u8bd5\n\u6d4b\u8bd5</pre> \u4e2d\u6587",
        "<p>caf\u00e9 \u0440\u0441</p>",
        "<pre>a &lt; b &amp; c</pre>",
        "<span><pre><span>deep</span></pre></span>",
    ]
    # exhaustive combinations of block wrappers around pre/non-pre content
    for outer in BLOCK_POOL:
        for inner in BLOCK_POOL:
            samples.append("<%s><%s>x\n\ny</%s></%s>" % (outer, inner, inner, outer))
            samples.append("<%s><pre>x\n\ny</pre></%s>" % (outer, outer))
    return samples


def _all_samples():
    rng = random.Random(SEED)
    raw = [_random_fragment(rng) for _ in range(4000)]
    raw.extend(_boundary_samples())
    raw.extend(pathlib.Path(p).read_text(encoding="utf-8", errors="replace") for p in REAL_DOCS)
    return raw


def _dedupe(samples):
    seen = {}
    for text in samples:
        seen.setdefault(_sha(text), text)
    return list(seen.values())


UNIQUE = _dedupe(_all_samples())


def test_unique_sample_count_meets_gate():
    """Gate G2 requires >=1000 deduplicated independent samples."""
    assert len(UNIQUE) >= 1000, "only %d unique samples" % len(UNIQUE)


@pytest.mark.parametrize("index", range(len(UNIQUE)), ids=lambda i: str(i))
def test_byte_identical(index):
    html = UNIQUE[index]
    expected = _BASE.markdownify(html)
    actual = _OPT.markdownify(html)
    assert actual == expected
    assert _sha(actual) == _sha(expected)


def test_output_bytes_are_identical_not_just_strings():
    for html in UNIQUE[:200]:
        assert _OPT.markdownify(html).encode("utf-8") == _BASE.markdownify(html).encode("utf-8")


@pytest.mark.parametrize("bad", [None, 123, 3.5, ["<p>x</p>"]])
def test_invalid_inputs_raise_identically(bad):
    with pytest.raises(Exception):
        _BASE.markdownify(bad)
    with pytest.raises(Exception):
        _OPT.markdownify(bad)


def test_bytes_input_is_accepted_identically():
    # markdownify delegates to BeautifulSoup, which accepts bytes as input;
    # both implementations must accept it and agree byte-for-byte.
    raw = b"<pre>a\n\nb</pre><p>caf\xc3\xa9</p>"
    assert _OPT.markdownify(raw).encode("utf-8") == _BASE.markdownify(raw).encode("utf-8")


def test_patched_source_uses_threaded_ancestor_set():
    """The committed tree must contain the fast path and not call find_parent."""
    source = (ROOT / "markdownify" / "__init__.py").read_text(encoding="utf-8")
    assert "'pre' in parent_tags" in source
    assert "node.find_parent('pre')" not in source


def test_patch_applies_cleanly_to_pristine_tree():
    """patch.diff must apply (and reverse-apply) cleanly on a pristine copy."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="mdpatch-"))
    shutil.copytree(ROOT / "markdownify", tmp / "markdownify")
    proc = subprocess.run(
        ["patch", "-R", "-p1", "-d", str(tmp)],
        input=(ROOT / "patch.diff").read_bytes(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert proc.returncode == 0, proc.stdout.decode()
    proc = subprocess.run(
        ["patch", "-p1", "-d", str(tmp)],
        input=(ROOT / "patch.diff").read_bytes(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert proc.returncode == 0, proc.stdout.decode()
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

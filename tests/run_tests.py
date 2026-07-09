#!/usr/bin/env python3
"""
Zero-dependency test runner for Corner's core logic.

There's no Node.js in this project (it's a deliberately single-file,
no-build-step app) and none was available to install in this environment,
so this runs the app's ACTUAL functions -- extracted verbatim from
corner.html, not reimplemented -- inside macOS's built-in JavaScriptCore
engine (via `osascript -l JavaScript`). That keeps tests exercising the
real code so they actually catch regressions, without adding a runtime
dependency to the project.

Usage:
    python3 tests/run_tests.py

Exits 0 if every test passes, 1 otherwise (and prints which ones failed).
Add a new test file by dropping a tests/*.test.js next to this file and
listing it in TEST_FILES below.
"""
import os
import re
import subprocess
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HTML_PATH = os.path.join(ROOT, "corner.html")

TEST_FILES = [
    "combo-picker.test.js",
    "timer.test.js",
    "smart-builder.test.js",
]

# Every const/function from corner.html the test files are allowed to call.
# Extracted verbatim (not retyped) so tests run against the real source.
REQUIRED_NAMES = [
    "BUILTIN", "ROUND_TYPES", "PACE_GAP", "ROUND_CATEGORY_CAPS",
    "FREQ_HIGH_BEG", "FREQ_LOW_BEG", "FREQ_HIGH_INT", "FREQ_LOW_INT",
    "FREQ_HIGH_ADV", "FREQ_LOW_ADV",
    "VARIANT_OBJ",
    "getComboCategories", "getFreqMult", "pickComboFromPool", "trackCombo",
    "tokenizeCombo", "mirrorForSouthpaw",
    "intervalForCombo", "countPunches", "computeGapAfterSpeech", "tplDuration",
    "getVariantObj", "parseSmartSession",
]


def read_html():
    with open(HTML_PATH, encoding="utf-8") as f:
        return f.read()


def extract_const(html, name):
    """Extract `const NAME = ...;` (object/array literal) verbatim, brace-matched."""
    m = re.search(r"const\s+" + re.escape(name) + r"\s*=\s*", html)
    if not m:
        raise RuntimeError(f"couldn't find const {name}")
    i = m.end()
    opener = html[i]
    if opener not in "{[":
        # simple literal, e.g. a number/string -- just take to the semicolon
        j = html.index(";", i)
        return html[m.start():j + 1]
    closer = "}" if opener == "{" else "]"
    depth = 0
    j = i
    while j < len(html):
        if html[j] == opener:
            depth += 1
        elif html[j] == closer:
            depth -= 1
            if depth == 0:
                j += 1
                break
        j += 1
    # allow a trailing `;` and also handle an immediately-invoked patch fn right after
    # (e.g. the (function patchFreq(){...})(); block for FREQ tags)
    end = j
    if html[end:end + 1] == ";":
        end += 1
    return html[m.start():end]


def extract_function(html, name):
    """Extract `function NAME(...){...}` verbatim, brace-matched."""
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\(", html)
    if not m:
        raise RuntimeError(f"couldn't find function {name}")
    i = html.index("{", m.start())
    depth = 0
    j = i
    while j < len(html):
        if html[j] == "{":
            depth += 1
        elif html[j] == "}":
            depth -= 1
            if depth == 0:
                j += 1
                break
        j += 1
    return html[m.start():j]


def extract_patch_freq(html):
    """The IIFE right after the FREQ_* sets that assigns c.freq on BUILTIN entries."""
    start = html.index("(function patchFreq(){")
    depth = 0
    j = start
    # walk to the matching close of the IIFE's outer parens: (function(){...})();
    paren_depth = 0
    started = False
    while j < len(html):
        ch = html[j]
        if ch == "(":
            paren_depth += 1
            started = True
        elif ch == ")":
            paren_depth -= 1
            if started and paren_depth == 0:
                j += 1
                break
        j += 1
    if html[j:j + 1] == ";":
        j += 1
    return html[start:j]


def build_app_source(html):
    parts = []
    for name in REQUIRED_NAMES:
        if name in ("getComboCategories", "getFreqMult", "pickComboFromPool",
                     "trackCombo", "tokenizeCombo", "mirrorForSouthpaw",
                     "intervalForCombo", "countPunches", "computeGapAfterSpeech",
                     "tplDuration", "getVariantObj", "parseSmartSession"):
            parts.append(extract_function(html, name))
        else:
            parts.append(extract_const(html, name))
    parts.append(extract_patch_freq(html))
    # pickComboFromPool/trackCombo read/write module-level `state` and `favCombos`
    parts.append("let state = {};")
    parts.append("let favCombos = new Set();")
    # countdownAudio-style gap math (computeGapAfterSpeech) references ROUND_TYPES[r.type].pace
    return "\n\n".join(parts)


ASSERT_LIB = """
const __results = [];
let __currentSuite = '';
function suite(name, fn){ __currentSuite = name; fn(); __currentSuite = ''; }
function test(name, fn){
  const full = __currentSuite ? __currentSuite + ' > ' + name : name;
  try { fn(); __results.push({name: full, pass: true}); }
  catch(e){ __results.push({name: full, pass: false, error: String(e && e.message || e)}); }
}
function assert(cond, msg){ if(!cond) throw new Error(msg || 'assertion failed'); }
function assertEqual(a, b, msg){
  const same = JSON.stringify(a) === JSON.stringify(b);
  if(!same) throw new Error((msg ? msg + ' -- ' : '') + 'expected ' + JSON.stringify(b) + ' but got ' + JSON.stringify(a));
}
function assertClose(a, b, tol, msg){
  if(Math.abs(a - b) > tol) throw new Error((msg ? msg + ' -- ' : '') + 'expected ' + a + ' to be within ' + tol + ' of ' + b);
}
function assertInRange(v, lo, hi, msg){
  if(v < lo || v > hi) throw new Error((msg ? msg + ' -- ' : '') + 'expected ' + v + ' to be in [' + lo + ', ' + hi + ']');
}
"""


def main():
    html = read_html()
    app_source = build_app_source(html)

    test_source = ""
    for fname in TEST_FILES:
        path = os.path.join(HERE, fname)
        with open(path, encoding="utf-8") as f:
            test_source += f"\n\n// ==== {fname} ====\n" + f.read()

    full = "\n\n".join([app_source, ASSERT_LIB, test_source, "JSON.stringify(__results);"])

    tmp_path = os.path.join(HERE, "_bundle.generated.js")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(full)

    osa_script = f'''
ObjC.import("Foundation");
var str = $.NSString.stringWithContentsOfFileEncodingError("{tmp_path}", $.NSUTF8StringEncoding, null);
try {{
  console.log(eval(str.js));
}} catch(e) {{
  console.log("__HARNESS_ERROR__ " + e);
}}
'''
    proc = subprocess.run(["osascript", "-l", "JavaScript", "-e", osa_script],
                           capture_output=True, text=True)
    os.remove(tmp_path)

    # osascript's console.log surfaces on stderr in some environments and
    # stdout in others -- whichever stream actually has our JSON, use it.
    out = proc.stdout.strip() or proc.stderr.strip()
    if proc.returncode != 0 or out.startswith("__HARNESS_ERROR__") or not out:
        print("FATAL: test harness itself failed to run.")
        print("stdout:", proc.stdout)
        print("stderr:", proc.stderr)
        sys.exit(1)

    try:
        results = json.loads(out)
    except json.JSONDecodeError:
        print("FATAL: couldn't parse test results as JSON.")
        print("raw output:", out)
        sys.exit(1)

    passed = [r for r in results if r["pass"]]
    failed = [r for r in results if not r["pass"]]

    for r in results:
        mark = "PASS" if r["pass"] else "FAIL"
        print(f"  [{mark}] {r['name']}")
        if not r["pass"]:
            print(f"         {r['error']}")

    print(f"\n{len(passed)}/{len(results)} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()

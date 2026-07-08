#!/usr/bin/env python3
"""
One-time (and re-runnable) audio generation for Corner.

Parses corner.html for:
  1. Every combo "n" string in BUILTIN + UNLOCKS -> tokenizes into a small
     reusable vocabulary of numbers ("1".."6") and phrases ("to the body",
     "slip inside", "pivot off lead foot", ...). Each unique token gets ONE
     ElevenLabs clip, silence-trimmed, saved to audio/chunks/<slug>.wav.
  2. Every full, non-combo line the app can speak (coach lines, round
     objectives, "Rest.", "Session complete. Good work.", "Resuming. Round
     N.") -> generated as natural full-sentence clips, saved to
     audio/sentences/<slug>.wav.

Then writes/updates the AUDIO_MANIFEST block inside corner.html (between the
AUDIO_MANIFEST:BEGIN/END markers) so the app can look up token/sentence text
-> file path at runtime. Safe to re-run: skips any file that already exists
unless --force is passed, so adding new combos later only pays for the new
clips.

Usage:
  ELEVENLABS_API_KEY=xxxx python3 audio/gen_audio.py            # generate
  ELEVENLABS_API_KEY=xxxx python3 audio/gen_audio.py --dry-run  # list only, no API calls, no file writes
  ELEVENLABS_API_KEY=xxxx python3 audio/gen_audio.py --force    # regenerate everything
"""
import os
import re
import sys
import json
import time
import wave
import argparse
import urllib.request
import urllib.error
from array import array

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HTML_PATH = os.path.join(ROOT, "corner.html")
CHUNKS_DIR = os.path.join(HERE, "chunks")
SENTENCES_DIR = os.path.join(HERE, "sentences")
MANIFEST_PATH = os.path.join(HERE, "manifest.json")

VOICE_ID = "qf1LWJrQbfWSvKZYhcQN"
SAMPLE_RATE = 24000
OUTPUT_FORMAT = "pcm_24000"

CHUNK_MODEL = "eleven_turbo_v2_5"
CHUNK_VOICE_SETTINGS = {"stability": 0.7, "similarity_boost": 0.8, "style": 0.15, "use_speaker_boost": True}

SENTENCE_MODEL = "eleven_multilingual_v2"
SENTENCE_VOICE_SETTINGS = {"stability": 0.45, "similarity_boost": 0.75, "style": 0.25, "use_speaker_boost": True}

# Extra digit chunks not present in any combo string but needed for the
# in-workout countdown ("10", and "4" which no BUILTIN combo happens to use
# standalone) -- reusing the same chunk player as combo numbers.
EXTRA_DIGIT_CHUNKS = ["4", "10"]


# ================= PARSE corner.html =================
def read_html():
    with open(HTML_PATH, encoding="utf-8") as f:
        return f.read()


def block(html, start_marker, end_marker):
    i = html.index(start_marker)
    j = html.index(end_marker, i)
    return html[i:j]


def extract_n_fields(text):
    return re.findall(r'n\s*:\s*"([^"]*)"', text)


def extract_quoted_strings(text):
    return re.findall(r'"([^"]*)"', text)


def extract_objectives(text):
    return re.findall(r'objective\s*:\s*"([^"]*)"', text)


def tokenize_combo(s):
    """Must stay in sync with tokenizeCombo() in corner.html."""
    tokens = []
    for seg in s.split(","):
        seg = seg.strip()
        if not seg:
            continue
        m = re.match(r"^([1-6](?:-[1-6])*)(?:\s+(.+))?$", seg)
        if m:
            tokens.extend(m.group(1).split("-"))
            if m.group(2):
                tokens.append(m.group(2).strip())
        else:
            tokens.append(seg)
    return tokens


def slugify(text):
    """Must stay in sync with slugifyText() in corner.html."""
    s = text.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def collect_vocab(html):
    builtin_block = block(html, "const BUILTIN = {", "/* ================= ROUND TYPES")
    unlocks_block = block(html, "const UNLOCKS = [", "function unlockedFor")
    variant_block = block(html, "const VARIANT_OBJ = {", "function getVariantObj")
    roundtypes_block = block(html, "const ROUND_TYPES = {", "/* Each pace band")
    coachlines_block = block(html, "const COACH_LINES = {", "const COACH_TEST")
    coachtest_block = block(html, "const COACH_TEST", "\n")

    combo_ns = sorted(set(extract_n_fields(builtin_block) + extract_n_fields(unlocks_block)))

    chunk_vocab = set(EXTRA_DIGIT_CHUNKS)
    for n in combo_ns:
        chunk_vocab.update(tokenize_combo(n))

    sentences = set()
    sentences.update(extract_quoted_strings(coachtest_block))
    sentences.update(extract_quoted_strings(coachlines_block))
    sentences.add("Rest.")
    sentences.add("Session complete. Good work.")
    sentences.add("Cool-down. Half speed, light hands, deep breaths.")
    # Round-start phrases are atomic (no "Round N." prefix baked in) so any
    # of them can follow ANY round number -- see speakGeneratedOrFallback /
    # the tick()/beginPreRoll round-intro sequencing in corner.html.
    sentences.update(extract_quoted_strings(variant_block))
    sentences.update(extract_objectives(roundtypes_block))
    for i in range(1, 21):
        sentences.add(f"Resuming. Round {i}.")
        sentences.add(f"Round {i}.")

    return combo_ns, sorted(chunk_vocab), sorted(sentences)


# ================= ELEVENLABS =================
def tts_pcm(text, model_id, voice_settings, api_key):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}?output_format={OUTPUT_FORMAT}"
    payload = json.dumps({
        "text": text,
        "model_id": model_id,
        "voice_settings": voice_settings,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("xi-api-key", api_key)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "audio/*")
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code in (401, 403):
                print(f"\nFATAL: ElevenLabs auth error ({e.code}): {body}", file=sys.stderr)
                sys.exit(1)
            last_err = f"HTTP {e.code}: {body}"
        except Exception as e:
            last_err = str(e)
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed after retries: {last_err}")


# ================= AUDIO PROCESSING =================
def trim_silence(samples, lead_pad_ms=90, trail_pad_ms=90, fade_ms=5, sample_rate=SAMPLE_RATE, rel_threshold=0.02, min_threshold=180, lead_in_ms=55):
    """Threshold is relative to the clip's own peak (not a fixed absolute
    level) so quieter recordings still get an accurate onset. A soft leading
    consonant (the "w" in "one") can sit well under a fixed threshold for
    50-80ms, so a generous lead_pad_ms is what actually prevents chopping
    into the word itself -- this used to be 40ms/threshold=400, which was
    clipping straight into the vowel on short number clips.

    Very short single-word inputs (a bare "1", "6"...) sometimes come back
    from ElevenLabs with NO natural lead-in at all -- the waveform is
    already near full amplitude at sample 0, so there's no silence for
    lead_pad_ms to recover and the word sounds like it starts mid-attack. A
    fixed silent lead-in is prepended unconditionally so every clip gets a
    clean onset regardless of how the source was rendered.

    trail_pad_ms is kept separate from lead_pad_ms because combo chunks
    (numbers/phrases meant to be stitched back-to-back) want a snappy tail
    -- a modifier phrase like "to the body" was recorded as its own little
    standalone utterance, complete with a natural sentence-final settle at
    the end, which reads as a dead-air gap once it's glued after a number
    mid-combo. Sentences keep a longer, more natural trail."""
    n = len(samples)
    if n == 0:
        return samples
    peak = max((abs(s) for s in samples), default=0)
    threshold = max(min_threshold, int(peak * rel_threshold))
    lead_pad = int(sample_rate * lead_pad_ms / 1000)
    trail_pad = int(sample_rate * trail_pad_ms / 1000)
    fade = max(1, int(sample_rate * fade_ms / 1000))

    start = 0
    while start < n and abs(samples[start]) < threshold:
        start += 1
    end = n
    while end > start and abs(samples[end - 1]) < threshold:
        end -= 1

    if start >= end:
        return samples  # essentially all silence -- keep original rather than emit empty audio

    start = max(0, start - lead_pad)
    end = min(n, end + trail_pad)

    trimmed = array("h", samples[start:end])
    for i in range(min(fade, len(trimmed))):
        trimmed[i] = int(trimmed[i] * (i / fade))
    for i in range(min(fade, len(trimmed))):
        idx = len(trimmed) - 1 - i
        trimmed[idx] = int(trimmed[idx] * (i / fade))

    lead_in = array("h", [0]) * int(sample_rate * lead_in_ms / 1000)
    return lead_in + trimmed


def write_wav(path, samples, sample_rate=SAMPLE_RATE):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())


def generate_clip(text, out_path, model_id, voice_settings, api_key, trail_pad_ms=90):
    pcm_bytes = tts_pcm(text, model_id, voice_settings, api_key)
    samples = array("h")
    samples.frombytes(pcm_bytes[: len(pcm_bytes) - (len(pcm_bytes) % 2)])
    trimmed = trim_silence(samples, trail_pad_ms=trail_pad_ms)
    write_wav(out_path, trimmed)
    return len(trimmed) / SAMPLE_RATE


# ================= MANIFEST INJECTION =================
MANIFEST_BEGIN = "/* AUDIO_MANIFEST:BEGIN (auto-generated by audio/gen_audio.py -- do not hand-edit) */"
MANIFEST_END = "/* AUDIO_MANIFEST:END */"


def patch_html_manifest(html, chunk_map, sentence_map):
    manifest_js = (
        MANIFEST_BEGIN + "\n"
        + "const AUDIO_MANIFEST = " + json.dumps({"chunks": chunk_map, "sentences": sentence_map}, indent=2) + ";\n"
        + MANIFEST_END
    )
    if MANIFEST_BEGIN in html:
        i = html.index(MANIFEST_BEGIN)
        j = html.index(MANIFEST_END, i) + len(MANIFEST_END)
        return html[:i] + manifest_js + html[j:]
    else:
        marker = "<script>\n/* ================= COMBO LIBRARY ================= */"
        if marker not in html:
            raise RuntimeError("could not find injection point in corner.html (expected COMBO LIBRARY marker)")
        return html.replace(marker, "<script>\n" + manifest_js + "\n\n/* ================= COMBO LIBRARY ================= */", 1)


# ================= MAIN =================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="list vocab/sentences and exit, no API calls")
    ap.add_argument("--force", action="store_true", help="regenerate every clip even if the file already exists")
    args = ap.parse_args()

    html = read_html()
    combo_ns, chunk_vocab, sentences = collect_vocab(html)

    print(f"Combo 'n' strings parsed: {len(combo_ns)}")
    print(f"Unique chunk tokens:      {len(chunk_vocab)}")
    print(f"Unique full sentences:    {len(sentences)}")

    if args.dry_run:
        print("\n-- chunks --")
        for t in chunk_vocab:
            print(" ", repr(t), "->", slugify(t) + ".wav")
        print("\n-- sentences --")
        for s in sentences:
            print(" ", repr(s), "->", slugify(s) + ".wav")
        return

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        print("FATAL: set ELEVENLABS_API_KEY in the environment before running.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(CHUNKS_DIR, exist_ok=True)
    os.makedirs(SENTENCES_DIR, exist_ok=True)

    chunk_map = {}
    sentence_map = {}
    generated, skipped, failed = 0, 0, 0

    print("\nGenerating chunks...")
    for tok in chunk_vocab:
        slug = slugify(tok)
        rel = f"audio/chunks/{slug}.wav"
        out_path = os.path.join(CHUNKS_DIR, f"{slug}.wav")
        chunk_map[slug] = rel
        if os.path.exists(out_path) and not args.force:
            skipped += 1
            continue
        try:
            dur = generate_clip(tok, out_path, CHUNK_MODEL, CHUNK_VOICE_SETTINGS, api_key, trail_pad_ms=30)
            print(f"  [{dur:5.2f}s] {tok!r} -> {rel}")
            generated += 1
            time.sleep(0.25)
        except Exception as e:
            print(f"  FAILED {tok!r}: {e}", file=sys.stderr)
            failed += 1

    print("\nGenerating sentences...")
    for text in sentences:
        slug = slugify(text)
        rel = f"audio/sentences/{slug}.wav"
        out_path = os.path.join(SENTENCES_DIR, f"{slug}.wav")
        sentence_map[slug] = rel
        if os.path.exists(out_path) and not args.force:
            skipped += 1
            continue
        try:
            dur = generate_clip(text, out_path, SENTENCE_MODEL, SENTENCE_VOICE_SETTINGS, api_key)
            print(f"  [{dur:5.2f}s] {text!r} -> {rel}")
            generated += 1
            time.sleep(0.25)
        except Exception as e:
            print(f"  FAILED {text!r}: {e}", file=sys.stderr)
            failed += 1

    with open(MANIFEST_PATH, "w") as f:
        json.dump({"chunks": chunk_map, "sentences": sentence_map}, f, indent=2)

    new_html = patch_html_manifest(html, chunk_map, sentence_map)
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(new_html)

    print(f"\nDone. generated={generated} skipped(existing)={skipped} failed={failed}")
    print(f"Manifest written to {MANIFEST_PATH} and patched into {HTML_PATH}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()

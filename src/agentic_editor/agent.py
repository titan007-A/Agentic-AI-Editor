"""Turning an editor's plain-language instruction into this job's settings, and judging
how sure we are that a finished video is ready to post.

The planning model lists the changes the instruction asks for, each with the instruction's
own words that ask for it. Small models also "find" changes nobody asked for, so every
request is checked in code: its quote must be in the instruction and be about that
setting, and a language or number must actually be there. Only grounded requests are
applied; the rest are logged and dropped. What the instruction says about the content
("start where he talks about X") goes to the planner as notes.
"""

import re
from typing import Literal

from pydantic import BaseModel, ValidationError

from agentic_editor.edl import EDL
from agentic_editor.presets import Preset

Setting = Literal[
    "captions_off",
    "captions_language",
    "captions_from_picture",
    "captions_lyrics",
    "one_liner",
    "look",
    "colour_correction",
    "whole_picture",
    "remove_instruments",
    "keep_music",
    "beat_sync",
    "versions",
    "max_seconds",
    "min_seconds",
    "no_watermark_blur",
    "start_point",
]

LOOK_WORDS = {
    "natural": ["natural"],
    "warm": ["warm"],
    "cool": ["cool", "cold"],
    "teal-orange": ["teal", "orange", "cinematic"],
    "film": ["film", "filmic", "vintage", "retro"],
    "moody": ["moody", "dark"],
    "vivid": ["vivid", "colorful", "colourful", "punchy", "saturated"],
    "mono": ["black and white", "black & white", "b&w", "mono", "grayscale", "greyscale"],
    "faded": ["faded", "washed"],
}

LANGUAGES = {
    "ar": "Arabic", "bn": "Bengali", "de": "German", "en": "English", "es": "Spanish",
    "fr": "French", "hi": "Hindi", "id": "Indonesian", "it": "Italian", "ja": "Japanese",
    "ko": "Korean", "mr": "Marathi", "nl": "Dutch", "pa": "Punjabi", "pl": "Polish",
    "pt": "Portuguese", "ru": "Russian", "ta": "Tamil", "te": "Telugu", "th": "Thai",
    "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu", "vi": "Vietnamese", "zh": "Chinese",
}  # fmt: skip

# The words a request's quote has to contain to be about that setting at all.
TOPIC = {
    "captions_off": r"caption|subtitle|text",
    "captions_language": r"caption|subtitle|translat|text|language",
    "captions_from_picture": r"caption|subtitle|text|describ|see|show",
    "captions_lyrics": r"lyric|word|sing|caption|subtitle",
    "one_liner": r"line|text|title|headline|write|caption|top|bottom",
    "look": r"look|colou?r|grade|tone|style|feel|"
    + "|".join(re.escape(w) for words in LOOK_WORDS.values() for w in words),
    "colour_correction": r"colou?r|grade|bright|exposure|correct|dark|light",
    "whole_picture": r"crop|frame|whole|full|letterbox|zoom",
    "remove_instruments": r"music|instrument|vocal|voice|a ?cappella|background",
    "keep_music": r"music|instrument|original|audio|sound",
    "beat_sync": r"beat|rhythm|tempo|bar",
    "versions": r"version|variant|option|cop(y|ies)|take|edit",
    "max_seconds": r"\d|second|minute|\bsec|\bmin",
    "min_seconds": r"\d|second|minute|\bsec|\bmin",
    "no_watermark_blur": r"watermark|logo|blur",
    "start_point": r"\b(start|begin|open)",
}


class Request(BaseModel):
    setting: Setting
    value: str = ""
    quote: str


class Requests(BaseModel):
    requests: list[Request]
    content_notes: str = ""


PROMPT = """An editor gave this instruction for making a short vertical video:

"{instruction}"

List only the changes it explicitly asks for, each as a setting, a value, and the exact words
of the instruction that ask for it (quote). Most instructions ask for one or two changes; an
instruction about which moment to use asks for none. Never add a change it doesn't ask for.

Settings:
- captions_off: no captions.
- captions_language: captions in another language; value is the language.
- captions_from_picture: captions written from what is seen, for videos without speech.
- captions_lyrics: captions of sung lyrics.
- one_liner: one line of text burned on the video; value is what it should say or be about.
- look: a colour look; value is one of natural, warm, cool, teal-orange, film, moody, vivid,
  mono, faded.
- colour_correction: fix the colours or brightness.
- whole_picture: don't crop the picture; show all of it.
- remove_instruments: remove the music or instruments and keep only the voice.
- keep_music: keep the original audio with its music.
- beat_sync: start and end on the beat.
- versions: make several versions; value is how many.
- max_seconds / min_seconds: a length limit; value is the number of seconds.
- no_watermark_blur: don't blur watermarks or logos.
- start_point: the instruction says where the video should start; the moment itself also
  goes in content_notes.

content_notes: the editor's own words about which moment to use, where to start or end, or
what the video is about; empty if the instruction says nothing about that."""


# "start where...", "begin with...", "open on...": the editor is placing the opening.
START_AT = r"\b(start|begin|open)\w*\s+(with|where|when|at|on|from|right|as|after)\b[^,.;]*"


def interpret(llm, instruction: str) -> Requests:
    reply = llm.chat_json(
        [{"role": "user", "content": PROMPT.format(instruction=instruction.strip())}],
        Requests.model_json_schema(),
        temperature=0.0,
    )
    wanted = Requests.model_validate(reply)
    # Small models often read "begin with X" as content only, and then the hook optimiser
    # would move the opening the editor asked for.
    placed = any(r.setting == "start_point" for r in wanted.requests)
    if not placed and (match := re.search(START_AT, instruction, re.IGNORECASE)):
        wanted.requests.append(Request(setting="start_point", quote=match.group().strip()))
    return wanted


def _number(text: str) -> float | None:
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    match = re.search(r"\d+(\.\d+)?", text)
    if match:
        return float(match.group())
    for word, n in words.items():
        if re.search(rf"\b{word}\b", text):
            return float(n)
    return None


NEGATION = r"\b(no|not|without|off|remove|skip|don'?t|dont|never)\b"


def named_language(text: str) -> str | None:
    lowered = text.lower()
    return next((n for n in LANGUAGES.values() if n.lower() in lowered), None)


def seconds(text: str) -> float | None:
    """A length from words like "30 seconds", "2 minutes", "under a minute"."""
    lowered = text.lower()
    n = _number(lowered)
    if "minute" in lowered or re.search(r"\bmin\b", lowered):
        return (n if n is not None else (0.5 if "half" in lowered else 1.0)) * 60
    return n


def normalise(request: Request) -> Request:
    """Fill in what small models leave out: a value they only quoted, or a language request
    filed under the wrong caption setting."""
    quote, value = request.quote, request.value.strip()
    setting = request.setting
    lang = named_language(quote)
    if setting.startswith("captions") and lang and not re.search(NEGATION, quote.lower()):
        setting, value = "captions_language", value or lang
    if not value:
        if setting == "captions_language":
            value = lang or ""
        elif setting == "look":
            value = quote
        elif setting in ("max_seconds", "min_seconds"):
            value = f"{seconds(quote):g}" if seconds(quote) else ""
        elif setting == "versions":
            value = f"{_number(quote):g}" if _number(quote) else ""
    return request.model_copy(update={"setting": setting, "value": value})


def language(value: str) -> str:
    """A language name from what the model gave: a name, or a code like "es"."""
    value = value.strip()
    return LANGUAGES.get(value.lower(), value).title()


def look(value: str, instruction: str) -> str | None:
    """The first look named in the value that the instruction also asks for."""
    said, value = instruction.lower(), value.lower()
    for name, words in LOOK_WORDS.items():
        if any(w in value for w in [name, *words]) and any(w in said for w in words):
            return name
    return None


def grounded(request: Request, instruction: str) -> str | None:
    """Why a request can't be trusted, or None if it is backed by the instruction's words."""
    said = instruction.lower()
    quote = request.quote.lower().strip(" .\"'")
    if not quote or quote not in said:
        return "its quote isn't in the instruction"
    if not re.search(TOPIC[request.setting], quote):
        return "its quote isn't about that"
    value = request.value.strip().lower()
    if request.setting == "captions_language" and (
        not value or language(value).lower() not in said
    ):
        return "no language named"
    if request.setting == "look" and look(value, instruction) is None:
        return "that look isn't asked for"
    if request.setting in ("captions_off", "no_watermark_blur") and not re.search(NEGATION, quote):
        return "it doesn't ask for anything to be off"
    if request.setting == "versions" and _number(quote) is None:
        return "no number given"
    if request.setting in ("max_seconds", "min_seconds") and seconds(quote) is None:
        return "no length given"
    return None


def apply(preset: Preset, requests: list[Request], instruction: str) -> tuple[Preset, list[str]]:
    """The preset with the instruction's grounded requests applied, and a line for each."""
    data = preset.model_dump()
    notes: list[str] = []

    def change(path: str, value, note: str) -> None:
        *parents, key = path.split(".")
        target = data
        for p in parents:
            target = target[p]
        if target[key] != value:
            target[key] = value
            notes.append(f"from your instruction: {note}")

    for request in map(normalise, requests):
        if (problem := grounded(request, instruction)) is not None:
            notes.append(f"ignored a reading of the instruction ({request.setting}): {problem}")
            continue
        value = request.value.strip()
        match request.setting:
            case "captions_off":
                change("captions.enabled", False, "captions off")
            case "captions_language":
                change("captions.enabled", True, "captions on")
                change("captions.mode", "translated", "captions translated")
                name = language(value)
                change("captions.language", name, f"captions in {name}")
            case "captions_from_picture":
                change("captions.enabled", True, "captions on")
                change("captions.mode", "visual", "captions written from the picture")
            case "captions_lyrics":
                change("captions.enabled", True, "captions on")
                change("captions.mode", "lyrics", "lyric captions")
            case "one_liner":
                change("one_liner.enabled", True, "one-liner on")
                hint = value or request.quote.strip()
                change("one_liner.hint", hint, f"one-liner about: {hint}")
            case "look":
                name = look(value, instruction)
                change("grade.look", name, f"look: {name}")
            case "colour_correction":
                change("grade.auto", True, "colour correction on")
            case "whole_picture":
                change("framing.mode", "none", "the whole picture, not cropped")
            case "remove_instruments":
                change("audio.source", "vocals", "instruments removed")
            case "keep_music":
                change("audio.source", "original", "original audio kept")
            case "beat_sync":
                change("beat_sync.snap_trims", True, "start and end on the beat")
            case "versions":
                n = int(min(5, max(1, _number(request.quote) or 1)))
                change("hook.variants", n, f"{n} version(s)")
            case "max_seconds":
                n = seconds(request.quote)
                change("max_duration", n, f"at most {n:g} s")
            case "min_seconds":
                n = seconds(request.quote)
                change("min_duration", n, f"at least {n:g} s")
            case "no_watermark_blur":
                change("watermarks.blur", False, "watermark blur off")
            case "start_point":
                change("hook.optimize", False, "the opening stays where you asked it to start")
    try:
        updated = Preset.model_validate(data)
    except ValidationError as exc:
        reason = exc.errors()[0]["msg"].removeprefix("Value error, ")
        return preset, [f"couldn't apply the instruction's settings ({reason}); kept the preset"]
    return updated, notes


def start_point(requests: list[Request], instruction: str) -> str | None:
    """The instruction's own words for where the video should start, if it says."""
    for request in map(normalise, requests):
        if request.setting == "start_point" and grounded(request, instruction) is None:
            return request.quote.strip()
    return None


def confidence(
    edl: EDL,
    issues: list[str],
    planner: str,
    preset: Preset,
    dead_air: list[tuple[float, float]] = (),
) -> tuple[float, list[str]]:
    """How sure the agent is that the video can be posted as it is, and why not.
    `dead_air` is the long silences left inside the reel, in source time."""
    doubts = list(issues)
    start = edl.clips[0].start
    doubts += [f"{b - a:.0f}s without voice at {a - start:.0f}s" for a, b in dead_air]
    if planner == "heuristic":
        doubts.append("planned without the language model")
    failures = ("failed", "couldn't", "untranslated", "no vision model", "left shorter")
    doubts += [d for d in edl.decisions if any(f in d for f in failures)]
    if edl.duration < preset.min_duration - 0.5:
        doubts.append(f"only {edl.duration:.0f}s long, under the {preset.min_duration:g}s minimum")
    score = 1.0 - 0.3 * len(issues) - 0.2 * (len(doubts) - len(issues))
    return round(max(0.0, min(1.0, score)), 2), doubts

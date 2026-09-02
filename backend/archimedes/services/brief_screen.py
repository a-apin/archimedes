"""Deterministic, pre-inference screening of untrusted text (#1801).

Two kinds of text reach a prompt in this system and neither is trusted:

1. **The user's brief.** `GenerateBrief.intent` is inserted verbatim into the
   validator message (`generation_pipeline._validate_brief`) and, as
   `strategic_direction`, verbatim into the fusion proposer prompt for every
   steer (`strategy_fusion.propose`). Before this module the only deterministic
   check was `cheap_brief_reject` — min-length, word-token and keyboard-mash
   heuristics — which deliberately deferred jailbreak attempts to an LLM
   validator that itself failed OPEN on every error path. An LLM guarding an
   LLM, with the guard's failure mode being "admit everything".
2. **Model output that re-enters a prompt.** `strategy_name` on the debate
   candidate cards (`debate_engine._candidate_cards`) and the opponent's claim
   prose in the round-2 rebuttal (`debate_engine._turn`) are both interpolated
   unescaped into a single-line context. A name carrying ``\\n[C6] …`` forges a
   candidate card that no proposer produced.

This module is the deterministic answer to both: pure Python, no network, no
LLM, no I/O. It decides admission; the LLM validator is downstream of it.

Two invariants worth stating out loud, because they are what make this
defensible rather than decorative:

* **Fail closed.** Any internal exception returns ``allow=False`` with
  ``screen.internal_error``. A screener that crashes must refuse, not admit —
  the exact inverse of the fail-open validator this replaces.
* **Never rewrite.** A rejected string is *omitted* from the outgoing prompt
  and the omission is recorded. Stored text — `transcript_json`, the brief on
  the job record — is never modified by this module. Prompt assembly may
  decline to include something and say so; it does not silently edit history.
* **Match on a canonical copy.** Every rule runs against a normalized COPY
  (see ``_canonical``) as well as the raw string, because a regex word anchor
  is defeated by a character nobody can see — a zero-width space inside
  ``ig<ZWSP>nore``, a Cyrillic ``о`` in ``ignоre``, a fullwidth ``Ｓｙｓｔｅｍ``.
  The copy is computed per call and thrown away; it never reaches a prompt, a
  transcript or a store. Canonicalising for matching is not rewriting.

What this is NOT: a semantic judge. It has no opinion on whether a brief is a
*good* brief, whether it is on-topic, or whether the strategy it describes is
sane. Off-topic-but-grammatical text ("add flour and bake at 350F") passes
here and is the LLM validator's problem, exactly as before. Unfamiliar
vocabulary is never a rejection reason — most real briefs contain some.

Reason codes are a stable, versioned vocabulary: see ``ALL_CODES`` and
``RULESET_VERSION`` at the bottom of this module, and the red/green corpus at
``backend/tests/fixtures/brief_screen/``. Changing the code set without
bumping the version fails ``test_brief_screen.py``.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

# Leaf import, deliberately. ``archimedes.api.generate_schemas`` imports only
# pydantic + stdlib and ``archimedes/api/__init__.py`` is a two-line comment,
# so this cannot re-enter the ``archimedes.services`` package the way a
# ``strategy_fusion`` import would (see the circular-import note in
# generate_schemas.py). It gives the length bound and the control-character
# class ONE definition shared by the request schema and the screener, instead
# of two copies held together by a drift test.
from archimedes.api.generate_schemas import CONTROL_CHARS, INTENT_MAX_LEN

logger = logging.getLogger(__name__)


# ── Surfaces ──────────────────────────────────────────────────────────────


class Surface(str, Enum):
    """Where the text came from — which decides which rule families run.

    ``BRIEF`` is text a human typed and is about to be spent on inference:
    it must be shaped like a brief (SHAPE), must not carry instructions aimed
    at the model (INJECT), and must be language rather than mash (LANG).

    ``MODEL_TEXT`` is text a model produced that is about to be interpolated
    back into another prompt. Shape and language rules do NOT apply — a
    two-word strategy name is not "too short", and model prose is not mash —
    but structural forgery (STRUCT) does, because that text lands inside a
    line-oriented card format, and INJECT does, because a compromised or
    merely creative model is not a trusted author either.
    """

    BRIEF = "brief"
    MODEL_TEXT = "model_text"


#: Upper bound for one model-produced string interpolated into a prompt.
#: A strategy name is ~40 chars and a claim ~200; 1,000 is generous headroom
#: while still bounding what one turn can inject into the next one's prompt.
MODEL_TEXT_MAX_CHARS = 1000


# ── Verdict ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Verdict:
    """The screening decision.

    ``reason`` and ``hint`` keep the exact shape and register the LLM
    validator's invalid-brief output uses (``generation_pipeline``'s
    ``_invalid_brief_message`` frames ``reason`` inside a sentence), so wiring
    this in front of the validator moves no API contract: ``reason`` is a
    lowercase fragment with no trailing period, ``hint`` is a full sentence.

    ``code`` is the machine-readable reason. ``None`` iff ``allow`` is True.
    """

    allow: bool
    code: str | None
    reason: str
    hint: str


_ALLOW = Verdict(allow=True, code=None, reason="", hint="")

_BRIEF_HINT = "Mention an asset class, a goal, or a risk appetite."
_INJECT_HINT = (
    "Describe the portfolio you want, not what the model should do. "
    "Links, code blocks, encoded blobs and instructions aimed at the system are not accepted."
)


def _reject(code: str, reason: str, hint: str) -> Verdict:
    return Verdict(allow=False, code=code, reason=reason, hint=hint)


# ── CANON — one normalisation pass, run before any rule ──────────────────
#
# The threat this closes: every INJECT pattern below is anchored on ``\b`` and
# on literal English words, and all three anchors are broken by characters a
# human reviewer cannot see or cannot tell apart. ``ig<U+200B>nore`` renders as
# "ignore"; ``ignоre`` with a Cyrillic "о" renders as "ignore"; ``Ｓｙｓｔｅｍ
# ｐｒｏｍｐｔ`` renders as "System prompt". None of the three is something a
# person writing about bonds produces by accident, and each is one keystroke
# for an attacker — the same argument the module already makes for refusing
# control characters, applied to the characters that survive a copy-paste.
#
# What this is NOT is a rewrite. ``_canonical`` returns a COPY used for
# matching and then discarded; the string that reaches the prompt, the
# transcript and the job record is byte-for-byte what its author wrote.

#: Invisible in rendered text, and every one of them splits a regex word
#: anchor: zero-width space / non-joiner / joiner, the LTR and RTL marks, word
#: joiner, the invisible math operators, the BOM, and the soft hyphen.
_ZERO_WIDTH = re.compile(r"[\u00ad\u200b-\u200f\u2060-\u2064\ufeff]")

#: Non-breaking and exotic spaces fold to a space; the Unicode line and
#: paragraph separators fold to a newline, so a payload that splits a rule
#: across ``U+2028`` is read as the line break it renders as.
_WHITESPACE_FOLD = str.maketrans({"\u00a0": " ", "\u202f": " ", "\u2007": " ", "\u2028": "\n", "\u2029": "\n"})

#: Cyrillic and Greek letters that are pixel-identical to a Latin letter in
#: the fonts a browser actually uses. Deliberately a SHORT, explicit table
#: rather than a general confusables database: these are the ones that spell
#: an English instruction word, and an explicit table can never quietly start
#: folding a legitimate Cyrillic or Greek brief into something the mash
#: heuristic mistakes for junk. Lowercase Greek is absent on purpose — it does
#: not spell English words, and mapping it would damage real Greek text.
_CONFUSABLES = str.maketrans(
    {
        # Cyrillic
        "\u0410": "A",  # А
        "\u0430": "a",  # а
        "\u0412": "B",  # В
        "\u0415": "E",  # Е
        "\u0435": "e",  # е
        "\u0406": "I",  # І
        "\u0456": "i",  # і
        "\u041a": "K",  # К
        "\u041c": "M",  # М
        "\u041d": "H",  # Н
        "\u041e": "O",  # О
        "\u043e": "o",  # о
        "\u0420": "P",  # Р
        "\u0440": "p",  # р
        "\u0421": "C",  # С
        "\u0441": "c",  # с
        "\u0422": "T",  # Т
        "\u0425": "X",  # Х
        "\u0445": "x",  # х
        "\u0423": "Y",  # У
        "\u0443": "y",  # у
        # Greek (uppercase only)
        "\u0391": "A",  # Α
        "\u0392": "B",  # Β
        "\u0395": "E",  # Ε
        "\u0396": "Z",  # Ζ
        "\u0397": "H",  # Η
        "\u0399": "I",  # Ι
        "\u039a": "K",  # Κ
        "\u039c": "M",  # Μ
        "\u039d": "N",  # Ν
        "\u039f": "O",  # Ο
        "\u03a1": "P",  # Ρ
        "\u03a4": "T",  # Τ
        "\u03a5": "Y",  # Υ
        "\u03a7": "X",  # Χ
    }
)

_HORIZONTAL_RUN = re.compile(r"[^\S\n]+")
_VERTICAL_RUN = re.compile(r"[^\S\n]*\n[\s]*")


def _canonical(text: str) -> str:
    """The matching copy: what the text *renders as*, spelled canonically.

    NFKC (fullwidth → ASCII, ligatures, compatibility forms) → drop the
    invisibles → fold the exotic spaces and separators → fold the homoglyphs →
    collapse runs of whitespace, keeping ONE newline where a line break was.

    The newline is kept deliberately: ``_ROLE_FORGERY``'s ``^system:`` anchor
    and ``_struct_rules``' line-break rule both read line structure, and a
    flat collapse to spaces would silently delete two rules.
    """
    folded = unicodedata.normalize("NFKC", text)
    folded = _ZERO_WIDTH.sub("", folded)
    folded = folded.translate(_WHITESPACE_FOLD)
    folded = folded.replace("\r\n", "\n").replace("\r", "\n")
    folded = folded.translate(_CONFUSABLES)
    folded = _HORIZONTAL_RUN.sub(" ", folded)
    return _VERTICAL_RUN.sub("\n", folded)


def _hits(pattern: re.Pattern[str], text: str, canon: str) -> bool:
    """Does this pattern fire on the raw text OR on its canonical copy?

    Both, never canonical alone. Collapsing whitespace is what makes the
    homoglyph and zero-width folds work, but it also erases the four-space
    indent that marks a markdown code block — so the raw string keeps its own
    vote. Searching a superset can only remove false negatives.
    """
    return bool(pattern.search(text) or pattern.search(canon))


# ── LANG — lifted verbatim from generation_pipeline (Lane 1.3c) ───────────
#
# Everything from here to the end of ``_lang_rules`` moved out of
# ``generation_pipeline.cheap_brief_reject`` unchanged, including the
# non-ASCII pass-through. It must stay conservative: a false NEGATIVE (missing
# real gibberish) is fine — the LLM validator still sees it. A false POSITIVE
# is not: `cheap_brief_reject` runs BEFORE the payment gate, so flagging a
# genuine brief refuses a paying user before they are even offered the chance
# to pay. Unfamiliar vocabulary is therefore never a rejection reason —
# "muni ladder", "SPY covered calls" and non-English text all pass.

_MIN_INTENT_CHARS = 3  # the request schema's min_length=1 only bars empty; 1-2 chars is not a brief
_MIN_GIBBERISH_TOKENS = 2  # ≥2 tokens before any mash token counts as junk

_VOWELS = frozenset("aeiouy")

# Straight runs across a keyboard row, forwards and backwards, are a
# fingerprint of mashing rather than typing ("asdf", "lkjh", "qwer", "poiu").
# No English word contains one; checked as 4-char windows.
_KEYBOARD_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
_KEYBOARD_RUNS = frozenset(
    row[i : i + 4] for base in _KEYBOARD_ROWS for row in (base, base[::-1]) for i in range(len(row) - 3)
)


def _looks_like_mash(token: str) -> bool:
    """Is this lowercased token *shaped* like keyboard mash?

    Structural only — this asks whether the letters could plausibly have been
    typed as a word, never whether the word is one we happen to know. A brief
    is full of words no list here contains ("muni", "ladder", "covered"), so
    unfamiliarity carries no signal at all.

    Non-ASCII tokens always return False. A Cyrillic, Greek or CJK brief has
    no vowel/consonant structure this test can read, and mis-reading one as
    mash would refuse a legitimate non-English user before they can even pay;
    those defer to the LLM validator, exactly like off-topic English does.
    """
    if not token.isascii():
        return False
    if not any(ch in _VOWELS for ch in token):
        return True  # "zxcvbnm", "qwrtp" — no vowel, not pronounceable
    run = 0
    for ch in token:
        run = 0 if ch in _VOWELS else run + 1
        if run >= 5:
            return True  # "lkjhgfdsa" — 5+ consonants with no break
    if any(a == b == c for a, b, c in zip(token, token[1:], token[2:], strict=False)):
        return True  # "aaargh", "jjjj"
    return any(token[i : i + 4] in _KEYBOARD_RUNS for i in range(len(token) - 3))


# Ordinary English function/content words. Their presence means the text is
# at least grammatical, even if off-topic — off-topic is the LLM's job, not
# this heuristic's.
_COMMON_WORDS = frozenset(
    "a an the and or but for nor so if of to in on at by with from into is are "
    "was were be been being this that these those i you he she it we they my "
    "your his her its our their not no yes want need make build create "
    "generate please can could would should like about money fund funds".split()
)

# Investing / finance-signal vocabulary. Presence means the text is on-topic
# even when it fails the common-word check above (e.g. "crypto momentum").
_FINANCE_WORDS = frozenset(
    "stock stocks bond bonds equity equities crypto bitcoin ethereum token "
    "tokens coin coins etf etfs treasury treasuries yield yields dividend "
    "dividends momentum value growth trend trending hedge hedged leverage "
    "leveraged volatility volatile vol risk risky conservative aggressive "
    "moderate income rebalance rebalancing diversify diversified "
    "diversification asset assets allocation market markets trading trade "
    "trades rate rates inflation macro commodity commodities gold silver "
    "oil futures options derivative derivatives arbitrage carry basis "
    "spread stablecoin usdc usdt defi staking lending long short bull bear "
    "index indices quant quantitative alpha beta sharpe drawdown portfolio "
    "invest investment strategy strategies".split()
)


def _lang_rules(_text: str, canon: str) -> Verdict:
    """``lang.no_words`` / ``lang.mash`` — is this language at all?"""
    # Unicode-aware: letters in ANY script, no digits/underscores. A Cyrillic
    # or CJK brief must tokenize as words, not vanish into the letter-free
    # branch below and be refused for "containing no words". Read off the
    # canonical copy so a brief padded with zero-width characters tokenizes as
    # the words it renders as.
    tokens = re.findall(r"[^\W\d_]{2,}", canon)
    if not tokens:
        # No word-like content at all — pure digits/punctuation/symbols.
        return _reject("lang.no_words", "it does not contain any words", _BRIEF_HINT)

    lowered = [t.lower() for t in tokens]
    if any(t in _COMMON_WORDS or t in _FINANCE_WORDS for t in lowered):
        return _ALLOW  # recognizable language — defer to the real validator

    if all(t.isupper() and len(t) <= 5 for t in tokens):
        return _ALLOW  # plausible ticker list, e.g. "BTC ETH SOL"

    # Junk needs positive evidence of mashing, never just unfamiliar words.
    if len(tokens) >= _MIN_GIBBERISH_TOKENS and any(_looks_like_mash(t) for t in lowered):
        return _reject("lang.mash", "it does not look like an investment goal", _BRIEF_HINT)
    return _ALLOW  # nothing mash-shaped to point at; defer to the LLM


# ── SHAPE ─────────────────────────────────────────────────────────────────


def _shape_rules_brief(text: str, canon: str) -> Verdict:
    """Empty / too short / too long / control characters.

    The control-character test reuses ``generate_schemas``' rule for the
    user-chosen strategy name: collapse whitespace FIRST so a ``\\t``/``\\n``
    arriving in a paste is normalized rather than rejected, then refuse
    anything that survives collapsing (NUL, ESC, DEL, …). Those never belong
    in text a human typed, and they are the classic way to smuggle a
    terminal-invisible payload past a human reviewer.

    Note this reads a COLLAPSED COPY. The brief itself is never rewritten —
    the stored intent is whatever the user sent.

    Emptiness and the minimum are judged on the CANONICAL copy, so a brief of
    nothing but zero-width spaces reads as the blank it renders as rather than
    as a three-character brief. The maximum is judged on the RAW string,
    because that is the bound ``GenerateBrief.intent``'s ``max_length``
    enforces one layer out — measuring a collapsed copy here would admit
    something the schema rejects.
    """
    stripped = canon.strip()
    if not stripped:
        return _reject("shape.empty", "it did not describe an investment goal", _BRIEF_HINT)
    if len(stripped) < _MIN_INTENT_CHARS:
        return _reject("shape.too_short", "too short to describe an investment goal", _BRIEF_HINT)
    if len(text) > INTENT_MAX_LEN:
        return _reject(
            "shape.too_long",
            f"it is longer than {INTENT_MAX_LEN} characters",
            "Trim it to one or two sentences — state the intent and let the pipeline choose the mechanics.",
        )
    if CONTROL_CHARS.search(re.sub(r"\s+", " ", text)):
        return _reject(
            "shape.control_chars",
            "it contained control characters",
            "Retype it as plain text — pasting from a PDF or a terminal can carry invisible characters.",
        )
    return _ALLOW


def _shape_rules_model(text: str, _canon: str) -> Verdict:
    """Model text: only the bounds that keep it safe to interpolate.

    No empty/too-short rule — an empty rebuttal is simply no rebuttal, and the
    caller already has a fallback for an empty strategy name.

    Both bounds read the RAW string on purpose: the length that matters is the
    length that lands in the next prompt, and a control character is refused,
    not folded.
    """
    if len(text) > MODEL_TEXT_MAX_CHARS:
        return _reject(
            "shape.too_long",
            f"model text longer than {MODEL_TEXT_MAX_CHARS} characters",
            "This is an internal bound; the text was left out of the next prompt.",
        )
    if CONTROL_CHARS.search(re.sub(r"[ \t]+", " ", text)):
        return _reject(
            "shape.control_chars",
            "model text contained control characters",
            "This is an internal bound; the text was left out of the next prompt.",
        )
    return _ALLOW


# ── INJECT ────────────────────────────────────────────────────────────────
#
# Every pattern below is written to need POSITIVE evidence of an instruction
# aimed at the system, never a merely unusual phrase. The false-positive cost
# is a refused paying user, so patterns that a real finance brief could
# plausibly contain are deliberately absent — most notably "act as", which is
# ordinary English in "act as a hedge against inflation".

#: verb + previous-ish + instruction-ish, within a short window. "disregard
#: prior signals" does not match (signals is not an instruction noun);
#: "ignore all previous instructions" does.
#:
#: The gap class is ``[^.]`` and NOT ``[^.\n]``: a single Enter between two
#: anchors is not a sentence boundary, it is the cheapest possible bypass of
#: this module's headline rule, and the brief box is a three-row textarea whose
#: newlines are pinned as legitimate by ``test_a_brief_may_contain_a_newline``.
#: The period still bounds the window, which is what keeps two unrelated
#: sentences from being read as one directive.
_OVERRIDE_DIRECTIVE = re.compile(
    r"\b(?:ignore|disregard|forget|override|bypass|discard|set\s+aside|put\s+aside)\b[^.]{0,40}"
    r"\b(?:previous|prior|above|earlier|preceding|foregoing|all|any|everything)\b[^.]{0,40}"
    r"\b(?:instruction|direction|prompt|rule|guideline|guardrail|restriction|constraint|"
    r"policy|message|command|system)s?\b",
    re.IGNORECASE,
)

#: Asking for the prompt back. Distinct from an override directive (nothing is
#: being disregarded) and from role forgery (no role is being reassigned): the
#: payload is "print what you were told", which is a request for the one string
#: this service must never emit. Needs a leak verb AND a prompt-ish noun AND a
#: pointer at earlier text, so "print the portfolio rules" is not enough.
_PROMPT_LEAK = re.compile(
    r"\b(?:repeat|reveal|print|show|output|display|echo|disclose|dump|reproduce|recite)\b[^.]{0,40}"
    r"\b(?:instructions?|prompts?|configuration|config|rules?|guidelines?|guardrails?|"
    r"system\s+message)\b[^.]{0,40}"
    r"\b(?:above|earlier|preceding|previous|prior|verbatim|word\s+for\s+word|in\s+full)\b",
    re.IGNORECASE,
)

_ROLE_FORGERY = (
    re.compile(r"\byou\s+are\s+now\b", re.IGNORECASE),
    re.compile(r"\bsystem\s+prompt\b", re.IGNORECASE),
    re.compile(r"^\s*(?:system|assistant|user|human)\s*:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\bpretend\s+(?:to\s+be|you\s+are|that\s+you)\b", re.IGNORECASE),
    re.compile(r"\bfrom\s+now\s+on,?\s+you\b", re.IGNORECASE),
    re.compile(r"\b(?:developer|jailbreak|god)\s+mode\b", re.IGNORECASE),
    # Chat-template markers: ChatML, Llama-style, Anthropic-style turn tags.
    re.compile(r"<\|?\s*(?:im_start|im_end|endoftext|system|/?s)\s*\|?>", re.IGNORECASE),
    re.compile(r"\[/?INST\]|<<\s*/?SYS\s*>>"),
)

#: Keys from the two JSON reply schemas this system asks models to produce —
#: ``_BRIEF_VALIDATION_SYSTEM`` (generation_pipeline) and ``_DEBATE_SYSTEM``
#: (debate_engine). Text carrying one of these as a quoted JSON key is
#: forging a reply terminator, not describing a portfolio.
_SCHEMA_KEYS = (
    "is_valid",
    "intent_summary",
    "asset_classes_inferred",
    "time_horizon_inferred",
    "risk_appetite_adjusted",
    "verdict",
    "confidence",
    "key_claims",
    "fatal_flaws",
    "candidate_id",
    "arxiv_ids",
    "discard",
    "claim",
)
#: Straight quotes, the four smart quotes macOS/iOS/Word substitute for them
#: by default, and the backtick. Autocorrect turning ``"`` into ``“`` was
#: enough to walk a forged reply terminator straight past this rule.
_QUOTE_CHARS = "[\"'\u2018\u2019\u201c\u201d`]"

#: Quoted key, or bare key followed by a colon. The unquoted branch is there
#: because ``{is_valid: true}`` is not valid JSON and is read as a forged reply
#: by exactly the same downstream parser — and because dropping the quotes is
#: the first thing an attacker tries after the quoted form is refused. The
#: false-positive cost is a brief that writes one of these thirteen schema
#: keys immediately before a colon; a green-corpus line pins the near-miss
#: ("signal confidence, capped at 20%") that a real brief actually contains.
_SCHEMA_FORGERY = re.compile(
    _QUOTE_CHARS + r"(?:" + "|".join(_SCHEMA_KEYS) + r")" + _QUOTE_CHARS + r"\s*:"
    r"|(?<![\w-])(?:" + "|".join(_SCHEMA_KEYS) + r")\s*:",
    re.IGNORECASE,
)

_CODE_FENCE = re.compile(r"(?:`{3,}|~{3,})")

#: Markdown's OTHER code block: a blank line followed by a line indented four
#: spaces (or a tab). It renders exactly like a fenced block and it carried a
#: ``SYSTEM OVERRIDE`` payload past the fence rule. The blank line is required
#: on purpose — a brief that merely opens with an accidental indent is not a
#: code block in markdown either, and refusing it would cost a real user.
_INDENTED_CODE = re.compile(r"\n[ \t]*\n[ \t]*(?: {4}|\t)\S")

#: An HTML comment. Invisible everywhere the brief is rendered, carried
#: verbatim into the prompt, and read as text by the model — the same
#: "hidden from the human, visible to the machine" shape as a control
#: character. Only the opener is matched: a bare ``-->`` is a plausible arrow
#: in ordinary prose ("momentum --> value").
_HTML_COMMENT = re.compile(r"<!--")

#: A link needs a scheme, a ``www.`` host, or a bare host FOLLOWED BY A PATH
#: OR A QUERY. The last clause is the load-bearing one: "Amazon, Alphabet and
#: Booking.com" is a list of companies, not a link, and half of a realistic
#: sample of large-cap briefs named at least one ``.com`` company. Refusing
#: those costs a paying user *before* the payment gate, which is the most
#: expensive failure this module has. Something to fetch — ``/spec``,
#: ``?payload=`` — is what separates an instruction to go elsewhere from a
#: company's name.
#:
#: The TLD list stays short for the second reason: exchange suffixes are
#: written exactly like bare hosts (".TO" Toronto, ".CO" Copenhagen, ".ME"
#: Montenegro/Moscow, ".AI", ".L" London, ".PA" Paris, ".DE" Xetra, ".HK",
#: ".SW" Swiss, ".AX" Australia), so every TLD colliding with one is left out
#: rather than refusing a user for naming a foreign-listed ticker. Green
#: corpus lines pin both halves.
_URL = re.compile(
    r"\b(?:https?|ftp|file|data)://"
    r"|\bwww\.[a-z0-9-]+\.[a-z]{2,}"
    r"|(?<![\w.])[a-z0-9](?:[a-z0-9-]{1,61})?"
    r"\.(?:com|net|org|io|xyz|ru|cn|info|biz|app|dev)(?![a-z])"
    r"(?:/\S*|\?\S+)",
    re.IGNORECASE,
)

#: A long unbroken base64/hex-looking run. Requires mixed case AND a digit so
#: a long hyphenless English phrase can never match; no English word is 40
#: characters anyway.
_B64_RUN = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=])")

#: An all-lowercase hex run walks past ``_B64_RUN``'s mixed-case requirement,
#: and hex is the encoding an attacker reaches for once base64 is refused. The
#: same both-kinds-of-character test applies below (digits AND a-f), so a long
#: run of one letter — the ``aaaa…`` padding in the length-bound corpus lines —
#: is not mistaken for a payload.
_HEX_RUN = re.compile(r"(?<![A-Za-z0-9])(?:[0-9a-f]{2}){16,}(?![A-Za-z0-9])", re.IGNORECASE)


def _inject_rules(text: str, canon: str) -> Verdict:
    """Instructions aimed at the model rather than a description of a portfolio.

    Every pattern is tried against the raw string and against its canonical
    copy (:func:`_hits`), so an invisible character or a homoglyph changes what
    the payload looks like without changing what it matches.
    """
    if _hits(_OVERRIDE_DIRECTIVE, text, canon):
        return _reject(
            "inject.override_directive",
            "it tried to override the system's instructions",
            _INJECT_HINT,
        )
    if any(_hits(p, text, canon) for p in _ROLE_FORGERY):
        return _reject(
            "inject.role_forgery",
            "it tried to reassign the model's role",
            _INJECT_HINT,
        )
    if _hits(_PROMPT_LEAK, text, canon):
        return _reject(
            "inject.prompt_leak",
            "it asked the system to repeat its own instructions",
            _INJECT_HINT,
        )
    if _hits(_SCHEMA_FORGERY, text, canon):
        return _reject(
            "inject.schema_forgery",
            "it contained a forged machine reply",
            _INJECT_HINT,
        )
    if _hits(_CODE_FENCE, text, canon) or _hits(_INDENTED_CODE, text, canon) or _hits(_HTML_COMMENT, text, canon):
        return _reject("inject.code_fence", "it contained a code block", _INJECT_HINT)
    if _hits(_URL, text, canon):
        return _reject("inject.url", "it contained a link", _INJECT_HINT)
    for run in (*_B64_RUN.findall(text), *_B64_RUN.findall(canon)):
        if any(c.isdigit() for c in run) and any(c.islower() for c in run) and any(c.isupper() for c in run):
            return _reject("inject.base64_blob", "it contained encoded data", _INJECT_HINT)
    for run in (*_HEX_RUN.findall(text), *_HEX_RUN.findall(canon)):
        if any(c.isdigit() for c in run) and any(c.isalpha() for c in run):
            return _reject("inject.base64_blob", "it contained encoded data", _INJECT_HINT)
    return _ALLOW


# ── STRUCT — model text re-entering a prompt ──────────────────────────────
#
# The debate prompt is line-oriented: `_candidate_cards` emits one
# `[C1] Name — cites arXiv:xxxx "Title"` line per candidate and the rebuttal
# clause is a single sentence. Model text carrying a newline or a card marker
# forges a candidate the proposer never produced, and the anti-hallucination
# guard in `_turn` checks claims against the ids printed on the cards — so a
# forged card is a forged evidence base, not just cosmetic.

_CARD_MARKER = re.compile(r"\[\s*C\s*\d{1,3}\s*\]")
_CITES_MARKER = re.compile(r"[—-]\s*cites\b", re.IGNORECASE)
_REPLY_MARKER = re.compile(r"\breply\s+with\s+one\s+json\b", re.IGNORECASE)


def _struct_rules(text: str, canon: str) -> Verdict:
    """``struct.newline_in_card_field`` / ``struct.delimiter_forgery``.

    The canonical copy folds ``U+2028``/``U+2029`` to ``\n``, so a separator
    that renders as a line break is refused as one rather than sliding through
    as an ordinary character.
    """
    if "\n" in text or "\r" in text or "\n" in canon:
        return _reject(
            "struct.newline_in_card_field",
            "model text contained a line break in a single-line prompt field",
            "This is an internal bound; the text was left out of the next prompt.",
        )
    if _hits(_CARD_MARKER, text, canon) or _hits(_CITES_MARKER, text, canon) or _hits(_REPLY_MARKER, text, canon):
        return _reject(
            "struct.delimiter_forgery",
            "model text forged a prompt delimiter",
            "This is an internal bound; the text was left out of the next prompt.",
        )
    return _ALLOW


# ── Entry point ───────────────────────────────────────────────────────────

_SURFACE_RULES = {
    # SHAPE → INJECT → LANG. INJECT runs before LANG so a jailbreak attempt
    # reports as a jailbreak rather than as mash.
    Surface.BRIEF: (_shape_rules_brief, _inject_rules, _lang_rules),
    # STRUCT first: it is the family that describes what actually breaks when
    # model text is interpolated, so it should own the code when both apply.
    Surface.MODEL_TEXT: (_struct_rules, _shape_rules_model, _inject_rules),
}

_INTERNAL_ERROR = Verdict(
    allow=False,
    code="screen.internal_error",
    reason="we could not check this brief right now",
    hint="Try again in a moment, or shorten the brief.",
)


def screen(text: str, surface: Surface | str) -> Verdict:
    """Screen one untrusted string. Fails CLOSED.

    ``surface`` selects the rule families (see :class:`Surface`). An unknown
    surface, a non-string ``text``, or any exception raised inside a rule all
    return ``screen.internal_error`` with ``allow=False``: a screener that
    cannot run must refuse, never admit.

    The canonical copy is computed ONCE here, before any rule runs, and handed
    to every rule alongside the original. No rule sees a normalized string
    without also seeing what was actually sent, and neither string is stored.

    There is deliberately no "it is only whitespace, let it through"
    short-circuit for model text: a claim of three newlines is not empty, it is
    three line breaks landing in a single-line prompt slot, and the module's
    own docstring promises that is refused.
    """
    try:
        rules = _SURFACE_RULES[Surface(surface)]
        if not isinstance(text, str):
            raise TypeError(f"screen() expects str, got {type(text).__name__}")
        canon = _canonical(text)
        for rule in rules:
            verdict = rule(text, canon)
            if not verdict.allow:
                return verdict
        return _ALLOW
    except Exception:  # pragma: no cover - exercised via monkeypatch in tests
        logger.exception("brief screen failed internally; refusing (fail-closed)")
        return _INTERNAL_ERROR


def omit_if_rejected(text: str, *, field: str, context: str = "") -> tuple[str, Verdict]:
    """Screen model text bound for a prompt; return ``("", verdict)`` if refused.

    The contract callers depend on: a refused string is OMITTED from the
    outgoing prompt and the omission is logged with its reason code. The
    original is never rewritten, never escaped, never truncated — it stays
    exactly as the model produced it in whatever store holds it.
    """
    verdict = screen(text, Surface.MODEL_TEXT)
    if verdict.allow:
        return text, verdict
    logger.warning(
        "prompt omission: field=%s context=%s code=%s ruleset=%s chars=%d",
        field,
        context or "-",
        verdict.code,
        RULESET_VERSION,
        len(text or ""),
    )
    return "", verdict


# ── Versioned reason-code vocabulary ──────────────────────────────────────

#: Every code this module can return. Consumers (the corpus fixtures, the SSE
#: `reason_code`, the guidelines doc) treat this as the closed vocabulary.
ALL_CODES = frozenset(
    {
        "shape.empty",
        "shape.too_short",
        "shape.too_long",
        "shape.control_chars",
        "lang.no_words",
        "lang.mash",
        "inject.override_directive",
        "inject.prompt_leak",
        "inject.role_forgery",
        "inject.schema_forgery",
        "inject.code_fence",
        "inject.url",
        "inject.base64_blob",
        "struct.newline_in_card_field",
        "struct.delimiter_forgery",
        "screen.internal_error",
    }
)


def code_digest() -> str:
    """Stable 8-hex digest of the reason-code vocabulary."""
    return hashlib.sha256(",".join(sorted(ALL_CODES)).encode()).hexdigest()[:8]


#: ``<date>.<code_digest()>``. The suffix is checked against the live code set
#: by ``test_brief_screen.py``, so adding, renaming or deleting a code without
#: bumping this constant in the same commit is a red test — the version can
#: never quietly describe a different ruleset than the one that shipped.
RULESET_VERSION = "2026-09-02.37df7771"

"""Canonical text forms used to identify tracks across services.

Three forms exist on purpose, and they serve different rungs of the matching
ladder (see :mod:`ratingrelay.engine.matching`):

``normalize``
    Stable key form: lowercase, punctuation dropped, whitespace collapsed.
    :func:`track_key` built from it is what ``SyncRecord`` and ``TrackMapping``
    rows are keyed on, so changing its output orphans existing rows. It is a
    data format, not a refactoring detail.
``comparison_form``
    v1's lax form: lowercase, quote characters removed. Used by the
    containment rung, where "Don't Stop" and "Dont Stop" must line up.
``variant_form``
    ``normalize`` plus accent folding and edition-noise removal
    ("(Remastered 2011)", "- Live at Wembley", "feat. X"). Used by the variant
    rung, which only writes when exactly one candidate survives.
"""

import re
import unicodedata

_NON_WORD = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")
_QUOTES = str.maketrans("", "", "'’‘`´")
_COMBINING = re.compile(r"[\u0300-\u036f]")

# Bracketed or dash-separated suffixes that do not identify the recording.
# Matched case-insensitively; anything else in brackets is left alone because
# "Song (Part 2)" is a different track from "Song".
_EDITION_WORDS = (
    "remaster",
    "remastered",
    "deluxe",
    "expanded",
    "anniversary",
    "edition",
    "reissue",
    "bonus",
    "mono",
    "stereo",
    "demo",
    "live",
    "acoustic",
    "radio edit",
    "single version",
    "album version",
    "explicit",
    "clean",
    "feat",
    "ft",
    "featuring",
    "prod",
    "produced by",
)
_BRACKETED = re.compile(r"\s*[(\[]([^)\]]*)[)\]]")
_DASH_SUFFIX = re.compile(r"\s+[-–—]\s+.*$")
# A credit says who else is on the recording, never which recording it is. Both
# words are matched as whole words, so "Prodigy" and "Product" are safe.
_FEATURE = re.compile(
    r"\s+(?:feat\.?|ft\.?|featuring|prod\.?|produced by)\s+.*$", re.IGNORECASE
)


def normalize(text: str) -> str:
    """Return the stable key form of *text* (kept byte-compatible with v2)."""
    return _WHITESPACE.sub(" ", _NON_WORD.sub("", (text or "").lower())).strip()


def track_key(artist: str, title: str) -> str:
    """Return the ``SyncRecord``/``TrackMapping`` key for a track."""
    return f"{normalize(artist)}||{normalize(title)}"


def comparison_form(text: str) -> str:
    """Return v1's lax comparison form: lowercase, quotes dropped, trimmed."""
    return _WHITESPACE.sub(" ", (text or "").lower().translate(_QUOTES)).strip()


def _is_edition(segment: str) -> bool:
    lowered = segment.lower()
    return any(
        re.search(rf"\b{re.escape(word)}\b", lowered) for word in _EDITION_WORDS
    )


def _drop_edition_brackets(text: str) -> str:
    return _BRACKETED.sub(
        lambda match: "" if _is_edition(match.group(1)) else match.group(0), text
    )


def _drop_edition_suffix(text: str) -> str:
    return _DASH_SUFFIX.sub(
        lambda match: "" if _is_edition(match.group(0)) else match.group(0), text
    )


def variant_form(text: str) -> str:
    """Return the variant form of *text*: normalised, accent-folded, de-noised."""
    folded = _COMBINING.sub("", unicodedata.normalize("NFKD", text or ""))
    folded = _drop_edition_brackets(folded)
    folded = _drop_edition_suffix(folded)
    folded = _FEATURE.sub("", folded)
    return normalize(folded)


def variant_key(artist: str, title: str) -> str:
    """Return the variant-rung key for a track."""
    return f"{variant_form(artist)}||{variant_form(title)}"

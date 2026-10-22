from __future__ import annotations

import re

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "could",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "should",
        "so",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "this",
        "to",
        "us",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "yours",
        "get",
        "set",
        "make",
        "use",
        "using",
        "want",
        "need",
        "way",
        "possible",
        "able",
    ]
)


def terms(text: str) -> list[str]:
    """Lower-cased content words, in order, deduplicated."""
    seen: dict[str, None] = {}
    for raw in re.findall(r"[a-z0-9][a-z0-9'-]*", text.lower()):
        word = raw.strip("'-")
        if len(word) > 1 and word not in STOPWORDS:
            seen.setdefault(word, None)
    return list(seen)


def or_query(text: str) -> str:
    """`websearch_to_tsquery` input that ORs every content word: questions rarely match all terms."""
    return " or ".join(terms(text))


def stem(word: str) -> str:
    for suffix in ("ing", "ers", "er", "es", "ed", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word

"""Explicit chat hashtags, using the character alphabet of Memos v0.30."""

import re
import unicodedata


def tag_char(char):
    # HTML delimiters are not accepted as tag names in this bridge.
    return char not in "<>" and (unicodedata.category(char)[0] in "LNSM" or char in "_-/&\u200d")


def valid_tag(tag):
    return 1 <= len(tag) <= 100 and all(tag_char(c) for c in tag)


def tag_spans(text):
    # Mask ignored regions without shifting offsets in the original content.
    masked = re.sub(r"https?://[^\s<>]+|(`+|~{3,})[\s\S]*?\1", lambda m: " " * len(m[0]), text)
    for match in re.finditer(r"(?<![\w/#\\])#", masked):
        start = match.end()
        end = start
        while end < len(masked) and tag_char(masked[end]):
            end += 1
        tag = masked[start:end]
        if valid_tag(tag):
            yield match.start(), end, tag


def extract_tags(text):
    return list(dict.fromkeys(tag for _, _, tag in tag_spans(text)))


def strip_tags(text):
    spans = list(tag_spans(text))
    for start, end, _ in reversed(spans):
        # Remove only the tag token and adjacent horizontal separator; preserve paragraphs/code/URLs.
        while start > 0 and text[start - 1] in " \t":
            start -= 1
        text = text[:start] + text[end:]
    return text.strip()

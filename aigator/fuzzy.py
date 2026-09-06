"""Small, literal, bounded fzf-style matcher (not edit-distance correction).

The SQL prefilter scans normalized text in SQLite, not whole messages in Python.
Final scoring only examines candidates; no recency cutoff hides old sessions.
Unlike fzf on a short filename, archive matches must fit a compact text window.
"""
import re
import unicodedata
from functools import lru_cache


def folded(text):
    normalized = unicodedata.normalize('NFKD', text.casefold())
    if normalized.isascii():
        return normalized
    return normalized.translate({ord(c): None for c in set(normalized) if unicodedata.combining(c)})


def tokens_for(query):
    if len(query) > 256:
        return []
    tokens = list(dict.fromkeys(folded(query).split()))
    return tokens if len(tokens) <= 8 and all(len(t) <= 64 for t in tokens) else []


def like_pattern(token):
    return '%' + '%'.join(c.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                          for c in token) + '%'


def mapped_fold(text):
    if text.isascii():
        return text.lower(), range(len(text))
    value, positions = [], []
    for i, char in enumerate(text):
        part = folded(char)
        value.append(part)
        positions.extend([i] * len(part))
    return ''.join(value), positions


def match_token(text, token):
    # A bounded cache helps repeated titles/common message blocks without
    # retaining thousands of multi-megabyte transcripts in process memory.
    if len(text) <= 4096:
        return _cached_match_token(text, token)
    return _match_token(text, token)


@lru_cache(maxsize=1024)
def _cached_match_token(text, token):
    return _match_token(text, token)


def _match_token(text, token):
    """Best ordered subsequence, bounded to one line and a short span."""
    best = None
    max_span = min(96, len(token) * 3 + 12)
    exact = text.find(token)
    while exact >= 0:
        if exact == 0 or not text[exact - 1].isalnum():
            return 0, list(range(exact, exact + len(token)))
        exact = text.find(token, exact + 1)
    start = text.find(token[0])
    while start >= 0:
        positions = [start]
        end = min(len(text), start + max_span)
        line_end = text.find('\n', start, end)
        if line_end >= 0:
            end = line_end
        for char in token[1:]:
            pos = text.find(char, positions[-1] + 1, end)
            if pos < 0:
                break
            positions.append(pos)
        if len(positions) == len(token):
            gaps = positions[-1] - start + 1 - len(token)
            boundary = start == 0 or not text[start - 1].isalnum()
            score = gaps * 40 + (0 if boundary else 2)
            candidate = (score, positions)
            if best is None or candidate[0] < best[0]:
                best = candidate
                # Exact word-boundary occurrences were checked above. A very
                # compact abbreviation is already useful: avoid rescoring
                # thousands of repeated occurrences in long tool outputs.
                if boundary and gaps <= 2:
                    break
        start = text.find(token[0], start + 1)
    return best


def score_match(title, content, tokens):
    # A weak abbreviation in the title must not mask a stronger body match.
    # Prefer title matches only when their title bonus actually makes them
    # better; the second pass reuses token scoring through the bounded cache.
    result = _score_match(title, content, tokens)
    title_matches = [match_token(folded(title), token) for token in tokens]
    if any(match is not None and match[0] >= 20 for match in title_matches):
        body_result = _score_match('', content, tokens)
        if body_result is not None and (result is None or body_result[0] < result[0]):
            result = body_result
    return result


def _score_match(title, content, tokens):
    """Return score and original content highlight positions, or None.

    Tokens may match the title plus a single 240-character content window.
    This prevents unrelated letters across a huge conversation from matching.
    """
    title_fold = folded(title)
    title_matches = [match_token(title_fold, token) for token in tokens]
    missing = [i for i, match in enumerate(title_matches) if match is None]
    if not missing:
        return sum(match[0] for match in title_matches) - 20 * len(tokens), []
    normalized = folded(content)
    mapping = range(len(normalized))  # Map to original text only for selected results.
    if len(tokens) == 1:
        match = match_token(normalized, tokens[0])
        return (match[0], sorted(set(mapping[pos] for pos in match[1]))) if match else None
    global_matches = [match_token(normalized, tokens[i]) for i in missing]
    if any(match is None for match in global_matches):
        return None
    global_positions = [pos for match in global_matches for pos in match[1]]
    lo, hi = min(global_positions), max(global_positions)
    if hi - lo < 240 and '\n' not in normalized[lo:hi]:
        score = sum(match[0] for match in global_matches)
        score += sum(match[0] - 20 for match in title_matches if match is not None)
        return score + (hi - lo) / 240, sorted(set(mapping[pos] for pos in global_positions))
    best = None
    # Overlap guarantees any <=120-character span is contained in a window.
    for line in re.finditer(r'[^\n]+', normalized):
        for start in range(line.start(), line.end(), 120):
            window = normalized[start:min(start + 240, line.end())]
            matches = [match_token(window, tokens[i]) for i in missing]
            if any(match is None for match in matches):
                continue
            score = sum(match[0] for match in matches)
            score += sum(match[0] - 20 for match in title_matches if match is not None)
            positions = [start + pos for match in matches for pos in match[1]]
            score += (max(positions) - min(positions)) / 240
            candidate = (score, sorted(set(mapping[pos] for pos in positions)))
            if best is None or score < best[0]:
                best = candidate
    return best


def make_snippet(content, positions):
    start = max(0, (positions[0] if positions else 0) - 55)
    end = min(len(content), max(start + 200, (positions[-1] + 40) if positions else 0))
    selected = set(positions)
    parts = ['…' if start else '']
    marked = False
    for index in range(start, end):
        match = index in selected
        if match != marked:
            parts.append('«' if match else '»')
            marked = match
        parts.append(content[index])
    if marked:
        parts.append('»')
    if end < len(content):
        parts.append('…')
    return ''.join(parts)


def result_snippet(title, content, tokens, positions):
    if positions:
        if content.isascii():
            original_positions = positions
        else:
            # Highlight only the winning span: don't build a per-character map
            # for an entire long transcript merely to display a short snippet.
            wanted = set(positions)
            last = max(wanted)
            offset = 0
            original_positions = []
            for index, char in enumerate(content):
                width = 1 if char.isascii() else len(folded(char))
                if any(pos in wanted for pos in range(offset, offset + width)):
                    original_positions.append(index)
                offset += width
                if offset > last:
                    break
        return make_snippet(content, original_positions)
    normalized, mapping = mapped_fold(title)
    title_positions = sorted({mapping[pos] for token in tokens
                              for pos in (match_token(normalized, token) or (0, []))[1]})
    return 'Title: ' + make_snippet(title, title_positions)

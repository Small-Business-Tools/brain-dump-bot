from datetime import datetime


def calculate_scores(entries: list[dict], claude_scores: dict) -> dict:
    """
    Calculate the full score for an idea cluster.

    Dimensions:
        density     (30%) — computed from entries (count, span, depth)
        revenue_fit (25%) — from Claude
        effort      (25%) — from Claude (already inverted: 100 = easy)
        novelty     (20%) — from Claude

    Returns a dict ready to pass to store.save_scores().
    """
    entry_count = len(entries)
    span_days = _span_days(entries)
    depth = _depth_score(entries)

    density = _density_score(entry_count, span_days, depth)

    revenue_fit = float(claude_scores.get("revenue_fit", 50))
    effort = float(claude_scores.get("effort", 50))
    novelty = float(claude_scores.get("novelty", 50))

    total = round(
        density * 0.30
        + revenue_fit * 0.25
        + effort * 0.25
        + novelty * 0.20
    )

    return {
        "density": round(density),
        "revenue_fit": round(revenue_fit),
        "effort": round(effort),
        "novelty": round(novelty),
        "total": total,
        "entry_count": entry_count,
        "span_days": round(span_days, 1),
        "depth": round(depth),
    }


def _span_days(entries: list[dict]) -> float:
    """Days between first and last entry. 0 if only one entry."""
    if len(entries) < 2:
        return 0.0
    fmt = "%Y-%m-%dT%H:%M:%S.%f"
    def parse(s):
        # Handle both with and without microseconds
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")

    dates = sorted(parse(e["created_at"]) for e in entries)
    return (dates[-1] - dates[0]).total_seconds() / 86400


def _depth_score(entries: list[dict]) -> float:
    """
    Score 0-100 based on richness of content across all entries.

    Signals:
    - Total word count (normalised to ~500 words = 60 pts)
    - Specificity markers: numbers, named entities (capitalised words),
      URLs, question marks (indicates thinking), bullet-style detail
    """
    if not entries:
        return 0.0

    combined = " ".join(e["raw_text"] for e in entries)
    words = combined.split()
    word_count = len(words)

    # Word count contribution (0-60)
    word_score = min(word_count / 500 * 60, 60)

    # Specificity markers (0-40)
    import re
    numbers = len(re.findall(r'\b\d+[\d,.]*\b', combined))
    capitalised = len(re.findall(r'\b[A-Z][a-z]{2,}\b', combined))
    questions = combined.count("?")
    urls = len(re.findall(r'https?://', combined))

    specificity = min((numbers * 3) + (capitalised * 1.5) + (questions * 4) + (urls * 5), 40)

    return min(word_score + specificity, 100)


def _density_score(entry_count: int, span_days: float, depth: float) -> float:
    """
    Combine entry count, time span, and depth into a 0-100 density score.

    Weights:
        entry_count  40% — normalised to 20 entries = max
        span_days    35% — normalised to 90 days = max
        depth        25% — already 0-100
    """
    count_score = min(entry_count / 20, 1.0) * 100
    span_score = min(span_days / 90, 1.0) * 100

    return (
        count_score * 0.40
        + span_score * 0.35
        + depth * 0.25
    )

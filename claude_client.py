import os
import json
import logging
import asyncio
from anthropic import AsyncAnthropic, APIStatusError, APIConnectionError, APITimeoutError

from store import (
    save_entry, save_cluster, update_cluster,
    link_entry_to_cluster, get_all_clusters,
    get_cluster_entries, save_scores
)
from scorer import calculate_scores

logger = logging.getLogger(__name__)
client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1200
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds between retries

FALLBACK_CLUSTER_NAME = "Unprocessed ideas"
FALLBACK_CLUSTER_SUMMARY = "Ideas saved but not yet processed due to an error."


# ---------------------------------------------------------------------------
# Fallback cluster
# ---------------------------------------------------------------------------

async def get_or_create_fallback_cluster() -> int:
    """Return the ID of the fallback cluster, creating it if needed."""
    clusters = get_all_clusters()
    for c in clusters:
        if c["name"] == FALLBACK_CLUSTER_NAME:
            return c["id"]
    return save_cluster(FALLBACK_CLUSTER_NAME, FALLBACK_CLUSTER_SUMMARY, ["unprocessed"])


async def _save_to_fallback(entry_id: int):
    """Link a failed entry to the fallback cluster. Never raises."""
    try:
        cluster_id = await get_or_create_fallback_cluster()
        link_entry_to_cluster(cluster_id, entry_id)
    except Exception as e:
        logger.error(f"Failed to save entry {entry_id} to fallback cluster: {e}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def process_idea(raw_text: str) -> str:
    """
    Full pipeline for a new incoming idea.

    1. Save the raw entry immediately — no idea is ever lost.
    2. Call Claude with retry logic for transient API errors.
    3. On any failure, move the entry to the fallback cluster and
       return a clear message to the user.
    """
    entry_id = save_entry(raw_text)

    try:
        return await _process_with_retries(raw_text, entry_id)

    except json.JSONDecodeError as e:
        logger.error(f"Claude returned invalid JSON after all retries: {e}")
        await _save_to_fallback(entry_id)
        return (
            "✓ Your idea was saved, but I couldn't process it right now "
            "(Claude returned an unexpected response). "
            "It's in your *Unprocessed ideas* cluster — send /list to see it."
        )

    except ValueError as e:
        logger.error(f"Claude response failed validation: {e}")
        await _save_to_fallback(entry_id)
        return (
            "✓ Your idea was saved, but the response from Claude was incomplete. "
            "It's in your *Unprocessed ideas* cluster — send /list to see it."
        )

    except (APIStatusError, APIConnectionError, APITimeoutError) as e:
        logger.error(f"Anthropic API error after all retries: {e}")
        await _save_to_fallback(entry_id)
        return (
            "✓ Your idea was saved, but the AI service is currently unavailable. "
            "It's in your *Unprocessed ideas* cluster and will need manual reprocessing."
        )

    except Exception as e:
        logger.error(f"Unexpected error processing idea (entry {entry_id}): {type(e).__name__}: {e}")
        await _save_to_fallback(entry_id)
        return (
            "✓ Your idea was saved, but something unexpected went wrong processing it. "
            "It's in your *Unprocessed ideas* cluster — send /list to see it."
        )


# ---------------------------------------------------------------------------
# Retry wrapper
# ---------------------------------------------------------------------------

async def _process_with_retries(raw_text: str, entry_id: int) -> str:
    """
    Attempt _process_with_claude up to MAX_RETRIES times.
    Retries only on transient API errors; raises immediately for logic errors.
    """
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await _process_with_claude(raw_text, entry_id)

        except (APIConnectionError, APITimeoutError) as e:
            last_error = e
            if attempt < MAX_RETRIES:
                wait = RETRY_DELAY * attempt
                logger.warning(f"Transient API error on attempt {attempt}/{MAX_RETRIES}, retrying in {wait}s: {e}")
                await asyncio.sleep(wait)
            else:
                logger.error(f"All {MAX_RETRIES} attempts failed with transient error.")

        except APIStatusError as e:
            # 5xx = transient; 4xx = our fault, don't retry
            if e.status_code >= 500:
                last_error = e
                if attempt < MAX_RETRIES:
                    wait = RETRY_DELAY * attempt
                    logger.warning(f"API 5xx on attempt {attempt}/{MAX_RETRIES}, retrying in {wait}s: {e}")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"All {MAX_RETRIES} attempts failed with 5xx.")
            else:
                raise  # 4xx — raise immediately, no point retrying

        except (json.JSONDecodeError, ValueError):
            raise  # Logic errors — raise immediately to outer handler

    raise last_error


# ---------------------------------------------------------------------------
# Core Claude call
# ---------------------------------------------------------------------------

async def _process_with_claude(raw_text: str, entry_id: int) -> str:
    """Call Claude, parse and validate the response, update the store, return reply."""

    existing = get_all_clusters()
    active_clusters = [c for c in existing if c["name"] != FALLBACK_CLUSTER_NAME]
    valid_cluster_ids = {c["id"] for c in active_clusters}

    cluster_summary = "\n".join(
        f"- ID {c['id']}: {c['name']} — {c['summary']}"
        for c in active_clusters
    ) or "None yet."

    prompt = f"""You are managing a personal idea repository. A new idea has just arrived.

New idea:
\"\"\"{raw_text}\"\"\"

Existing idea clusters:
{cluster_summary}

Your job:
1. Decide if this idea belongs to an existing cluster (same theme, compatible, or an extension of one).
2. If yes, return the cluster ID to attach it to, and an updated summary.
3. If no, create a new cluster with a short name and summary.
4. Extract 2-4 tags (single words or short phrases).
5. Note any cross-links to OTHER clusters this idea connects to (not the one it belongs to).
6. Score the idea cluster on these dimensions (0-100):
   - revenue_fit: how clearly a monetisation path exists
   - effort: invert this — 100 means very low effort to build, 0 means enormous effort
   - novelty: how unique vs the existing clusters and general market

Return ONLY valid JSON in this exact shape, with no markdown or code fences:
{{
  "action": "add_to_existing" | "create_new",
  "cluster_id": <int or null>,
  "cluster_name": "<string>",
  "cluster_summary": "<one sentence>",
  "tags": ["tag1", "tag2"],
  "cross_links": [<cluster_id>, ...],
  "scores": {{
    "revenue_fit": <0-100>,
    "effort": <0-100>,
    "novelty": <0-100>
  }},
  "confirmation_note": "<one friendly sentence to send back to the user>"
}}"""

    response = await client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if Claude includes them despite instructions
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    result = json.loads(raw)  # raises JSONDecodeError if malformed — caught upstream

    # Validate required fields
    required = ["action", "cluster_name", "cluster_summary", "tags", "scores", "confirmation_note"]
    missing = [f for f in required if f not in result]
    if missing:
        raise ValueError(f"Claude response missing required fields: {missing}")

    # Validate scores shape
    score_fields = ["revenue_fit", "effort", "novelty"]
    missing_scores = [f for f in score_fields if f not in result.get("scores", {})]
    if missing_scores:
        raise ValueError(f"Claude response missing score fields: {missing_scores}")

    # Sanitise cross_links — drop any IDs that don't exist
    raw_cross_links = result.get("cross_links") or []
    cross_links = [cid for cid in raw_cross_links if cid in valid_cluster_ids]

    # Persist cluster
    if result["action"] == "add_to_existing" and result.get("cluster_id") in valid_cluster_ids:
        cluster_id = result["cluster_id"]
        update_cluster(
            cluster_id,
            result["cluster_name"],
            result["cluster_summary"],
            result["tags"]
        )
    else:
        cluster_id = save_cluster(
            result["cluster_name"],
            result["cluster_summary"],
            result["tags"]
        )

    link_entry_to_cluster(cluster_id, entry_id)

    # Recalculate density + full score
    entries = get_cluster_entries(cluster_id)
    scores = calculate_scores(entries, result["scores"])
    save_scores(cluster_id, scores)

    # Build reply
    reply_lines = [
        f"✓ *{result['cluster_name']}*",
        f"_{result['confirmation_note']}_",
        f"",
        f"Score: *{scores['total']}/100* · Entries: {scores['entry_count']}",
    ]

    if cross_links:
        linked_names = [c["name"] for c in active_clusters if c["id"] in cross_links]
        if linked_names:
            reply_lines.append(f"Connects to: {', '.join(linked_names)}")

    tags_str = " ".join(f"`{t}`" for t in result["tags"])
    reply_lines.append(f"Tags: {tags_str}")

    return "\n".join(reply_lines)

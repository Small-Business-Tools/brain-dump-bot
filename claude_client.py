import json
import os
from anthropic import AsyncAnthropic
from store import (
    save_entry,
    save_cluster,
    update_cluster,
    get_all_clusters,
    get_cluster_by_id,
    link_entry_to_cluster,
    get_cluster_entries,
    get_or_create_fallback_cluster,
    save_cluster_link,
    get_cluster_links,
)
from scorer import calculate_scores
from store import save_scores

MODEL = "claude-opus-4-5"
client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


async def process_idea(raw_text: str) -> str:
    """
    Full pipeline for a new idea:
      1. Save the raw entry immediately (never lost).
      2. Ask Claude to categorise, score, and find cross-links.
      3. Persist cluster + links.
      4. Build and return a Telegram reply.

    Any failure after step 1 routes to the fallback cluster so nothing is lost.
    """
    entry_id = save_entry(raw_text)

    try:
        return await _process_with_claude(raw_text, entry_id)
    except json.JSONDecodeError as e:
        await _save_to_fallback(entry_id)
        return (
            "⚠️ Idea saved, but Claude returned malformed JSON — check logs.\n"
            f"_Error: {e}_"
        )
    except ValueError as e:
        await _save_to_fallback(entry_id)
        return f"⚠️ Idea saved, but response was missing fields.\n_Error: {e}_"
    except Exception as e:
        await _save_to_fallback(entry_id)
        return f"⚠️ Idea saved, but something went wrong.\n_Error: {type(e).__name__}: {e}_"


async def _save_to_fallback(entry_id: int):
    fallback_id = get_or_create_fallback_cluster()
    link_entry_to_cluster(fallback_id, entry_id)


async def _process_with_claude(raw_text: str, entry_id: int) -> str:
    existing = get_all_clusters()

    cluster_summary = "\n".join(
        f"[ID {c['id']}] {c['name']}: {c['summary']} (tags: {', '.join(c['tags'])})"
        for c in existing
    ) if existing else "None yet."

    prompt = f"""You are managing a personal idea repository. A new idea has just arrived.

New idea:
\"\"\"{raw_text}\"\"\"

Existing idea clusters:
{cluster_summary}

Your job:
1. Decide if this idea belongs to an existing cluster (same theme, compatible, or an extension).
   - If yes: return that cluster's ID and an updated summary.
   - If no: create a new cluster with a short name and one-sentence summary.
2. Extract 2-4 tags (single words or short phrases).
3. Identify any CROSS-LINKS — other clusters (NOT the one this idea belongs to) that this
   idea meaningfully connects to. For each cross-link provide:
   - The cluster ID
   - A short reason (one sentence) explaining WHY they are connected. This will be shown
     directly to the user, so make it specific and insightful, not generic.
4. Score the idea cluster on these dimensions (0–100):
   - revenue_fit: how clearly a monetisation path exists
   - effort: inverted — 100 = very low effort, 0 = enormous effort
   - novelty: how unique vs existing clusters and the general market

Return ONLY valid JSON in this exact shape — no markdown, no prose:
{{
  "action": "add_to_existing" | "create_new",
  "cluster_id": <int or null>,
  "cluster_name": "<string>",
  "cluster_summary": "<one sentence>",
  "tags": ["tag1", "tag2"],
  "cross_links": [
    {{"cluster_id": <int>, "reason": "<one sentence>"}}
  ],
  "scores": {{
    "revenue_fit": <0-100>,
    "effort": <0-100>,
    "novelty": <0-100>
  }},
  "confirmation_note": "<one friendly sentence to send back to the user>"
}}

cross_links may be an empty list [] if there are no meaningful connections.
"""

    response = await client.messages.create(
        model=MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()

    # Strip markdown fences if Claude wraps the JSON anyway
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    result = json.loads(raw)

    required = ["action", "cluster_name", "cluster_summary", "tags", "scores", "confirmation_note"]
    missing = [f for f in required if f not in result]
    if missing:
        raise ValueError(f"Claude response missing fields: {missing}")

    # ── Persist cluster ──────────────────────────────────────────────────────
    if result["action"] == "add_to_existing" and result.get("cluster_id"):
        cluster_id = int(result["cluster_id"])
        update_cluster(cluster_id, result["cluster_name"], result["cluster_summary"], result["tags"])
    else:
        cluster_id = save_cluster(result["cluster_name"], result["cluster_summary"], result["tags"])

    link_entry_to_cluster(cluster_id, entry_id)

    # ── Recalculate density + full score ─────────────────────────────────────
    entries = get_cluster_entries(cluster_id)
    scores = calculate_scores(entries, result["scores"])
    save_scores(cluster_id, scores)

    # ── Persist cross-links ──────────────────────────────────────────────────
    raw_links = result.get("cross_links", [])

    # Support both old list-of-ints format and new list-of-objects format
    normalised_links = []
    for item in raw_links:
        if isinstance(item, int):
            normalised_links.append({"cluster_id": item, "reason": ""})
        elif isinstance(item, dict) and "cluster_id" in item:
            normalised_links.append(item)

    persisted_links = []
    for link in normalised_links:
        other_id = int(link["cluster_id"])
        reason = link.get("reason", "")
        other_cluster = get_cluster_by_id(other_id)
        if other_cluster is None:
            continue  # stale ID from Claude — skip silently
        save_cluster_link(cluster_id, other_id, reason)
        persisted_links.append({
            "name": other_cluster["name"],
            "reason": reason,
        })

    # ── Build Telegram reply ─────────────────────────────────────────────────
    reply = _build_reply(result, scores, persisted_links)
    return reply


def _build_reply(result: dict, scores: dict, persisted_links: list[dict]) -> str:
    """
    Compose the Telegram reply message. Cross-links are shown prominently
    with their reasons so they feel like a useful insight, not a footnote.
    """
    lines = [
        f"✓ *{result['cluster_name']}*",
        f"_{result['confirmation_note']}_",
        "",
        f"Score: *{scores['total']}/100* · Entries: {scores['entry_count']}",
    ]

    if persisted_links:
        lines.append("")
        if len(persisted_links) == 1:
            lines.append("🔗 *Connects to 1 existing idea:*")
        else:
            lines.append(f"🔗 *Connects to {len(persisted_links)} existing ideas:*")

        for link in persisted_links:
            name = link["name"]
            reason = link["reason"]
            if reason:
                lines.append(f"  • *{name}* — _{reason}_")
            else:
                lines.append(f"  • *{name}*")

    tags_str = " ".join(f"`{t}`" for t in result["tags"])
    lines.append("")
    lines.append(f"Tags: {tags_str}")

    return "\n".join(lines)

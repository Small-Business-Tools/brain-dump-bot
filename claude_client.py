import os
import json
import logging
from anthropic import AsyncAnthropic

from store import (
    save_entry, save_cluster, update_cluster,
    link_entry_to_cluster, get_all_clusters,
    get_cluster_entries, save_scores
)
from scorer import calculate_scores

logger = logging.getLogger(__name__)
client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL = "claude-opus-4-5"


async def process_idea(raw_text: str) -> str:
    """
    Full pipeline for a new incoming idea:
    1. Save the raw entry
    2. Ask Claude to categorise and match to existing clusters
    3. Update or create the cluster
    4. Recalculate scores
    5. Return a confirmation message with cross-links
    """
    entry_id = save_entry(raw_text)
    existing = get_all_clusters()

    cluster_summary = "\n".join(
        f"- ID {c['id']}: {c['name']} — {c['summary']}" for c in existing
    ) if existing else "None yet."

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

Return ONLY valid JSON in this exact shape:
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
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    result = json.loads(raw)

    # Persist cluster
    if result["action"] == "add_to_existing" and result.get("cluster_id"):
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

    if result.get("cross_links"):
        linked_names = [
            c["name"] for c in existing if c["id"] in result["cross_links"]
        ]
        if linked_names:
            reply_lines.append(f"Connects to: {', '.join(linked_names)}")

    tags_str = " ".join(f"`{t}`" for t in result["tags"])
    reply_lines.append(f"Tags: {tags_str}")

    return "\n".join(reply_lines)

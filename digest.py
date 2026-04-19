import os
import logging
from anthropic import AsyncAnthropic
from store import get_top_clusters, get_cluster_entries

logger = logging.getLogger(__name__)
client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL = "claude-opus-4-5"


async def send_digest() -> str:
    """
    Build and return a weekly digest of the top ideas.
    Called on-demand (/digest) or by the Sunday scheduler.
    """
    top = get_top_clusters(n=5)

    if not top:
        return "No ideas stored yet. Send me something this week!"

    # Build a rich context for Claude to write the digest
    cluster_details = []
    for c in top:
        entries = get_cluster_entries(c["id"])
        entry_texts = "\n".join(f"  - {e['raw_text'][:200]}" for e in entries[-5:])
        cluster_details.append(
            f"Cluster: {c['name']} (score {c['score']}/100, {c['entry_count']} entries)\n"
            f"Summary: {c['summary']}\n"
            f"Recent entries:\n{entry_texts}"
        )

    context = "\n\n---\n\n".join(cluster_details)

    prompt = f"""You are writing a concise weekly digest for someone building a personal idea repository.

Here are their top-scoring ideas this week:

{context}

Write a digest that:
1. Opens with one punchy sentence about what stands out this week.
2. Lists the top 3 ideas in order, each with:
   - The idea name (bold)
   - One sentence on WHY it scored well (density, revenue potential, or ease)
   - One concrete "next action" they could take to move it forward
3. Ends with one sentence spotting any pattern across the ideas.

Keep it under 300 words. Be direct and practical, not cheerleader-y.
Format it cleanly for Telegram (use *bold* for names, no HTML)."""

    response = await client.messages.create(
        model=MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )

    digest_text = response.content[0].text.strip()
    return f"*Weekly Idea Digest*\n\n{digest_text}"

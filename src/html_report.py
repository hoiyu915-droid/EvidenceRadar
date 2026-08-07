"""Self-contained HTML renderer built from the same Paper objects as Markdown."""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from . import events


def _e(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _paper_card(paper: Any, category: str, featured: bool) -> str:
    from . import formal_taxonomy, radar

    url = radar.primary_url(paper)
    title = _e(paper.title)
    title_html = f'<a href="{_e(url)}">{title}</a>' if url else title
    directions = formal_taxonomy.directions_for(paper)
    tags = [paper.evidence_tier, paper.study_design, *directions]
    event = events.display_event(paper)
    event_html = "<span>事件未確認</span>"
    if event:
        evidence_url = str(event.get("url") or "")
        evidence = f"{_e(event.get('source'))} · {_e(event.get('source_field'))}"
        if evidence_url:
            evidence = f'<a href="{_e(evidence_url)}">{evidence}</a>'
        event_html = (
            f"<strong>{_e(event.get('label'))}</strong> · {_e(event.get('occurred_at'))}"
            f"<br><small>{evidence} · {_e(event.get('precision'))} · {_e(event.get('confidence'))}</small>"
        )
    abstract = radar.truncate(paper.abstract, 520) if paper.abstract else "無摘要；需開啟原文判讀。"
    identifiers = radar.ids_line(paper).replace("`", "")
    classes = "paper featured" if featured else "paper"
    return (
        f'<article class="{classes}">'
        f'<div class="eyebrow">{_e(category)} · score {_e(f"{paper.total_score:.1f}")}</div>'
        f"<h3>{title_html}</h3>"
        f'<div class="chips">{"".join(f"<span>{_e(tag)}</span>" for tag in tags if tag)}</div>'
        f'<p class="event">{event_html}</p>'
        f'<p><strong>Why flagged</strong> — {_e(paper.one_line_reason)}</p>'
        f'<p>{_e(abstract)}</p>'
        f'<p class="meta">{_e(paper.journal_or_venue)} · {_e(paper.publication_date)}<br>{_e(identifiers)}</p>'
        "</article>"
    )


def render_html(
    generated_at: datetime,
    featured: dict[str, dict[str, list[Any]]],
    candidate_pool: list[Any],
    *,
    category_order: tuple[str, ...],
    category_titles: dict[str, str],
    window_hours: int,
    cutoff: datetime,
    retrieved_count: int,
    event_qualified_count: int,
    warnings: list[str],
) -> str:
    featured_ids = {
        paper.identity_key()
        for category in featured.values()
        for papers in category.values()
        for paper in papers
    }
    buckets = {category: [] for category in category_order}
    from . import categories

    for paper in candidate_pool:
        category = categories.category_for(paper)
        if category:
            buckets[category].append(paper)
    sections: list[str] = []
    for category in category_order:
        cards = "".join(
            _paper_card(paper, category_titles[category], paper.identity_key() in featured_ids)
            for paper in buckets[category]
        ) or '<p class="empty">本次沒有符合門檻的研究。</p>'
        sections.append(
            f'<section><div class="section-head"><h2>{_e(category_titles[category])}</h2>'
            f'<span>{len(buckets[category])} candidates</span></div><div class="grid">{cards}</div></section>'
        )
    warning_html = "；".join(_e(item) for item in warnings) if warnings else "None"
    return f'''<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Evidence Radar — {_e(generated_at.strftime('%Y-%m-%d %H:%M'))}</title>
<style>
:root{{--ink:#17211b;--muted:#657168;--paper:#f6f4ed;--card:#fff;--line:#d8ddd5;--accent:#245c46;--soft:#e5efe9}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.65 system-ui,-apple-system,"Noto Sans TC",sans-serif}}
main{{max-width:1180px;margin:auto;padding:42px 24px 80px}} header{{border-bottom:2px solid var(--ink);padding-bottom:24px;margin-bottom:34px}}
h1{{font-size:clamp(34px,6vw,68px);line-height:1;margin:.15em 0}} h2{{font-size:26px;margin:0}} h3{{font-size:19px;line-height:1.35;margin:8px 0}}
a{{color:var(--accent)}} .kicker,.eyebrow{{text-transform:uppercase;letter-spacing:.08em;font-size:12px;font-weight:700;color:var(--accent)}}
.stats{{display:flex;gap:12px;flex-wrap:wrap;margin-top:18px}} .stats span,.chips span{{background:var(--soft);border-radius:999px;padding:4px 10px}}
section{{margin-top:42px}} .section-head{{display:flex;justify-content:space-between;align-items:end;border-bottom:1px solid var(--line);padding-bottom:8px;margin-bottom:16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}} .paper{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px}}
.paper.featured{{border-top:5px solid var(--accent);padding-top:14px}} .chips{{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}} .chips span{{font-size:11px}}
.event{{border-left:3px solid var(--accent);padding-left:10px}} .meta,small,.empty{{color:var(--muted)}} footer{{margin-top:48px;border-top:1px solid var(--line);padding-top:18px;color:var(--muted)}}
@media print{{body{{background:#fff}}main{{max-width:none}}.paper{{break-inside:avoid}}}}
</style></head><body><main>
<header><div class="kicker">Verified-event literature radar</div><h1>Evidence Radar</h1>
<p>{_e(generated_at.isoformat())} · Asia/Tokyo<br>Rolling {_e(window_hours)}h: {_e(cutoff.isoformat())} → {_e(generated_at.isoformat())}</p>
<div class="stats"><span>{retrieved_count} retrieved</span><span>{event_qualified_count} event-qualified</span><span>{len(candidate_pool)} candidates</span><span>{len(featured_ids)} featured</span></div></header>
{''.join(sections)}
<footer>AUTO-TRIAGE：事件已按來源欄位核對；研究品質、全文斷言、校正／撤稿仍須人工審核。<br>Warnings: {warning_html}</footer>
</main></body></html>'''

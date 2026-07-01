from __future__ import annotations

import os
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.feeds import Article
from app.summarizer import (
    ClaudeApiError,
    _clean_title,
    _strip_html,
    generate_text,
    has_claude_api_key,
)


@dataclass
class RankedArticle:
    article: Article
    impact_score: int
    impact_reason: str


def _fallback_score(article: Article, keywords: list[str] | None = None) -> tuple[int, str]:
    text = _strip_html(f"{article.title} {article.summary_text}").lower()
    score = 20
    reasons: list[str] = []

    if "phase 3" in text or "phase iii" in text:
        score += 40
        reasons.append("第3相")
    elif "phase 2" in text or "phase ii" in text:
        score += 22
        reasons.append("第2相")

    if "randomi" in text:
        score += 12
    if any(term in text for term in ("overall survival", "progression-free", " pfs", " os")):
        score += 12
        reasons.append("生存期間")
    if any(
        term in text
        for term in (
            "first-line",
            "first line",
            "standard of care",
            "standard therapy",
            "guideline",
            "approval",
            "regulatory",
        )
    ):
        score += 10
        reasons.append("標準治療関連")
    if any(term in text for term in ("noninferior", "superiority", "hazard ratio")):
        score += 8

    if keywords:
        from app.filter import matches_user_keywords

        if matches_user_keywords(article, keywords):
            score += 5

    if any(
        term in text
        for term in ("correction", "erratum", "correspondence", "comment", "editorial", "viewpoint")
    ):
        score -= 30
        reasons = ["低インパクト"]

    reason = "・".join(reasons[:2]) if reasons else "キーワードから推定"
    return max(1, min(100, score)), reason[:40]


def _build_ranking_prompt(articles: list[Article]) -> str:
    lines = []
    for index, article in enumerate(articles):
        title = _clean_title(article.title)
        summary = _strip_html(article.summary_text)[:400]
        lines.append(f"{index}. 出典:{article.source_name}\n   タイトル:{title}\n   概要:{summary}")

    joined = "\n\n".join(lines)
    return (
        "あなたは腫瘍内科の専門医です。"
        "以下の論文について、がん薬物療法の「標準治療を変える可能性（臨床的インパクト）」が"
        "大きい順に評価してください。\n\n"
        "評価の目安:\n"
        "- 第3相試験で主要評価項目が達成し、ガイドラインや標準治療の変更が期待される → 高得点\n"
        "- 新薬・新コンビネーションの承認や適応拡大に直結する結果 → 高得点\n"
        "- 第2相試験でも画期的な有効性 → 中〜高得点\n"
        "- コメンタリー・訂正・手紙・叙述的レビュー → 低得点\n\n"
        "各論文に1〜100の整数スコアと、20字以内の日本語理由を付けてください。\n"
        'JSON配列のみ返してください: [{"index":0,"score":85,"reason":"第3相でOS改善"}, ...]\n\n'
        f"{joined}"
    )


def _parse_ranking_response(raw: str, article_count: int) -> dict[int, tuple[int, str]]:
    match = re.search(r"\[[\s\S]*\]", raw)
    if not match:
        return {}

    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError:
        return {}

    results: dict[int, tuple[int, str]] = {}
    if not isinstance(parsed, list):
        return results

    for item in parsed:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        score = item.get("score")
        reason = str(item.get("reason", "")).strip()
        if not isinstance(index, int) or not isinstance(score, (int, float)):
            continue
        if 0 <= index < article_count:
            results[index] = (max(1, min(100, int(score))), reason[:40] or "臨床インパクト評価")

    return results


async def rank_by_clinical_impact(
    articles: list[Article],
    keywords: list[str] | None = None,
) -> list[RankedArticle]:
    if not articles:
        return []

    use_ai = os.getenv("CLAUDE_AI_RANKING", "").lower() in ("1", "true", "yes")
    if not use_ai or not has_claude_api_key():
        ranked = []
        for article in articles:
            score, reason = _fallback_score(article, keywords)
            ranked.append(RankedArticle(article=article, impact_score=score, impact_reason=reason))
        ranked.sort(
            key=lambda item: (item.impact_score, item.article.published or datetime.min.replace(tzinfo=timezone.utc)),
            reverse=True,
        )
        return ranked

    prompt = _build_ranking_prompt(articles)
    try:
        raw = await generate_text(
            prompt,
            system="腫瘍内科医として論文の臨床インパクトを評価します。",
            temperature=0.2,
            max_output_tokens=1536,
        )
    except ClaudeApiError:
        ranked = []
        for article in articles:
            score, reason = _fallback_score(article, keywords)
            ranked.append(RankedArticle(article=article, impact_score=score, impact_reason=reason))
        ranked.sort(
            key=lambda item: (item.impact_score, item.article.published or datetime.min.replace(tzinfo=timezone.utc)),
            reverse=True,
        )
        return ranked

    scores = _parse_ranking_response(raw, len(articles))

    ranked: list[RankedArticle] = []
    for index, article in enumerate(articles):
        if index in scores:
            score, reason = scores[index]
        else:
            score, reason = _fallback_score(article, keywords)
        ranked.append(
            RankedArticle(article=article, impact_score=score, impact_reason=reason)
        )

    ranked.sort(
        key=lambda item: (item.impact_score, item.article.published or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )
    return ranked

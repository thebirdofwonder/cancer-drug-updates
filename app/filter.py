from __future__ import annotations

import re
from datetime import datetime, timezone
from html import unescape

from app.feeds import Article
from app.ranking import _fallback_score

_MIN_DATE = datetime.min.replace(tzinfo=timezone.utc)

CANCER_TERMS = (
    "cancer",
    "carcinoma",
    "oncolog",
    "tumor",
    "tumour",
    "neoplasm",
    "malignan",
    "lymphoma",
    "leukemia",
    "leukaemia",
    "melanoma",
    "sarcoma",
    "metastat",
)

DRUG_THERAPY_TERMS = (
    "drug",
    "therapy",
    "chemotherap",
    "immunotherap",
    "inhibitor",
    "antibody",
    "treatment",
    "pharmac",
    "trial",
    "efficacy",
    "survival",
    "combination",
    "regimen",
    "dose",
    "adc",
    "checkpoint",
    "pd-1",
    "pd-l1",
    "ctla-4",
    "her2",
    "egfr",
    "alk",
    "braf",
    "kras",
    "parp",
    "cdk4",
    "cdk6",
    "car-t",
    "targeted",
    "antineoplastic",
    "systemic",
)


def _strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def parse_keywords(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def matches_user_keywords(article: Article, keywords: list[str]) -> bool:
    if not keywords:
        return False
    text = _strip_html(f"{article.title} {article.summary_text}").lower()
    return any(keyword.lower() in text for keyword in keywords)


def relevance_score(article: Article, keywords: list[str] | None = None) -> int:
    text = _strip_html(f"{article.title} {article.summary_text}")
    score = 0

    has_cancer = _contains_any(text, CANCER_TERMS)
    has_drug = _contains_any(text, DRUG_THERAPY_TERMS)

    if article.oncology_focused:
        score += 3
        if has_drug:
            score += 4
        elif has_cancer:
            score += 2
        else:
            score += 1
    else:
        if has_cancer and has_drug:
            score += 8
        elif has_cancer:
            score += 3
        elif has_drug:
            score += 1

    if "randomized" in text.lower() or "phase" in text.lower():
        score += 2

    lowered = text.lower()
    if any(
        term in lowered
        for term in ("correction", "erratum", "correspondence", "editorial")
    ):
        score -= 3

    if keywords and matches_user_keywords(article, keywords):
        score += 5

    return score


def is_relevant(article: Article, keywords: list[str] | None = None) -> bool:
    if keywords and not matches_user_keywords(article, keywords):
        return False
    if article.oncology_focused:
        return relevance_score(article) >= 3
    return relevance_score(article) >= 5


def _dedupe_relevant(
    articles: list[Article],
    keywords: list[str] | None = None,
) -> list[Article]:
    relevant = [article for article in articles if is_relevant(article, keywords)]
    relevant.sort(
        key=lambda item: (
            _fallback_score(item, keywords)[0],
            item.published or _MIN_DATE,
        ),
        reverse=True,
    )

    seen_links: set[str] = set()
    unique: list[Article] = []
    for article in relevant:
        if article.link in seen_links:
            continue
        seen_links.add(article.link)
        unique.append(article)

    return unique


def select_candidates(
    articles: list[Article],
    pool_size: int,
    keywords: list[str] | None = None,
) -> list[Article]:
    return _dedupe_relevant(articles, keywords)[:pool_size]


def select_articles(
    articles: list[Article],
    limit: int,
    keywords: list[str] | None = None,
) -> list[Article]:
    return select_candidates(articles, limit, keywords)

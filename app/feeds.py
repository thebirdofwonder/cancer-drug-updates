from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional

import feedparser
import httpx

USER_AGENT = (
    "Mozilla/5.0 (compatible; CancerDrugUpdates/1.0; +https://github.com/local)"
)

SOURCES: list[dict[str, str | bool]] = [
    # General medicine
    {
        "id": "nejm",
        "name": "New England Journal of Medicine",
        "url": "https://www.nejm.org/action/showFeed?jc=nejm&type=etoc&feed=rss",
        "oncology_focused": False,
    },
    {
        "id": "bmj",
        "name": "The BMJ",
        "url": "https://www.bmj.com/rss.xml",
        "oncology_focused": False,
    },
    {
        "id": "jama",
        "name": "JAMA",
        "url": "https://jamanetwork.com/rss/site_3/67.xml",
        "oncology_focused": False,
    },
    {
        "id": "jamaonc",
        "name": "JAMA Oncology",
        "url": "https://jamanetwork.com/rss/site_159/174.xml",
        "oncology_focused": True,
    },
    {
        "id": "lancet",
        "name": "The Lancet",
        "url": "https://www.thelancet.com/rssfeed/lancet_current.xml",
        "oncology_focused": False,
    },
    # Oncology journals
    {
        "id": "lanonc",
        "name": "The Lancet Oncology",
        "url": "https://www.thelancet.com/rssfeed/lanonc_current.xml",
        "oncology_focused": True,
    },
    {
        "id": "jco",
        "name": "Journal of Clinical Oncology",
        "url": "https://ascopubs.org/action/showFeed?type=etoc&feed=rss&jc=jco",
        "oncology_focused": True,
    },
    {
        "id": "annonc",
        "name": "Annals of Oncology",
        "url": "https://www.annalsofoncology.org/action/showFeed?type=etoc&feed=rss&jc=annonc",
        "oncology_focused": True,
    },
    # Nature / Cell portfolio
    {
        "id": "nature",
        "name": "Nature",
        "url": "https://feeds.nature.com/nature/rss/current",
        "oncology_focused": False,
    },
    {
        "id": "natmed",
        "name": "Nature Medicine",
        "url": "https://feeds.nature.com/nm/rss/current",
        "oncology_focused": False,
    },
    {
        "id": "cell",
        "name": "Cell",
        "url": "https://www.cell.com/action/showFeed?type=etoc&feed=rss&jc=cell",
        "oncology_focused": False,
    },
    {
        "id": "cancercell",
        "name": "Cancer Cell",
        "url": "https://www.cell.com/action/showFeed?type=etoc&feed=rss&jc=ccell",
        "oncology_focused": True,
    },
]

SOURCE_ABBREV: dict[str, str] = {
    "nejm": "NEJM",
    "bmj": "BMJ",
    "jama": "JAMA",
    "jamaonc": "JAMA Oncology",
    "lancet": "Lancet",
    "lanonc": "Lancet Oncology",
    "jco": "JCO",
    "annonc": "Ann Oncol",
    "nature": "Nature",
    "natmed": "Nat Med",
    "cell": "Cell",
    "cancercell": "Cancer Cell",
}


def source_abbrev(source_id: str, source_name: str = "") -> str:
    if source_id in SOURCE_ABBREV:
        return SOURCE_ABBREV[source_id]
    if source_id.startswith("custom-"):
        return "Custom"
    return source_name or source_id


def format_published_date(published: Optional[datetime]) -> Optional[str]:
    if not published:
        return None
    return published.strftime("%Y-%m-%d")


@dataclass
class Article:
    id: str
    title: str
    link: str
    source_id: str
    source_name: str
    summary_text: str
    published: Optional[datetime]
    oncology_focused: bool


def _parse_published(entry: dict[str, Any]) -> Optional[datetime]:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pass
    for key in ("published", "updated"):
        raw = entry.get(key)
        if raw:
            try:
                return parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                pass
    return None


def _entry_summary(entry: dict[str, Any]) -> str:
    for key in ("summary", "description", "content"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, dict):
                text = first.get("value", "")
                if text.strip():
                    return text.strip()
    return ""


async def fetch_feed(source: dict[str, str], client: httpx.AsyncClient) -> list[Article]:
    response = await client.get(
        source["url"],
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=30.0,
    )
    response.raise_for_status()
    parsed = feedparser.parse(response.text)
    articles: list[Article] = []

    for index, entry in enumerate(parsed.entries):
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue

        published = _parse_published(entry)
        summary_text = _entry_summary(entry)
        article_id = f"{source['id']}-{index}-{hash(link) & 0xFFFFFFFF:08x}"

        articles.append(
            Article(
                id=article_id,
                title=title,
                link=link,
                source_id=source["id"],
                source_name=source["name"],
                summary_text=summary_text,
                published=published,
                oncology_focused=bool(source["oncology_focused"]),
            )
        )

    return articles


async def fetch_selected_feeds(
    source_ids: list[str],
    custom_urls: list[str],
) -> list[Article]:
    sources_to_fetch: list[dict[str, str | bool]] = [
        source for source in SOURCES if source["id"] in source_ids
    ]

    for index, url in enumerate(custom_urls):
        sources_to_fetch.append(
            {
                "id": f"custom-{index}",
                "name": url,
                "url": url,
                "oncology_focused": False,
            }
        )

    async with httpx.AsyncClient() as client:
        results: list[Article] = []
        for source in sources_to_fetch:
            try:
                results.extend(await fetch_feed(source, client))  # type: ignore[arg-type]
            except Exception:
                continue
        return results


async def fetch_all_feeds() -> list[Article]:
    return await fetch_selected_feeds(
        [source["id"] for source in SOURCES],
        [],
    )

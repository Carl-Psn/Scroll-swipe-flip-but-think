"""Session orchestration shared by Streamlit and FastAPI."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from verto.cache import clear_feed_cache
from verto.core import (
    DEFAULT_ACTIVE_PACKAGES,
    NOTEBOOKLM_URL,
    _collecter_lot,
    _feeds_cache_key,
    _is_summary_error,
    _jobs_from_feeds_key,
    _lister_sources,
    _normalize_active_packages,
    _packages_payload,
    obtenir_paragraphes,
    resume_pour_url,
)


def default_state() -> dict[str, Any]:
    return {
        "enrich_text": {},
        "enrich_summary": {},
        "custom_feeds": [],
        "disabled_feeds": [],
        "active_packages": DEFAULT_ACTIVE_PACKAGES.copy(),
        "articles_feeds_key": None,
        "articles_pool": [],
        "feed_offsets": {},
        "feeds_has_more": False,
        "gemini_api_key": "",
    }


@dataclass
class VertoSession:
    enrich_text: dict[str, list[str]] = field(default_factory=dict)
    enrich_summary: dict[str, dict[str, Any]] = field(default_factory=dict)
    custom_feeds: list[dict[str, str]] = field(default_factory=list)
    disabled_feeds: list[str] = field(default_factory=list)
    active_packages: list[str] = field(default_factory=lambda: DEFAULT_ACTIVE_PACKAGES.copy())
    articles_feeds_key: str | None = None
    articles_pool: list[dict[str, Any]] = field(default_factory=list)
    feed_offsets: dict[str, int] = field(default_factory=dict)
    feeds_has_more: bool = False
    gemini_api_key: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "VertoSession":
        base = default_state()
        if data:
            base.update(data)
        return cls(
            enrich_text=base["enrich_text"],
            enrich_summary=base["enrich_summary"],
            custom_feeds=base["custom_feeds"],
            disabled_feeds=base["disabled_feeds"],
            active_packages=_normalize_active_packages(base["active_packages"]),
            articles_feeds_key=base["articles_feeds_key"],
            articles_pool=base["articles_pool"],
            feed_offsets=base["feed_offsets"],
            feeds_has_more=base["feeds_has_more"],
            gemini_api_key=base.get("gemini_api_key", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enrich_text": self.enrich_text,
            "enrich_summary": self.enrich_summary,
            "custom_feeds": self.custom_feeds,
            "disabled_feeds": self.disabled_feeds,
            "active_packages": self.active_packages,
            "articles_feeds_key": self.articles_feeds_key,
            "articles_pool": self.articles_pool,
            "feed_offsets": self.feed_offsets,
            "feeds_has_more": self.feeds_has_more,
            "gemini_api_key": self.gemini_api_key,
        }

    def feeds_key(self) -> str:
        return _feeds_cache_key(
            self.custom_feeds,
            self.disabled_feeds,
            self.active_packages,
        )

    def build_enrichments(self) -> dict[str, Any]:
        return {
            "text": self.enrich_text,
            "summary": self.enrich_summary,
            "notebooklm": NOTEBOOKLM_URL,
            "custom_feeds": self.custom_feeds,
            "feed_packages": _packages_payload(),
            "active_packages": self.active_packages,
            "disabled_feeds": self.disabled_feeds,
            "has_more": self.feeds_has_more,
            "sources": _lister_sources(
                self.active_packages,
                self.custom_feeds,
                self.disabled_feeds,
            ),
        }

    def bootstrap_payload(self) -> dict[str, Any]:
        self.ensure_articles()
        return {
            "articles": deepcopy(self.articles_pool),
            "enrichments": self.build_enrichments(),
        }

    def ensure_articles(self) -> None:
        key = self.feeds_key()
        if self.articles_feeds_key != key:
            self._reset_articles_pool(key)

    def _reset_articles_pool(self, feeds_key: str) -> None:
        jobs = _jobs_from_feeds_key(feeds_key)
        batch, has_more, offsets = _collecter_lot(jobs, {})
        self.articles_feeds_key = feeds_key
        self.feed_offsets = offsets
        self.articles_pool = batch
        self.feeds_has_more = has_more

    def update_settings(self, gemini_api_key: str | None) -> None:
        self.gemini_api_key = (gemini_api_key or "").strip()

    def update_feeds(
        self,
        feeds: list[dict[str, str]] | None,
        disabled_feeds: list[str] | None,
        active_packages: list[str] | None,
    ) -> bool:
        new_feeds = feeds or []
        new_disabled = disabled_feeds or []
        new_packages = _normalize_active_packages(
            active_packages or DEFAULT_ACTIVE_PACKAGES
        )
        changed = (
            new_feeds != self.custom_feeds
            or new_disabled != self.disabled_feeds
            or new_packages != self.active_packages
        )
        if not changed:
            return False
        self.custom_feeds = new_feeds
        self.disabled_feeds = new_disabled
        self.active_packages = new_packages
        clear_feed_cache()
        self.articles_feeds_key = None
        self.ensure_articles()
        return True

    def load_more(self) -> None:
        jobs = _jobs_from_feeds_key(self.feeds_key())
        batch, has_more, offsets = _collecter_lot(jobs, self.feed_offsets)
        if batch:
            seen = {a["id"] for a in self.articles_pool}
            self.articles_pool.extend(a for a in batch if a["id"] not in seen)
        self.feed_offsets = offsets
        self.feeds_has_more = has_more

    def article_index(self) -> dict[str, dict[str, Any]]:
        return {a["id"]: a for a in self.articles_pool}

    def enrich_text_for(self, article_id: str, article: dict[str, Any]) -> list[str]:
        if article_id not in self.enrich_text:
            self.enrich_text[article_id] = obtenir_paragraphes(article)
        return self.enrich_text[article_id]

    def enrich_summary_for(
        self,
        article_id: str,
        article: dict[str, Any],
        n_points: int = 5,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        existing = self.enrich_summary.get(article_id)
        existing_text = (
            existing.get("text") if isinstance(existing, dict) else existing
        )
        key = (api_key or "").strip() or self.gemini_api_key
        if key:
            self.gemini_api_key = key
        should_regenerate = (
            article_id not in self.enrich_summary
            or _is_summary_error(existing_text)
        )
        if should_regenerate:
            self.enrich_summary[article_id] = {
                "text": resume_pour_url(
                    article["link"],
                    article.get("summary_html", ""),
                    n_points,
                    api_key=key,
                ),
                "points": n_points,
            }
        return self.enrich_summary[article_id]

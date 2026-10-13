"""Favoritos y recientes en ~/.config/gifteando/library.json (fuera del repo)."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from gifteando.models import GifItem

LIBRARY_PATH = Path.home() / ".config" / "gifteando" / "library.json"
MAX_FAVORITES = 100
MAX_RECENTS = 40

_FREE_OK = re.compile(
    r"cc0|public domain|pdm|cc[\s\-]?by(?:[\s\-]?sa)?\b|/zero/|/publicdomain/",
    re.IGNORECASE,
)
_FREE_BLOCK = re.compile(
    r"\bnc\b|/nc[\-/]|by-nc|non[\s\-]?commercial|\bnd\b|/nd[\-/]|by-nd|no[\s\-]?deriv",
    re.IGNORECASE,
)


def is_freely_reusable(item: GifItem) -> bool:
    """True si hay licencia clara tipo CC0, dominio público, CC BY o CC BY-SA."""
    blob = f"{item.license_name} {item.license_url}".strip()
    if not blob:
        return False
    if _FREE_BLOCK.search(blob):
        return False
    return bool(_FREE_OK.search(blob))


def attribution_text(item: GifItem) -> str:
    parts = [item.title.strip() or "GIF"]
    if item.author:
        parts.append(f"Autor: {item.author}")
    if item.license_name:
        parts.append(f"Licencia: {item.license_name}")
        if item.license_url:
            parts.append(item.license_url)
    link = item.page_url or item.original_url or item.download_url
    if link:
        parts.append(link)
    return " · ".join(parts)


def _item_key(item: GifItem) -> tuple[str, str]:
    return (item.source, item.id)


def _from_dict(raw: object) -> GifItem | None:
    if not isinstance(raw, dict):
        return None
    source = str(raw.get("source") or "").strip()
    item_id = str(raw.get("id") or "").strip()
    preview = str(raw.get("preview_url") or "").strip()
    download = str(raw.get("download_url") or "").strip()
    if not source or not item_id or not preview or not download:
        return None
    return GifItem(
        id=item_id,
        title=str(raw.get("title") or "GIF"),
        preview_url=preview,
        download_url=download,
        source=source,
        attribution=str(raw.get("attribution") or ""),
        original_url=str(raw.get("original_url") or ""),
        page_url=str(raw.get("page_url") or ""),
        author=str(raw.get("author") or ""),
        license_name=str(raw.get("license_name") or ""),
        license_url=str(raw.get("license_url") or ""),
    )


def _load_raw() -> dict:
    if not LIBRARY_PATH.is_file():
        return {"favorites": [], "recents": [], "free_only": False}
    try:
        raw = json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"favorites": [], "recents": [], "free_only": False}
    if not isinstance(raw, dict):
        return {"favorites": [], "recents": [], "free_only": False}
    return raw


def _save_raw(payload: dict) -> None:
    LIBRARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    LIBRARY_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    LIBRARY_PATH.chmod(0o600)


def load_favorites() -> list[GifItem]:
    items = [_from_dict(entry) for entry in _load_raw().get("favorites") or []]
    return [item for item in items if item is not None]


def load_recents() -> list[GifItem]:
    items = [_from_dict(entry) for entry in _load_raw().get("recents") or []]
    return [item for item in items if item is not None]


def load_free_only() -> bool:
    return bool(_load_raw().get("free_only"))


def save_free_only(value: bool) -> None:
    raw = _load_raw()
    raw["free_only"] = bool(value)
    favorites = [_from_dict(entry) for entry in raw.get("favorites") or []]
    recents = [_from_dict(entry) for entry in raw.get("recents") or []]
    raw["favorites"] = [asdict(item) for item in favorites if item is not None]
    raw["recents"] = [asdict(item) for item in recents if item is not None]
    _save_raw(raw)


def is_favorite(item: GifItem) -> bool:
    key = _item_key(item)
    return any(_item_key(fav) == key for fav in load_favorites())


def toggle_favorite(item: GifItem) -> bool:
    """Añade o quita de favoritos. Devuelve True si queda como favorito."""
    raw = _load_raw()
    favorites = [entry for entry in (_from_dict(x) for x in raw.get("favorites") or []) if entry]
    key = _item_key(item)
    kept = [fav for fav in favorites if _item_key(fav) != key]
    now_favorite = len(kept) == len(favorites)
    if now_favorite:
        kept.insert(0, item)
        kept = kept[:MAX_FAVORITES]
    raw["favorites"] = [asdict(fav) for fav in kept]
    recents = [entry for entry in (_from_dict(x) for x in raw.get("recents") or []) if entry]
    raw["recents"] = [asdict(entry) for entry in recents]
    raw["free_only"] = bool(raw.get("free_only"))
    _save_raw(raw)
    return now_favorite


def remember_recent(item: GifItem) -> None:
    raw = _load_raw()
    favorites = [entry for entry in (_from_dict(x) for x in raw.get("favorites") or []) if entry]
    recents = [entry for entry in (_from_dict(x) for x in raw.get("recents") or []) if entry]
    key = _item_key(item)
    recents = [entry for entry in recents if _item_key(entry) != key]
    recents.insert(0, item)
    raw["favorites"] = [asdict(fav) for fav in favorites]
    raw["recents"] = [asdict(entry) for entry in recents[:MAX_RECENTS]]
    raw["free_only"] = bool(raw.get("free_only"))
    _save_raw(raw)

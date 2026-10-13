from dataclasses import dataclass


@dataclass(frozen=True)
class GifItem:
    id: str
    title: str
    preview_url: str
    download_url: str
    source: str
    attribution: str = ""
    original_url: str = ""
    page_url: str = ""
    author: str = ""
    license_name: str = ""
    license_url: str = ""


@dataclass(frozen=True)
class SearchPage:
    items: list[GifItem]
    more: dict[str, int | str | None]
    notice: str = ""

    @property
    def has_more(self) -> bool:
        return bool(self.more)

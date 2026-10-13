import asyncio
import html
import re
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

import httpx

from gifteando.config import ApiKeys
from gifteando.models import GifItem, SearchPage

GIPHY_SEARCH_URL = "https://api.giphy.com/v1/gifs/search"
KLIPY_SEARCH_URL = "https://api.klipy.com/api/v1/{api_key}/gifs/search"
GIFCITIES_SEARCH_URL = "https://gifcities.archive.org/api/v1/gifsearch"
OPENVERSE_SEARCH_URL = "https://api.openverse.org/v1/images/"
WIKIMEDIA_API_URL = "https://commons.wikimedia.org/w/api.php"
WAYBACK_GIF_URL = "https://web.archive.org/web/"
PAGE_SIZE = 20
KEYED_SOURCES = {"giphy": "Giphy", "klipy": "KLIPY"}
FREE_SOURCES = ("openverse", "gifcities", "wikimedia")
_TAGS = re.compile(r"<[^>]+>")
_OPENVERSE_LICENSES = {
    "CC0": "CC0",
    "PDM": "Public Domain Mark",
    "BY": "CC BY",
    "BY-SA": "CC BY-SA",
    "BY-NC": "CC BY-NC",
    "BY-ND": "CC BY-ND",
    "BY-NC-SA": "CC BY-NC-SA",
    "BY-NC-ND": "CC BY-NC-ND",
}


class ProviderError(Exception):
    """Error devuelto por un portal o por la red."""


class MissingKeyError(ProviderError):
    """No hay clave para ninguno de los portales elegidos."""


class RateLimitError(ProviderError):
    """El portal ha limitado el número de búsquedas."""


def _limited(source_name: str) -> RateLimitError:
    return RateLimitError(
        f"{source_name} ha superado el límite de búsquedas por hora. Espera un rato o usa otro portal."
    )


SOURCE_LABELS = {
    "giphy": "Giphy",
    "klipy": "KLIPY",
    "openverse": "Openverse",
    "gifcities": "GifCities",
    "wikimedia": "Wikimedia Commons",
}


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _plain(value: object) -> str:
    text = html.unescape(_text(value))
    text = _TAGS.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _http_url(value: object) -> str:
    url = _text(value)
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("http://", "https://")):
        return ""
    parts = urlsplit(url)
    kept = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def _openverse_license(entry: dict) -> tuple[str, str]:
    code = _text(entry.get("license")).upper()
    version = _text(entry.get("license_version"))
    label = _OPENVERSE_LICENSES.get(code, code)
    if version and label:
        label = f"{label} {version}"
    return label, _http_url(entry.get("license_url"))


def _first_url(images: dict, *names: str) -> str:
    for name in names:
        entry = images.get(name)
        if isinstance(entry, dict):
            url = _text(entry.get("url"))
            if url:
                return url
    return ""


def parse_giphy(payload: dict, offset: int) -> tuple[list[GifItem], int | None, bool]:
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    status = meta.get("status", 200)
    if status == 429:
        raise _limited("Giphy")
    if status not in (200, None):
        raise ProviderError(_text(meta.get("msg")) or "Giphy no ha podido completar la búsqueda.")

    items: list[GifItem] = []
    data = payload.get("data")
    if isinstance(data, list):
        for entry in data:
            if not isinstance(entry, dict):
                continue
            images = entry.get("images") if isinstance(entry.get("images"), dict) else {}
            preview = _first_url(images, "fixed_height", "downsized", "original")
            download = _first_url(images, "original", "downsized", "fixed_height")
            if not preview or not download:
                continue
            user = entry.get("user") if isinstance(entry.get("user"), dict) else {}
            author = _text(user.get("display_name")) or _text(user.get("username"))
            items.append(
                GifItem(
                    id=_text(entry.get("id")),
                    title=_text(entry.get("title")) or "GIF de Giphy",
                    preview_url=preview,
                    download_url=download,
                    source="giphy",
                    attribution="Powered by GIPHY",
                    original_url=_http_url(download),
                    page_url=_http_url(entry.get("source_post_url")) or _http_url(entry.get("url")),
                    author=author,
                )
            )

    pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
    count = int(pagination.get("count") or len(items))
    total = int(pagination.get("total_count") or 0)
    current = int(pagination.get("offset") or offset)
    next_offset = current + count
    has_more = count > 0 and next_offset < total
    return items, (next_offset if has_more else None), has_more


def _klipy_entries(payload: dict) -> list:
    results = payload.get("results")
    if isinstance(results, list):
        return results
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        nested = data.get("data")
        if isinstance(nested, list):
            return nested
        nested_results = data.get("results")
        if isinstance(nested_results, list):
            return nested_results
    return []


def _klipy_media_url(entry: dict, *formats: str) -> str:
    media = entry.get("media_formats")
    if isinstance(media, dict):
        for name in formats:
            item = media.get(name)
            if isinstance(item, dict):
                url = _text(item.get("url"))
                if url:
                    return url

    file_info = entry.get("file")
    if isinstance(file_info, dict):
        tiers = {
            "tinygif": ("sm", "xs", "md"),
            "mediumgif": ("md", "sm", "hd"),
            "gif": ("hd", "md", "sm"),
        }
        for name in formats:
            for tier in tiers.get(name, ("md", "sm", "hd")):
                level = file_info.get(tier)
                if not isinstance(level, dict):
                    continue
                gif = level.get("gif")
                if isinstance(gif, dict):
                    url = _text(gif.get("url"))
                    if url:
                        return url
    return ""


def _klipy_next(payload: dict) -> str | None:
    containers = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        containers.append(data)
    for container in containers:
        nxt = container.get("next")
        if isinstance(nxt, str) and nxt.strip():
            return nxt.strip()
        if isinstance(nxt, int) and nxt >= 0:
            return str(nxt)
        if container.get("has_next") is True:
            current = container.get("current_page") or container.get("page") or 1
            try:
                return str(int(current) + 1)
            except (TypeError, ValueError):
                return None
    return None


def parse_klipy(payload: dict) -> tuple[list[GifItem], str | None, bool]:
    items: list[GifItem] = []
    for entry in _klipy_entries(payload):
        if not isinstance(entry, dict):
            continue
        preview = _klipy_media_url(entry, "tinygif", "mediumgif", "gif")
        download = _klipy_media_url(entry, "gif", "mediumgif", "tinygif")
        if not preview or not download:
            continue
        title = _text(entry.get("title")) or _text(entry.get("content_description")) or "GIF de KLIPY"
        user = entry.get("user") if isinstance(entry.get("user"), dict) else {}
        author = _text(user.get("display_name")) or _text(user.get("username")) or _text(entry.get("username"))
        page_url = ""
        for key in ("url", "item_url", "source_url", "page_url"):
            page_url = _http_url(entry.get(key))
            if page_url:
                break
        items.append(
            GifItem(
                id=_text(entry.get("id")) or _text(entry.get("slug")),
                title=title,
                preview_url=preview,
                download_url=download,
                source="klipy",
                original_url=_http_url(download),
                page_url=page_url,
                author=author,
            )
        )
    next_pos = _klipy_next(payload)
    return items, next_pos, next_pos is not None


def _raise_for_status(response: httpx.Response, source_name: str) -> None:
    if response.status_code == 429:
        raise _limited(source_name)
    if response.status_code in (401, 403):
        raise ProviderError(f"La clave de {source_name} no es válida.")
    if response.status_code >= 400:
        raise ProviderError(f"{source_name} ha respondido con un error ({response.status_code}).")


async def search_giphy(
    client: httpx.AsyncClient,
    api_key: str,
    query: str,
    offset: int,
) -> tuple[list[GifItem], int | None, bool]:
    response = await client.get(
        GIPHY_SEARCH_URL,
        params={
            "api_key": api_key,
            "q": query,
            "limit": PAGE_SIZE,
            "offset": offset,
            "rating": "g",
            "lang": "es",
        },
    )
    _raise_for_status(response, "Giphy")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ProviderError("Giphy ha devuelto una respuesta inesperada.")
    return parse_giphy(payload, offset)


async def search_klipy(
    client: httpx.AsyncClient,
    api_key: str,
    customer_id: str,
    query: str,
    pos: str | None,
) -> tuple[list[GifItem], str | None, bool]:
    params = {
        "q": query,
        "per_page": PAGE_SIZE,
        "locale": "es",
        "contentfilter": "medium",
        "customer_id": customer_id,
    }
    if pos:
        params["pos"] = pos
    response = await client.get(
        KLIPY_SEARCH_URL.format(api_key=quote(api_key, safe="")),
        params=params,
    )
    _raise_for_status(response, "KLIPY")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ProviderError("KLIPY ha devuelto una respuesta inesperada.")
    return parse_klipy(payload)


def parse_gifcities(payload: object, offset: int) -> tuple[list[GifItem], int | None, bool]:
    if not isinstance(payload, list):
        raise ProviderError("GifCities ha devuelto una respuesta inesperada.")
    page = payload[offset : offset + PAGE_SIZE]
    items: list[GifItem] = []
    for entry in page:
        if not isinstance(entry, dict):
            continue
        path = _text(entry.get("gif")).lstrip("/")
        stamp, separator, original = path.partition("/")
        if not separator or not original:
            continue
        # id_ pide el archivo original. Sin ese sufijo, Archive.org a veces
        # devuelve la página HTML del visor y el GIF no se puede mostrar.
        url = f"{WAYBACK_GIF_URL}{stamp}id_/{quote(original, safe='')}"
        title = _text(entry.get("url_text")) or "GIF de GifCities"
        historical = unquote(original)
        items.append(
            GifItem(
                id=_text(entry.get("checksum")) or path,
                title=title,
                preview_url=url,
                download_url=url,
                source="gifcities",
                original_url=_http_url(historical),
                page_url=_http_url(entry.get("page")),
            )
        )
    has_more = len(payload) >= offset + PAGE_SIZE and len(page) == PAGE_SIZE
    return items, (offset + PAGE_SIZE if has_more else None), has_more


def parse_openverse(payload: dict, page: int) -> tuple[list[GifItem], int | None, bool]:
    results = payload.get("results")
    if not isinstance(results, list):
        raise ProviderError("Openverse ha devuelto una respuesta inesperada.")
    items: list[GifItem] = []
    for entry in results:
        if not isinstance(entry, dict):
            continue
        url = _http_url(entry.get("url"))
        filetype = _text(entry.get("filetype")).lower()
        if not url or (filetype and filetype != "gif"):
            continue
        license_name, license_url = _openverse_license(entry)
        items.append(
            GifItem(
                id=_text(entry.get("id")) or url,
                title=_text(entry.get("title")) or "GIF de Openverse",
                preview_url=url,
                download_url=url,
                source="openverse",
                attribution=_text(entry.get("attribution")),
                original_url=url,
                page_url=_http_url(entry.get("foreign_landing_url")),
                author=_text(entry.get("creator")),
                license_name=license_name,
                license_url=license_url,
            )
        )
    current = int(payload.get("page") or page)
    page_count = int(payload.get("page_count") or 0)
    has_more = bool(results) and current < page_count
    return items, (current + 1 if has_more else None), has_more


def _json_payload(response: httpx.Response, source_name: str) -> object:
    _raise_for_status(response, source_name)
    content_type = response.headers.get("content-type", "")
    if "json" not in content_type:
        snippet = response.text[:300].lower()
        if "rate limit" in snippet:
            raise _limited(source_name)
        raise ProviderError(f"{source_name} ha devuelto una respuesta inesperada.")
    return response.json()


async def search_gifcities(
    client: httpx.AsyncClient,
    query: str,
    offset: int,
) -> tuple[list[GifItem], int | None, bool]:
    response = await client.get(
        GIFCITIES_SEARCH_URL,
        params={"q": query, "limit": offset + PAGE_SIZE},
    )
    payload = _json_payload(response, "GifCities")
    return parse_gifcities(payload, offset)


async def search_openverse(
    client: httpx.AsyncClient,
    query: str,
    page: int,
) -> tuple[list[GifItem], int | None, bool]:
    response = await client.get(
        OPENVERSE_SEARCH_URL,
        params={
            "q": query,
            "extension": "gif",
            "page_size": PAGE_SIZE,
            "page": page,
            "mature": "false",
        },
    )
    payload = _json_payload(response, "Openverse")
    if not isinstance(payload, dict):
        raise ProviderError("Openverse ha devuelto una respuesta inesperada.")
    return parse_openverse(payload, page)


def _wikimedia_meta(info: dict, key: str) -> str:
    metadata = info.get("extmetadata")
    if not isinstance(metadata, dict):
        return ""
    entry = metadata.get(key)
    if isinstance(entry, dict):
        return _plain(entry.get("value"))
    return _plain(entry)


def parse_wikimedia(payload: dict, offset: int) -> tuple[list[GifItem], int | None, bool]:
    error = payload.get("error")
    if isinstance(error, dict):
        code = _text(error.get("code"))
        if code in {"maxlag", "ratelimited"}:
            raise _limited("Wikimedia Commons")
        raise ProviderError(_text(error.get("info")) or "Wikimedia Commons no ha podido completar la búsqueda.")

    query = payload.get("query") if isinstance(payload.get("query"), dict) else {}
    pages = query.get("pages") if isinstance(query.get("pages"), dict) else {}
    ordered = sorted(
        (page for page in pages.values() if isinstance(page, dict)),
        key=lambda page: int(page.get("index") or 0),
    )
    items: list[GifItem] = []
    for page in ordered:
        infos = page.get("imageinfo")
        info = infos[0] if isinstance(infos, list) and infos and isinstance(infos[0], dict) else {}
        if _text(info.get("mime")).lower() != "image/gif":
            continue
        url = _http_url(info.get("url"))
        if not url:
            continue
        object_name = _wikimedia_meta(info, "ObjectName")
        title = object_name or _text(page.get("title")).removeprefix("File:").replace("_", " ")
        license_name = _wikimedia_meta(info, "LicenseShortName") or _wikimedia_meta(info, "UsageTerms")
        items.append(
            GifItem(
                id=_text(page.get("pageid")) or title or url,
                title=title or "GIF de Wikimedia Commons",
                preview_url=url,
                download_url=url,
                source="wikimedia",
                original_url=url,
                page_url=_http_url(info.get("descriptionurl")),
                author=_wikimedia_meta(info, "Artist"),
                license_name=license_name,
                license_url=_http_url(_wikimedia_meta(info, "LicenseUrl")),
            )
        )

    cont = payload.get("continue") if isinstance(payload.get("continue"), dict) else {}
    try:
        next_offset = int(cont["gsroffset"])
    except (KeyError, TypeError, ValueError):
        next_offset = None
    has_more = next_offset is not None and next_offset > offset
    return items, (next_offset if has_more else None), has_more


async def search_wikimedia(
    client: httpx.AsyncClient,
    query: str,
    offset: int,
) -> tuple[list[GifItem], int | None, bool]:
    response = await client.get(
        WIKIMEDIA_API_URL,
        params={
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"filemime:image/gif {query}",
            "gsrnamespace": 6,
            "gsrlimit": PAGE_SIZE,
            "gsroffset": offset,
            "prop": "imageinfo",
            "iiprop": "url|mime|extmetadata",
            "iiextmetadatalanguage": "es",
            "maxlag": 5,
        },
    )
    payload = _json_payload(response, "Wikimedia Commons")
    if not isinstance(payload, dict):
        raise ProviderError("Wikimedia Commons ha devuelto una respuesta inesperada.")
    return parse_wikimedia(payload, offset)


def _interleave(groups: list[list[GifItem]]) -> list[GifItem]:
    merged: list[GifItem] = []
    length = max((len(group) for group in groups), default=0)
    for index in range(length):
        for group in groups:
            if index < len(group):
                merged.append(group[index])
    return merged


async def search(
    client: httpx.AsyncClient,
    sources: list[str],
    query: str,
    keys: ApiKeys,
    *,
    cursors: dict[str, int | str | None] | None = None,
) -> SearchPage:
    known = ("giphy", "klipy", *FREE_SOURCES)
    selected = [source for source in sources if source in known]
    if not selected:
        raise ProviderError("Elige al menos un portal.")

    positions = cursors or {}
    missing: list[str] = []
    runnable: list[str] = []
    for source in selected:
        if source in KEYED_SOURCES and not getattr(keys, source):
            missing.append(KEYED_SOURCES[source])
        else:
            runnable.append(source)
    if not runnable:
        names = " y ".join(missing)
        raise MissingKeyError(
            f"Falta la clave API gratuita de {names}. Ábrela en Editar, Claves API, antes de buscar."
        )

    async def run_source(source: str) -> tuple[str, list[GifItem], int | str | None, ProviderError | None]:
        try:
            if source == "giphy":
                items, nxt, more = await search_giphy(
                    client, keys.giphy, query, int(positions.get("giphy") or 0)
                )
            elif source == "klipy":
                pos = positions.get("klipy")
                items, nxt, more = await search_klipy(
                    client,
                    keys.klipy,
                    keys.customer_id,
                    query,
                    str(pos) if pos else None,
                )
            elif source == "gifcities":
                items, nxt, more = await search_gifcities(
                    client, query, int(positions.get("gifcities") or 0)
                )
            elif source == "openverse":
                items, nxt, more = await search_openverse(
                    client, query, int(positions.get("openverse") or 1)
                )
            elif source == "wikimedia":
                items, nxt, more = await search_wikimedia(
                    client, query, int(positions.get("wikimedia") or 0)
                )
            else:
                return source, [], None, ProviderError("Portal no disponible.")
            return source, items, (nxt if more else None), None
        except ProviderError as exc:
            return source, [], None, exc
        except (httpx.HTTPError, OSError):
            label = SOURCE_LABELS.get(source, source)
            return source, [], None, ProviderError(f"{label} no responde.")

    results = await asyncio.gather(*(run_source(source) for source in runnable))
    groups: list[list[GifItem]] = []
    more_map: dict[str, int | str | None] = {}
    errors: list[ProviderError] = []
    for source, items, nxt, error in results:
        groups.append(items)
        if error is not None:
            errors.append(error)
        elif nxt is not None:
            more_map[source] = nxt

    found = _interleave(groups)
    if not found and errors:
        if all(isinstance(error, RateLimitError) for error in errors):
            raise RateLimitError(" ".join(dict.fromkeys(str(error) for error in errors)))
        raise ProviderError(" ".join(dict.fromkeys(str(error) for error in errors)))

    notices: list[str] = []
    if missing and found:
        notices.append(f"Sin clave de {' y '.join(missing)}.")
    if errors and found:
        notices.extend(dict.fromkeys(str(error) for error in errors))
    return SearchPage(found, more_map, " ".join(notices))


async def fetch_bytes(client: httpx.AsyncClient, url: str) -> bytes:
    delay = 0.5
    last_error = "No se ha podido descargar el GIF."
    for _ in range(3):
        response = await client.get(url)
        if response.status_code in (429, 503):
            last_error = "El portal ha limitado las descargas. Espera un poco e inténtalo de nuevo."
            await asyncio.sleep(delay)
            delay *= 2
            continue
        if response.status_code >= 400:
            raise ProviderError("No se ha podido descargar el GIF.")
        data = response.content
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return data
        last_error = "El archivo recibido no es un GIF."
        break
    raise ProviderError(last_error)

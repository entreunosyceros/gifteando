import asyncio
import re
from io import BytesIO
from pathlib import Path

import flet as ft
import httpx

from gifteando.config import load_keys, save_keys
from gifteando.library import (
    attribution_text,
    is_favorite,
    is_freely_reusable,
    load_favorites,
    load_free_only,
    load_recents,
    remember_recent,
    save_free_only,
    toggle_favorite,
)
from gifteando.models import GifItem
from gifteando.providers import (
    MissingKeyError,
    ProviderError,
    RateLimitError,
    fetch_bytes,
    search,
)
from gifteando.thumbs import CACHE_DIR, load_thumb, shrink_gif, store_thumb
from gifteando.tray import SystemTray

LOGO_PATH = Path(__file__).resolve().parent / "img" / "logo.png"
_logo_for_dialog: bytes | None = None


def dialog_logo() -> bytes:
    """Small PNG for the About dialog. The file on disk is 1254 px and too large to embed."""
    global _logo_for_dialog
    if _logo_for_dialog is not None:
        return _logo_for_dialog
    from PIL import Image

    with Image.open(LOGO_PATH) as image:
        image = image.convert("RGBA")
        image.thumbnail((320, 320), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
    _logo_for_dialog = buffer.getvalue()
    return _logo_for_dialog


GIPHY_KEYS_URL = "https://developers.giphy.com/dashboard/"
KLIPY_KEYS_URL = "https://partner.klipy.com/"
PROJECT_URL = "https://github.com/entreunosyceros/gifteando"
SOURCE_GROUPS = {
    "free": ["openverse", "gifcities", "wikimedia"],
    "all": ["openverse", "gifcities", "wikimedia", "giphy", "klipy"],
    "openverse": ["openverse"],
    "gifcities": ["gifcities"],
    "wikimedia": ["wikimedia"],
    "giphy": ["giphy"],
    "klipy": ["klipy"],
}
ATTRIBUTION = {
    "openverse": "Openverse",
    "gifcities": "GifCities · Internet Archive",
    "wikimedia": "Wikimedia Commons",
    "giphy": "Powered by GIPHY",
    "klipy": "KLIPY",
}
START_CURSORS: dict[str, int | str | None] = {
    "openverse": 1,
    "gifcities": 0,
    "wikimedia": 0,
    "giphy": 0,
    "klipy": None,
}
LIBRARY_VIEWS = {"favorites", "recents"}
CLIPBOARD_DIR = CACHE_DIR / "clipboard"


def _filename(item: GifItem) -> str:
    cleaned = re.sub(r"[^\w\- ]+", "", item.title, flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)[:60].strip()
    stem = cleaned or item.id or "gif"
    return f"{stem}.gif"


def _placeholder(source: str) -> str:
    if source == "klipy":
        return "Search KLIPY"
    if source == "favorites":
        return "Tus GIFs favoritos"
    if source == "recents":
        return "GIFs abiertos hace poco"
    return "Busca un GIF animado"


def _attribution(source: str) -> str:
    if source == "favorites":
        return "Favoritos guardados en este equipo"
    if source == "recents":
        return "Recientes de este equipo"
    names = SOURCE_GROUPS.get(source, [])
    return " · ".join(ATTRIBUTION[name] for name in names if name in ATTRIBUTION)


def _link_for(item: GifItem) -> str:
    return item.page_url or item.original_url or item.download_url


def _provenance(item: GifItem) -> list[ft.Control]:
    notes: list[ft.Control] = [
        ft.Text(ATTRIBUTION.get(item.source, item.source), size=12, color=ft.Colors.ON_SURFACE_VARIANT),
    ]
    if item.author:
        notes.append(ft.Text(f"Autor: {item.author}", size=12))
    if item.license_name:
        if item.license_url:
            notes.append(ft.TextButton(item.license_name, url=item.license_url))
        else:
            notes.append(ft.Text(f"Licencia: {item.license_name}", size=12))
    else:
        notes.append(
            ft.Text(
                "Sin datos de licencia. Descargarlo no autoriza a reutilizarlo.",
                size=12,
            )
        )
    portal = ATTRIBUTION.get(item.source, "")
    if item.attribution and item.attribution not in {item.license_name, portal}:
        notes.append(ft.Text(item.attribution, size=12))
    if item.page_url:
        notes.append(ft.TextButton("Página de procedencia", url=item.page_url))
    if item.original_url and item.original_url not in {item.download_url, item.page_url}:
        notes.append(ft.TextButton("URL original", url=item.original_url))
    if is_freely_reusable(item):
        notes.append(
            ft.Text("Licencia libre para reutilizar (revisa siempre los términos).", size=12)
        )
    return notes


async def main(page: ft.Page) -> None:
    page.title = "Gifteando"
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = ft.Theme(color_scheme_seed=ft.Colors.DEEP_PURPLE)
    page.padding = 20
    page.window.width = 1100
    page.window.height = 760
    page.window.min_width = 800
    page.window.min_height = 560

    keys = load_keys()
    file_picker = ft.FilePicker()
    clipboard = ft.Clipboard()
    gif_cache: dict[str, bytes] = {}
    thumb_cache: dict[tuple[str, int, int], bytes] = {}
    # Una sola descarga a la vez evita el 429 de upload.wikimedia.org.
    load_gate = asyncio.Semaphore(1)
    ui_lock = asyncio.Lock()
    client = httpx.AsyncClient(
        timeout=60,
        follow_redirects=True,
        headers={
            "User-Agent": (
                "Gifteando/1.0 (https://github.com/entreunosyceros/gifteando; "
                "desktop GIF search) python-httpx"
            ),
        },
    )

    query = ft.TextField(
        hint_text=_placeholder("free"),
        expand=True,
        autofocus=True,
    )
    source = ft.Dropdown(
        label="Portal",
        value="free",
        width=300,
        options=[
            ft.DropdownOption(key="free", text="Sin clave"),
            ft.DropdownOption(key="all", text="Todos"),
            ft.DropdownOption(key="openverse", text="Openverse"),
            ft.DropdownOption(key="gifcities", text="GifCities"),
            ft.DropdownOption(key="wikimedia", text="Wikimedia Commons"),
            ft.DropdownOption(key="giphy", text="Giphy"),
            ft.DropdownOption(key="klipy", text="KLIPY"),
            ft.DropdownOption(key="favorites", text="Favoritos"),
            ft.DropdownOption(key="recents", text="Recientes"),
        ],
    )
    free_only = ft.Switch(
        label="Solo licencia libre",
        value=load_free_only(),
        tooltip="Muestra CC0, dominio público, CC BY y CC BY-SA. Oculta NC/ND y lo que no informa licencia.",
    )
    search_button = ft.Button("Buscar", icon=ft.Icons.SEARCH, on_click=lambda e: page.run_task(run_search, False))
    attribution = ft.Text(_attribution("free"), size=12, color=ft.Colors.ON_SURFACE_VARIANT)
    status_icon = ft.ProgressRing(width=18, height=18, stroke_width=2, visible=False)
    status_text = ft.Text(
        "Escribe al menos dos letras: la búsqueda empieza al parar. Abre un GIF para copiarlo o marcarlo favorito.",
        expand=True,
    )
    spinner = ft.Container(
        visible=False,
        alignment=ft.Alignment.CENTER,
        content=ft.Column(
            tight=True,
            spacing=12,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.ProgressRing(width=40, height=40, stroke_width=3),
                ft.Text("Buscando…"),
            ],
        ),
    )
    grid = ft.GridView(
        expand=True,
        max_extent=220,
        child_aspect_ratio=0.82,
        spacing=12,
        run_spacing=12,
    )
    load_more = ft.Button(
        "Cargar más",
        visible=False,
        on_click=lambda e: page.run_task(run_search, True),
    )

    state = {
        "items": [],
        "query": "",
        "source": "free",
        "cursors": dict(START_CURSORS),
        "pending": set(SOURCE_GROUPS["free"]),
        "generation": 0,
        "busy": False,
        "typing": 0,
        "queued": False,
    }

    def set_status(message: str, busy: bool = False) -> None:
        status_text.value = message
        status_icon.visible = busy
        spinner.visible = busy and not state["items"]
        search_button.disabled = busy or (source.value in LIBRARY_VIEWS)
        load_more.disabled = busy
        free_only.disabled = busy
        page.update()

    def visible_items(items: list[GifItem]) -> list[GifItem]:
        if not free_only.value:
            return items
        return [item for item in items if is_freely_reusable(item)]

    def render_items() -> None:
        generation = state["generation"]
        cards = []
        loaders = []
        for item in state["items"]:
            card, loader = _card(item, generation)
            cards.append(card)
            loaders.append(loader)
        grid.controls = cards
        load_more.visible = (
            bool(state["items"])
            and state.get("has_more", False)
            and state["source"] not in LIBRARY_VIEWS
        )
        grid.update()
        load_more.update()
        page.run_task(load_previews, loaders, generation)

    def show_library(kind: str) -> None:
        state["generation"] += 1
        state["source"] = kind
        state["query"] = ""
        state["has_more"] = False
        state["pending"] = set()
        items = load_favorites() if kind == "favorites" else load_recents()
        filtered = visible_items(items)
        state["items"] = filtered
        render_items()
        label = "favoritos" if kind == "favorites" else "recientes"
        if not items:
            set_status(f"No hay GIFs en {label} todavía.")
        elif not filtered:
            set_status(f"Hay {len(items)} en {label}, pero ninguno pasa el filtro de licencia libre.")
        else:
            set_status(f"{len(filtered)} GIFs en {label}.")

    async def load_previews(loaders: list, generation: int) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        for loader in loaders:
            queue.put_nowait(loader)

        async def worker() -> None:
            while generation == state["generation"]:
                try:
                    loader = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                await loader()

        await worker()

    async def thumbnail(url: str, max_side: int = 160, max_frames: int = 8) -> bytes:
        key = (url, max_side, max_frames)
        cached = thumb_cache.get(key)
        if cached is None:
            cached = await asyncio.to_thread(load_thumb, url, max_side, max_frames)
        if cached is None:
            raw = await cached_gif(url)
            cached = await asyncio.to_thread(shrink_gif, raw, max_side, max_frames)
            await asyncio.to_thread(store_thumb, url, cached, max_side, max_frames)
        thumb_cache[key] = cached
        return cached

    async def cached_gif(url: str) -> bytes:
        cached = gif_cache.get(url)
        if cached is not None:
            return cached
        async with load_gate:
            cached = gif_cache.get(url)
            if cached is not None:
                return cached
            data = await fetch_bytes(client, url)
            gif_cache[url] = data
            return data

    def _card(item: GifItem, generation: int) -> tuple[ft.Control, object]:
        preview = ft.Container(
            height=160,
            alignment=ft.Alignment.CENTER,
            content=ft.ProgressRing(width=18, height=18, stroke_width=2),
        )

        async def load_preview() -> None:
            try:
                thumb = await thumbnail(item.preview_url)
            except (ProviderError, httpx.HTTPError, OSError):
                thumb = None
            if generation != state["generation"]:
                return
            async with ui_lock:
                if generation != state["generation"]:
                    return
                if thumb:
                    preview.content = ft.Image(
                        src=thumb,
                        height=150,
                        fit=ft.BoxFit.CONTAIN,
                        cache_width=320,
                        cache_height=240,
                    )
                else:
                    preview.content = ft.Text("No se pudo cargar", size=12)
                try:
                    preview.update()
                except RuntimeError:
                    return
                await asyncio.sleep(0.02)

        card = ft.Container(
            border_radius=12,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            on_click=lambda e, current=item: page.run_task(open_detail, current),
            content=ft.Column(
                spacing=0,
                controls=[
                    preview,
                    ft.Container(
                        padding=10,
                        content=ft.Text(
                            item.title,
                            size=12,
                            max_lines=2,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            tooltip=" · ".join(
                                part
                                for part in (
                                    item.title,
                                    ATTRIBUTION.get(item.source, item.source),
                                    item.author,
                                    item.license_name,
                                    item.attribution,
                                )
                                if part
                            ),
                        ),
                    ),
                ],
            ),
        )
        return card, load_preview

    async def open_detail(item: GifItem) -> None:
        detail_status = ft.Text("", size=12)
        favorite = is_favorite(item)
        favorite_button = ft.IconButton(
            icon=ft.Icons.STAR if favorite else ft.Icons.STAR_BORDER,
            tooltip="Quitar de favoritos" if favorite else "Añadir a favoritos",
            icon_color=ft.Colors.AMBER if favorite else None,
        )
        remember_recent(item)

        async def download(_e: ft.Event) -> None:
            detail_status.value = "Descargando…"
            detail_status.update()
            try:
                data = gif_cache.get(item.download_url) or await cached_gif(item.download_url)
                path = await file_picker.save_file(
                    dialog_title="Guardar GIF",
                    file_name=_filename(item),
                    file_type=ft.FilePickerFileType.CUSTOM,
                    allowed_extensions=["gif"],
                    src_bytes=data,
                )
            except ProviderError as exc:
                detail_status.value = str(exc)
                detail_status.update()
                return
            except httpx.HTTPError:
                detail_status.value = "No hay conexión para descargar el GIF."
                detail_status.update()
                return
            if not path:
                detail_status.value = ""
                detail_status.update()
                return
            saved = Path(path)
            if not saved.is_file():
                saved.write_bytes(data)
            detail_status.value = f"Guardado en {path}"
            detail_status.update()

        async def copy_link(_e: ft.Event) -> None:
            link = _link_for(item)
            if not link:
                detail_status.value = "Este GIF no tiene enlace para copiar."
                detail_status.update()
                return
            await clipboard.set(link)
            detail_status.value = "Enlace copiado. Pégalo en el chat o el documento."
            detail_status.update()

        async def copy_gif(_e: ft.Event) -> None:
            detail_status.value = "Preparando GIF…"
            detail_status.update()
            try:
                data = gif_cache.get(item.download_url) or await cached_gif(item.download_url)
                CLIPBOARD_DIR.mkdir(parents=True, exist_ok=True)
                path = CLIPBOARD_DIR / _filename(item)
                path.write_bytes(data)
                ok = await clipboard.set_files([str(path)])
            except ProviderError as exc:
                detail_status.value = str(exc)
                detail_status.update()
                return
            except httpx.HTTPError:
                detail_status.value = "No hay conexión para copiar el GIF."
                detail_status.update()
                return
            except Exception as exc:
                detail_status.value = f"No se pudo copiar el archivo: {exc}"
                detail_status.update()
                return
            detail_status.value = (
                "GIF copiado como archivo. Pégalo en el chat o el documento."
                if ok
                else "El sistema no aceptó el archivo en el portapapeles."
            )
            detail_status.update()

        async def copy_credit(_e: ft.Event) -> None:
            await clipboard.set(attribution_text(item))
            detail_status.value = "Atribución copiada."
            detail_status.update()

        def on_favorite(_e: ft.Event) -> None:
            now = toggle_favorite(item)
            favorite_button.icon = ft.Icons.STAR if now else ft.Icons.STAR_BORDER
            favorite_button.tooltip = "Quitar de favoritos" if now else "Añadir a favoritos"
            favorite_button.icon_color = ft.Colors.AMBER if now else None
            favorite_button.update()
            detail_status.value = "Añadido a favoritos." if now else "Quitado de favoritos."
            detail_status.update()
            if state["source"] == "favorites":
                show_library("favorites")

        favorite_button.on_click = on_favorite

        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=item.title,
                scrollable=True,
                content=ft.Column(
                    tight=True,
                    width=520,
                    spacing=12,
                    controls=[
                        picture := ft.Container(
                            height=360,
                            alignment=ft.Alignment.CENTER,
                            content=ft.ProgressRing(width=28, height=28, stroke_width=2),
                        ),
                        *_provenance(item),
                        detail_status,
                    ],
                ),
                actions=[
                    favorite_button,
                    ft.Button("Copiar enlace", icon=ft.Icons.LINK, on_click=copy_link),
                    ft.Button("Copiar GIF", icon=ft.Icons.CONTENT_COPY, on_click=copy_gif),
                    ft.TextButton("Copiar atribución", on_click=copy_credit),
                    ft.Button("Descargar", icon=ft.Icons.DOWNLOAD, on_click=download),
                    ft.TextButton("Cerrar", on_click=lambda e: page.pop_dialog()),
                ],
            )
        )
        ready = thumb_cache.get((item.preview_url, 160, 8))
        if ready is not None:
            picture.content = ft.Image(src=ready, fit=ft.BoxFit.CONTAIN, cache_width=840, cache_height=720)
            try:
                picture.update()
            except RuntimeError:
                return
        try:
            data = await thumbnail(item.download_url, 420, 12)
        except (ProviderError, httpx.HTTPError, OSError):
            if ready is None:
                picture.content = ft.Text("No se pudo mostrar el GIF")
                try:
                    picture.update()
                except RuntimeError:
                    return
            return
        picture.content = ft.Image(src=data, fit=ft.BoxFit.CONTAIN, cache_width=840, cache_height=720)
        try:
            picture.update()
        except RuntimeError:
            return

    async def run_search(append: bool) -> None:
        if state["busy"]:
            if not append:
                state["queued"] = True
            return

        state["busy"] = True
        try:
            await _run_search(append)
        finally:
            state["busy"] = False
            if state["queued"]:
                state["queued"] = False
                page.run_task(run_search, False)

    async def _run_search(append: bool) -> None:
        current = source.value or "free"
        if current in LIBRARY_VIEWS:
            show_library(current)
            return

        term = (state["query"] if append else (query.value or "")).strip()
        if not term:
            set_status("Escribe un término para buscar.")
            return

        state["generation"] += 1
        generation = state["generation"]
        if not append:
            state["items"] = []
            state["query"] = term
            state["source"] = current
            state["cursors"] = dict(START_CURSORS)
            state["pending"] = set(SOURCE_GROUPS.get(state["source"], []))
            state["has_more"] = False
            render_items()
        sources = [name for name in SOURCE_GROUPS.get(state["source"], []) if name in state["pending"]]
        if not sources:
            set_status("No quedan portales con más resultados.")
            return
        set_status("Cargando más…" if append else "Buscando…", busy=True)

        try:
            result = await search(
                client,
                sources,
                term,
                keys,
                cursors=state["cursors"],
            )
        except MissingKeyError as exc:
            if generation == state["generation"]:
                set_status(str(exc))
            return
        except RateLimitError as exc:
            if generation == state["generation"]:
                set_status(str(exc))
            return
        except ProviderError as exc:
            if generation == state["generation"]:
                set_status(str(exc))
            return
        except httpx.HTTPError:
            if generation == state["generation"]:
                set_status("No hay conexión. Revisa la red.")
            return

        if generation != state["generation"]:
            return

        batch = visible_items(result.items)
        if append:
            seen = {(item.source, item.id) for item in state["items"]}
            state["items"].extend(item for item in batch if (item.source, item.id) not in seen)
        else:
            state["items"] = list(batch)
        for name in sources:
            if name in result.more:
                state["cursors"][name] = result.more[name]
            else:
                state["pending"].discard(name)
        state["has_more"] = bool(state["pending"])
        render_items()
        hidden = len(result.items) - len(batch)
        if not state["items"]:
            if free_only.value and result.items:
                set_status(
                    f"Ningún resultado de «{term}» pasa el filtro de licencia libre. "
                    "Prueba Openverse/Wikimedia o desactiva el filtro."
                )
            else:
                set_status(f"No hay GIFs para «{term}».")
        else:
            extra = []
            if free_only.value and hidden:
                extra.append(f"ocultos {hidden} sin licencia libre")
            if result.notice:
                extra.append(result.notice)
            suffix = f" {' · '.join(extra)}" if extra else ""
            set_status(f"{len(state['items'])} GIFs.{suffix}")

    async def wait_and_search(token: int) -> None:
        await asyncio.sleep(0.45)
        if token != state["typing"]:
            return
        if (source.value or "free") in LIBRARY_VIEWS:
            return
        term = (query.value or "").strip()
        if len(term) < 2:
            return
        if term == state["query"] and (source.value or "free") == state["source"] and state["items"]:
            return
        await run_search(False)

    def on_query_change(_e: ft.Event) -> None:
        state["typing"] += 1
        page.run_task(wait_and_search, state["typing"])

    def submit_query(_e: ft.Event) -> None:
        state["typing"] += 1
        page.run_task(run_search, False)

    query.on_change = on_query_change
    query.on_submit = submit_query

    def on_source_change(_e: ft.Event) -> None:
        current = source.value or "free"
        query.hint_text = _placeholder(current)
        attribution.value = _attribution(current)
        query.disabled = current in LIBRARY_VIEWS
        search_button.disabled = current in LIBRARY_VIEWS
        page.update()
        if current in LIBRARY_VIEWS:
            show_library(current)

    def on_free_only_change(_e: ft.Event) -> None:
        save_free_only(bool(free_only.value))
        current = source.value or "free"
        if current in LIBRARY_VIEWS:
            show_library(current)
        elif state["items"] and state["query"]:
            page.run_task(run_search, False)

    source.on_select = on_source_change
    free_only.on_change = on_free_only_change

    def open_keys(_e: ft.Event | None = None) -> None:
        giphy_field = ft.TextField(
            label="Clave de Giphy",
            value=keys.giphy,
            password=True,
            can_reveal_password=True,
            hint_text="Clave beta gratuita",
        )
        klipy_field = ft.TextField(
            label="Clave de KLIPY",
            value=keys.klipy,
            password=True,
            can_reveal_password=True,
            hint_text="Clave de prueba gratuita",
        )

        def store(_e: ft.Event) -> None:
            nonlocal keys
            keys = save_keys(giphy_field.value or "", klipy_field.value or "", keys.customer_id)
            page.pop_dialog()
            set_status("Claves guardadas en este equipo.")

        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title="Claves API",
                content=ft.Column(
                    tight=True,
                    width=460,
                    spacing=12,
                    controls=[
                        ft.Text(
                            "Giphy y KLIPY piden una clave gratuita. Se guarda solo en este equipo, fuera del proyecto."
                        ),
                        giphy_field,
                        ft.TextButton("Obtener clave de Giphy", url=GIPHY_KEYS_URL),
                        klipy_field,
                        ft.TextButton("Obtener clave de KLIPY", url=KLIPY_KEYS_URL),
                    ],
                ),
                actions=[
                    ft.TextButton("Cancelar", on_click=lambda e: page.pop_dialog()),
                    ft.Button("Guardar", icon=ft.Icons.KEY, on_click=store),
                ],
            )
        )

    def open_about(_e: ft.Event | None = None) -> None:
        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title="Acerca de Gifteando",
                scrollable=True,
                content=ft.Column(
                    tight=True,
                    width=420,
                    spacing=16,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Container(
                            height=240,
                            alignment=ft.Alignment.CENTER,
                            content=ft.Image(
                                src=dialog_logo(),
                                fit=ft.BoxFit.CONTAIN,
                                cache_width=320,
                                cache_height=320,
                            ),
                        ),
                        ft.Text(
                            "Busca GIFs animados gratuitos y guárdalos en tu equipo.\n\n"
                            "Sin clave: Openverse (licencia y atribución), GifCities (animaciones retro) "
                            "y Wikimedia Commons.\n"
                            "Con clave gratuita: Giphy y KLIPY, para memes y GIF populares. "
                            "Se configuran en Editar, Claves API.\n\n"
                            "Al abrir un GIF puedes copiar el enlace, el archivo o la atribución, "
                            "marcarlo favorito y ver la licencia cuando el portal la informa.\n"
                            "Descargarlo no implica que se pueda reutilizar.\n\n"
                            "Al cerrar la ventana, Gifteando sigue en la bandeja del sistema. "
                            "Usa Salir en el menú de la bandeja o en Archivo para cerrarlo del todo.\n\n"
                            "Powered by GIPHY cuando se usa ese portal."
                        ),
                        ft.Button("GitHub", icon=ft.Icons.OPEN_IN_NEW, url=PROJECT_URL),
                    ],
                ),
                actions=[ft.TextButton("Cerrar", on_click=lambda e: page.pop_dialog())],
            )
        )

    loop = asyncio.get_running_loop()
    tray_holder: dict[str, SystemTray | None] = {"icon": None}
    quitting = {"value": False}

    def run_on_ui(action) -> None:
        if asyncio.iscoroutinefunction(action):
            loop.call_soon_threadsafe(lambda: page.run_task(action))
        else:
            loop.call_soon_threadsafe(action)

    async def show_window(_e: ft.Event | None = None) -> None:
        page.window.visible = True
        page.window.minimized = False
        page.window.skip_task_bar = False
        page.window.focused = True
        page.update()
        await page.window.to_front()

    async def hide_to_tray(_e: ft.Event | None = None) -> None:
        page.window.visible = False
        page.window.skip_task_bar = True
        page.update()
        set_status("Gifteando sigue en la bandeja del sistema.")

    async def menu_search(_e: ft.Event | None = None) -> None:
        await show_window()
        if source.value in LIBRARY_VIEWS:
            source.value = "free"
            query.disabled = False
            search_button.disabled = False
            query.hint_text = _placeholder("free")
            attribution.value = _attribution("free")
            page.update()
        state["typing"] += 1
        await query.focus()
        await run_search(False)

    def menu_favorites(_e: ft.Event | None = None) -> None:
        page.run_task(show_window)
        source.value = "favorites"
        on_source_change(None)

    def menu_recents(_e: ft.Event | None = None) -> None:
        page.run_task(show_window)
        source.value = "recents"
        on_source_change(None)

    def menu_keys(_e: ft.Event | None = None) -> None:
        page.run_task(show_window)
        open_keys()

    async def quit_app(_e: ft.Event | None = None) -> None:
        if quitting["value"]:
            return
        quitting["value"] = True
        tray = tray_holder["icon"]
        tray_holder["icon"] = None
        if tray is not None:
            await asyncio.to_thread(tray.stop)
        page.window.prevent_close = False
        page.update()
        await page.window.destroy()

    async def on_window_event(e: ft.WindowEvent) -> None:
        if e.type == ft.WindowEventType.CLOSE and not quitting["value"]:
            await hide_to_tray()

    page.window.prevent_close = True
    page.window.on_event = on_window_event

    page.add(
        ft.MenuBar(
            controls=[
                ft.SubmenuButton(
                    content="Archivo",
                    controls=[
                        ft.MenuItemButton(content="Buscar", on_click=menu_search),
                        ft.MenuItemButton(content="Favoritos", on_click=menu_favorites),
                        ft.MenuItemButton(content="Recientes", on_click=menu_recents),
                        ft.MenuItemButton(content="Ocultar en la bandeja", on_click=hide_to_tray),
                        ft.MenuItemButton(content="Salir", on_click=quit_app),
                    ],
                ),
                ft.SubmenuButton(
                    content="Editar",
                    controls=[
                        ft.MenuItemButton(content="Claves API…", on_click=open_keys),
                    ],
                ),
                ft.SubmenuButton(
                    content="Ayuda",
                    controls=[
                        ft.MenuItemButton(content="Acerca de Gifteando", on_click=open_about),
                    ],
                ),
            ]
        ),
        ft.Text("Gifteando", size=28, weight=ft.FontWeight.W_600),
        ft.Row(
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[query, source, free_only, search_button],
        ),
        attribution,
        ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[status_icon, status_text],
        ),
        ft.Column(expand=True, controls=[spinner, grid]),
        ft.Row(alignment=ft.MainAxisAlignment.CENTER, controls=[load_more]),
    )
    await page.window.center()

    tray_holder["icon"] = SystemTray(
        logo_path=LOGO_PATH,
        on_show=lambda: run_on_ui(show_window),
        on_search=lambda: run_on_ui(menu_search),
        on_favorites=lambda: run_on_ui(menu_favorites),
        on_recents=lambda: run_on_ui(menu_recents),
        on_keys=lambda: run_on_ui(menu_keys),
        on_hide=lambda: run_on_ui(hide_to_tray),
        on_quit=lambda: run_on_ui(quit_app),
    )
    tray_holder["icon"].start()

    async def close_client() -> None:
        tray = tray_holder["icon"]
        tray_holder["icon"] = None
        if tray is not None:
            await asyncio.to_thread(tray.stop)
        await client.aclose()

    page.on_close = lambda e: page.run_task(close_client)

import asyncio
import re
from io import BytesIO
from pathlib import Path

import flet as ft
import httpx

from gifteando.config import ApiKeys, load_keys, save_keys
from gifteando.models import GifItem
from gifteando.providers import (
    MissingKeyError,
    ProviderError,
    RateLimitError,
    fetch_bytes,
    search,
)
from gifteando.thumbs import load_thumb, shrink_gif, store_thumb

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


def _filename(item: GifItem) -> str:
    cleaned = re.sub(r"[^\w\- ]+", "", item.title, flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)[:60].strip()
    stem = cleaned or item.id or "gif"
    return f"{stem}.gif"


def _placeholder(source: str) -> str:
    if source == "klipy":
        return "Search KLIPY"
    return "Busca un GIF animado"


def _attribution(source: str) -> str:
    names = SOURCE_GROUPS.get(source, [])
    return " · ".join(ATTRIBUTION[name] for name in names if name in ATTRIBUTION)


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
    gif_cache: dict[str, bytes] = {}
    thumb_cache: dict[tuple[str, int, int], bytes] = {}
    load_gate = asyncio.Semaphore(2)
    ui_lock = asyncio.Lock()
    client = httpx.AsyncClient(
        timeout=60,
        follow_redirects=True,
        headers={"User-Agent": "Gifteando/1.0 (personal desktop app; GIF preview) python-httpx"},
    )

    query = ft.TextField(
        hint_text=_placeholder("free"),
        expand=True,
        autofocus=True,
    )
    source = ft.Dropdown(
        label="Portal",
        value="free",
        width=320,
        options=[
            ft.DropdownOption(key="free", text="Sin clave"),
            ft.DropdownOption(key="all", text="Todos"),
            ft.DropdownOption(key="openverse", text="Openverse"),
            ft.DropdownOption(key="gifcities", text="GifCities"),
            ft.DropdownOption(key="wikimedia", text="Wikimedia Commons"),
            ft.DropdownOption(key="giphy", text="Giphy"),
            ft.DropdownOption(key="klipy", text="KLIPY"),
        ],
    )
    search_button = ft.Button("Buscar", icon=ft.Icons.SEARCH, on_click=lambda e: page.run_task(run_search, False))
    attribution = ft.Text(_attribution("free"), size=12, color=ft.Colors.ON_SURFACE_VARIANT)
    status_icon = ft.ProgressRing(width=18, height=18, stroke_width=2, visible=False)
    status_text = ft.Text(
        "Escribe al menos dos letras: la búsqueda empieza al parar. Openverse, GifCities y Wikimedia Commons no necesitan clave.",
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
        search_button.disabled = busy
        load_more.disabled = busy
        page.update()

    def render_items() -> None:
        generation = state["generation"]
        cards = []
        loaders = []
        for item in state["items"]:
            card, loader = _card(item, generation)
            cards.append(card)
            loaders.append(loader)
        grid.controls = cards
        load_more.visible = bool(state["items"]) and state.get("has_more", False)
        grid.update()
        load_more.update()
        page.run_task(load_previews, loaders, generation)

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

        await asyncio.gather(worker(), worker())

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
        term = (state["query"] if append else (query.value or "")).strip()
        if not term:
            set_status("Escribe un término para buscar.")
            return

        state["generation"] += 1
        generation = state["generation"]
        if not append:
            state["items"] = []
            state["query"] = term
            state["source"] = source.value or "free"
            state["cursors"] = dict(START_CURSORS)
            state["pending"] = set(SOURCE_GROUPS.get(state["source"], []))
            state["has_more"] = False
            render_items()
        sources = [name for name in SOURCE_GROUPS.get(state["source"], []) if name in state["pending"]]
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

        if append:
            seen = {(item.source, item.id) for item in state["items"]}
            state["items"].extend(item for item in result.items if (item.source, item.id) not in seen)
        else:
            state["items"] = list(result.items)
        for name in sources:
            if name in result.more:
                state["cursors"][name] = result.more[name]
            else:
                state["pending"].discard(name)
        state["has_more"] = bool(state["pending"])
        render_items()
        if not state["items"]:
            set_status(f"No hay GIFs para «{term}».")
        elif result.notice:
            set_status(f"{len(state['items'])} GIFs. {result.notice}")
        else:
            set_status(f"{len(state['items'])} GIFs.")

    async def wait_and_search(token: int) -> None:
        await asyncio.sleep(0.45)
        if token != state["typing"]:
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
        page.update()

    source.on_select = on_source_change

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
                            "Al abrir un GIF se muestran el autor, la licencia y la página de origen "
                            "cuando el portal los informa. Descargarlo no implica que se pueda reutilizar.\n\n"
                            "Powered by GIPHY cuando se usa ese portal."
                        ),
                        ft.Button("GitHub", icon=ft.Icons.OPEN_IN_NEW, url=PROJECT_URL),
                    ],
                ),
                actions=[ft.TextButton("Cerrar", on_click=lambda e: page.pop_dialog())],
            )
        )

    async def menu_search(_e: ft.Event) -> None:
        state["typing"] += 1
        await query.focus()
        await run_search(False)

    async def quit_app(_e: ft.Event) -> None:
        await page.window.close()

    page.add(
        ft.MenuBar(
            controls=[
                ft.SubmenuButton(
                    content="Archivo",
                    controls=[
                        ft.MenuItemButton(content="Buscar", on_click=menu_search),
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
            controls=[query, source, search_button],
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

    async def close_client() -> None:
        await client.aclose()

    page.on_close = lambda e: page.run_task(close_client)

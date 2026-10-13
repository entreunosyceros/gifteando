"""Icono de bandeja del sistema con pystray (Flet no lo trae de serie)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from PIL import Image

Action = Callable[[], None]


def tray_image(logo_path: Path) -> Image.Image:
    with Image.open(logo_path) as image:
        frame = image.convert("RGBA")
        frame.thumbnail((64, 64), Image.Resampling.LANCZOS)
        return frame.copy()


class SystemTray:
    def __init__(
        self,
        *,
        logo_path: Path,
        on_show: Action,
        on_search: Action,
        on_favorites: Action,
        on_recents: Action,
        on_keys: Action,
        on_hide: Action,
        on_quit: Action,
    ) -> None:
        self._logo_path = logo_path
        self._on_show = on_show
        self._on_search = on_search
        self._on_favorites = on_favorites
        self._on_recents = on_recents
        self._on_keys = on_keys
        self._on_hide = on_hide
        self._on_quit = on_quit
        self._icon = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._icon is not None:
            return
        try:
            import pystray
        except ImportError:
            return

        image = tray_image(self._logo_path)

        def item(label: str, action: Action, *, default: bool = False):
            return pystray.MenuItem(
                label,
                lambda _icon, _item: action(),
                default=default,
            )

        menu = pystray.Menu(
            item("Mostrar Gifteando", self._on_show, default=True),
            item("Buscar", self._on_search),
            item("Favoritos", self._on_favorites),
            item("Recientes", self._on_recents),
            item("Claves API…", self._on_keys),
            pystray.Menu.SEPARATOR,
            item("Ocultar en la bandeja", self._on_hide),
            item("Salir", self._on_quit),
        )
        self._icon = pystray.Icon(
            name="gifteando",
            icon=image,
            title="Gifteando",
            menu=menu,
        )
        self._thread = threading.Thread(target=self._icon.run, name="gifteando-tray", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        icon = self._icon
        self._icon = None
        if icon is None:
            return
        try:
            icon.stop()
        except Exception:
            pass

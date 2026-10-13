import hashlib
import io
import os
from pathlib import Path

from PIL import Image

CACHE_DIR = Path.home() / ".cache" / "gifteando"
# v2: GIF re-codificados para que Flutter/Skia pueda leer los fotogramas.
_CACHE_VERSION = "v2"


def _thumb_path(url: str, max_side: int, max_frames: int) -> Path:
    digest = hashlib.sha256(
        f"{_CACHE_VERSION}\n{max_side}\n{max_frames}\n{url}".encode()
    ).hexdigest()
    return CACHE_DIR / digest[:2] / f"{digest}.gif"


def load_thumb(url: str, max_side: int = 160, max_frames: int = 8) -> bytes | None:
    """Lee una miniatura ya generada. None si no está en disco o está dañada."""
    path = _thumb_path(url, max_side, max_frames)
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        try:
            path.unlink()
        except OSError:
            pass
        return None
    return data


def store_thumb(url: str, data: bytes, max_side: int = 160, max_frames: int = 8) -> None:
    """Guarda la miniatura en ~/.cache/gifteando para no volver a descargarla."""
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        return
    path = _thumb_path(url, max_side, max_frames)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(CACHE_DIR, 0o700)
        os.chmod(path.parent, 0o700)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    except OSError:
        return


def _frame_duration(image: Image.Image, step: int) -> int:
    raw = image.info.get("duration", 100)
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else 100
    try:
        duration = int(raw)
    except (TypeError, ValueError):
        duration = 100
    return max(40, duration * step)


def _prepare_frame(frame: Image.Image, max_side: int) -> Image.Image:
    """RGB opaco + paleta: Flutter falla a menudo con GIF RGBA de Pillow."""
    rgba = frame.convert("RGBA")
    rgba.thumbnail((max_side, max_side), Image.Resampling.BILINEAR)
    background = Image.new("RGB", rgba.size, (24, 24, 28))
    background.paste(rgba, mask=rgba.getchannel("A"))
    return background.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)


def shrink_gif(data: bytes, max_side: int = 160, max_frames: int = 8) -> bytes:
    """Devuelve un GIF corto y pequeño para la cuadrícula.

    El original puede pesar varios megabytes. Mostrarlo entero en cada
    tarjeta bloquea el dibujo de la ventana. Además se re-codifica en un
    formato que Flutter puede animar sin errores de fotograma.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            frame_count = max(getattr(image, "n_frames", 1) or 1, 1)
            # GIF ya pequeño y de un solo fotograma: no hace falta tocar.
            if frame_count == 1 and len(data) <= 60_000:
                return data
            step = max(1, frame_count // max_frames)
            frames: list[Image.Image] = []
            durations: list[int] = []
            for index in range(0, frame_count, step):
                image.seek(index)
                frames.append(_prepare_frame(image, max_side))
                durations.append(_frame_duration(image, step))
                if len(frames) >= max_frames:
                    break
    except OSError:
        return data

    if not frames:
        return data
    buffer = io.BytesIO()
    frames[0].save(
        buffer,
        format="GIF",
        save_all=len(frames) > 1,
        append_images=frames[1:],
        duration=durations if len(frames) > 1 else durations[0],
        loop=0,
        disposal=2,
        optimize=False,
    )
    shrunk = buffer.getvalue()
    if not shrunk.startswith(b"GIF"):
        return _one_frame(frames[0])
    if len(shrunk) > 250_000 and max_side > 96:
        return shrink_gif(data, max_side=max_side // 2, max_frames=max(4, max_frames // 2))
    return shrunk


def _one_frame(frame: Image.Image) -> bytes:
    prepared = frame if frame.mode == "P" else _prepare_frame(frame, max(frame.size))
    buffer = io.BytesIO()
    prepared.save(buffer, format="GIF")
    return buffer.getvalue()

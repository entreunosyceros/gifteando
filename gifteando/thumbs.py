import hashlib
import io
import os
from pathlib import Path

from PIL import Image

CACHE_DIR = Path.home() / ".cache" / "gifteando"


def _thumb_path(url: str, max_side: int, max_frames: int) -> Path:
    digest = hashlib.sha256(f"{max_side}\n{max_frames}\n{url}".encode()).hexdigest()
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


def shrink_gif(data: bytes, max_side: int = 160, max_frames: int = 8) -> bytes:
    """Devuelve un GIF corto y pequeño para la cuadrícula.

    El original puede pesar varios megabytes. Mostrarlo entero en cada
    tarjeta bloquea el dibujo de la ventana.
    """
    if len(data) <= 60_000:
        return data
    with Image.open(io.BytesIO(data)) as image:
        frame_count = max(getattr(image, "n_frames", 1) or 1, 1)
        step = max(1, frame_count // max_frames)
        frames: list[Image.Image] = []
        durations: list[int] = []
        for index in range(0, frame_count, step):
            image.seek(index)
            frame = image.convert("RGBA")
            frame.thumbnail((max_side, max_side), Image.Resampling.BILINEAR)
            frames.append(frame)
            duration = int(image.info.get("duration") or 100)
            durations.append(max(40, duration) * step)
            if len(frames) >= max_frames:
                break
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
    )
    shrunk = buffer.getvalue()
    if not shrunk.startswith(b"GIF"):
        return data if len(data) <= 200_000 else _one_frame(frames[0])
    if len(shrunk) > 250_000 and max_side > 96:
        return shrink_gif(data, max_side=max_side // 2, max_frames=max(4, max_frames // 2))
    if len(shrunk) >= len(data) and len(data) <= 200_000:
        return data
    return shrunk


def _one_frame(frame: Image.Image) -> bytes:
    buffer = io.BytesIO()
    frame.save(buffer, format="GIF")
    return buffer.getvalue()

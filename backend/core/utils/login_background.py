"""Storage and public presentation of instance-wide login artwork."""

import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

from backend.core.settings import settings
from backend.database.models import GeneralSettings
from backend.models.login_background import LoginBackgroundResponse

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


def background_directory() -> Path:
    return settings.static_dir_path / "login-backgrounds"


def delete_background(filename: str | None) -> None:
    if filename and Path(filename).name == filename:
        (background_directory() / filename).unlink(missing_ok=True)


def save_background(contents: bytes) -> str:
    directory = background_directory()
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    path = directory / filename
    try:
        with Image.open(BytesIO(contents)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("Use a JPEG, PNG, or WebP image")
            if source.width * source.height > 25_000_000:
                raise ValueError("Image must contain no more than 25 million pixels")
            source.verify()
        with Image.open(BytesIO(contents)) as source:
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.thumbnail((3840, 2160), Image.Resampling.LANCZOS)
            flattened = Image.new("RGB", image.size, "black")
            flattened.paste(image, mask=image.getchannel("A"))
            flattened.save(path, "JPEG", quality=90, optimize=True)
        return filename
    except Exception:
        path.unlink(missing_ok=True)
        raise


def background_response(row: GeneralSettings | None) -> LoginBackgroundResponse:
    if row is None:
        return LoginBackgroundResponse()
    return LoginBackgroundResponse.model_validate(
        {
            "mode": row.login_background_mode,
            "urls": row.login_background_urls,
            "interval_seconds": row.login_background_interval,
            "image_url": (
                f"/static/login-backgrounds/{row.login_background_path}"
                if row.login_background_path
                else None
            ),
        }
    )

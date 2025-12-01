from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings


def store_uploaded_file(upload: UploadFile, upload_dir: Path | None = None) -> Path:
    target_dir = upload_dir or settings.upload_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}_{upload.filename}"
    filepath = target_dir / filename

    with open(filepath, "wb") as f:
        contents = upload.file.read()
        f.write(contents)

    return filepath

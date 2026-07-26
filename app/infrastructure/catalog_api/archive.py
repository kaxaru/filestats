"""Распаковка ZIP-ответа каталога."""

from __future__ import annotations

import io
import logging
import zipfile

from app.domain.values import FileContent, FileName, InvalidFileName

logger = logging.getLogger(__name__)


def unpack(payload: bytes) -> dict[FileName, FileContent]:
    """Разобрать архив в отображение «имя → содержимое».

    Имена внутри архива приходят снаружи, поэтому проходят ту же проверку, что
    и всё остальное: запись с путём вместо имени — это попытка вылезти за
    пределы каталога, а не безобидная особенность формата.
    """
    files: dict[FileName, FileContent] = {}

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            raw_name = entry.filename.rsplit("/", 1)[-1]
            try:
                name = FileName.parse(raw_name)
            except InvalidFileName:
                logger.error("пропускаю запись архива с недопустимым именем: %r", entry.filename)
                continue

            content = FileContent.parse(archive.read(entry).decode("utf-8", errors="replace"))
            if not content.is_canonical:
                # Не отвергаем: файл уже скачан, выбросить его — потерять
                # безвозвратно. Но расхождение с ожидаемым форматом стоит знать.
                logger.warning("содержимое %s не каноническое: длина %s", name, content.length)
            files[name] = content

    return files

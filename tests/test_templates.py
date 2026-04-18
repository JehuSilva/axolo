from datetime import datetime, timezone
from pathlib import Path

from axolo.metadata import MediaCategory, MediaMetadata, MediaType
from axolo.templates import render_template


def build_metadata(path: Path) -> MediaMetadata:
    return MediaMetadata(
        source_path=path,
        media_type=MediaType.IMAGE,
        category=MediaCategory.PHOTOS_VIDEOS,
        captured_at=datetime(2023, 5, 17, 14, 30, tzinfo=timezone.utc),
        camera_make="Canon",
        camera_model="EOS 5D",
        original_name=path.name,
    )


def test_render_template_with_defaults(tmp_path):
    file_path = tmp_path / "photo.JPG"
    file_path.touch()
    metadata = build_metadata(file_path)
    result = render_template(metadata, "{year}/{month:02d}")
    assert result == Path("2023/05")


def test_render_template_with_month_name(tmp_path):
    file_path = tmp_path / "photo.JPG"
    file_path.touch()
    metadata = build_metadata(file_path)
    result = render_template(metadata, "{year}/{month_name}")
    assert result == Path("2023/mayo")


def test_render_template_with_month_name_short(tmp_path):
    file_path = tmp_path / "photo.JPG"
    file_path.touch()
    metadata = build_metadata(file_path)
    result = render_template(metadata, "{year}/{month_name_short}")
    assert result == Path("2023/may")


def test_render_template_includes_category(tmp_path):
    file_path = tmp_path / "photo.JPG"
    file_path.touch()
    metadata = build_metadata(file_path)
    result = render_template(metadata, "{category}/{year}")
    assert result == Path("Fotos y Videos/2023")


def test_render_template_unknown_placeholder_raises(tmp_path):
    file_path = tmp_path / "photo.JPG"
    file_path.touch()
    metadata = build_metadata(file_path)
    template = "{year}/{unknown}"
    try:
        render_template(metadata, template)
    except ValueError as exc:
        assert "desconocidos" in str(exc)
    else:  # pragma: no cover - se espera excepción
        raise AssertionError("render_template debía lanzar ValueError")

from pathlib import Path

from axolo.metadata import MediaCategory, MediaType, detect_media_type, resolve_category


def test_detect_media_type_for_audio():
    media_type = detect_media_type(Path("track.MP3"))
    assert media_type == MediaType.AUDIO
    assert resolve_category(media_type) == MediaCategory.MUSIC


def test_detect_media_type_for_document():
    media_type = detect_media_type(Path("manual.PDF"))
    assert media_type == MediaType.DOCUMENT
    assert resolve_category(media_type) == MediaCategory.DOCUMENTS


def test_detect_media_type_for_other_extension():
    media_type = detect_media_type(Path("archive.xyz"))
    assert media_type == MediaType.OTHER
    assert resolve_category(media_type) == MediaCategory.OTHER

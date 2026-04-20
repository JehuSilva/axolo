"""Utilities for extracting media metadata."""

from __future__ import annotations

import enum
import json
import logging
import re
import struct
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO, Callable, Optional, Sequence
from xml.etree import ElementTree as ET

from dateutil import parser as date_parser
from PIL import ExifTags, Image

logger = logging.getLogger(__name__)

try:  # pragma: no cover - only used in production with HEIC support
    from pillow_heif import register_heif_opener  # type: ignore[import]

    register_heif_opener()
except ImportError:  # pragma: no cover
    logger.debug(
        "pillow-heif is not installed; HEIC files will be processed without native support"
    )

try:  # pragma: no cover - dependencias opcionales
    import mutagen  # type: ignore
except ImportError:  # pragma: no cover
    mutagen = None

try:  # pragma: no cover
    from pypdf import PdfReader  # type: ignore
except ImportError:  # pragma: no cover
    PdfReader = None  # type: ignore


class MediaType(enum.Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    OTHER = "other"


class MediaCategory(enum.Enum):
    PHOTOS_VIDEOS = ("Fotos y Videos", "Fotos y Videos")
    MUSIC = ("Musica", "Musica")
    DOCUMENTS = ("Documentos", "Documentos")
    HIDDEN = ("Ocultos", "Ocultos")
    OTHER = ("Otros", "Otros")

    def label(self) -> str:
        return self.value[0]

    def folder_name(self) -> str:
        return self.value[1]


class TimestampSource(enum.Enum):
    METADATA = "metadata"
    FILE_CREATION = "file_creation"
    FILE_MODIFICATION = "file_modification"
    FILENAME = "filename"
    CONTAINER_METADATA = "container_metadata"
    UNKNOWN = "unknown"


@dataclass
class MediaMetadata:
    """Metadata relevante para organizar archivos multimedia."""

    source_path: Path
    media_type: MediaType
    category: MediaCategory
    captured_at: datetime
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    original_name: Optional[str] = None
    timestamp_source: TimestampSource = TimestampSource.METADATA
    is_panoramic: bool = False
    music_artist: Optional[str] = None
    music_title: Optional[str] = None
    music_genre: Optional[str] = None
    music_album: Optional[str] = None

    @property
    def stem(self) -> str:
        return self.source_path.stem

    @property
    def suffix(self) -> str:
        return self.source_path.suffix.lower()

    @property
    def has_reliable_timestamp(self) -> bool:
        return self.timestamp_source not in {
            TimestampSource.UNKNOWN,
            TimestampSource.FILE_MODIFICATION,
        }


IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".tiff",
    ".tif",
    ".heic",
    ".heif",
    ".raw",
    ".cr2",
    ".nef",
    ".arw",
    ".dng",
    ".insp",
}

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".mkv",
    ".avi",
    ".wmv",
    ".mpg",
    ".mpeg",
    ".3gp",
    ".webm",
    ".mts",
    ".m2ts",
    ".insv",
}

PANORAMIC_360_EXTENSIONS = {".insp", ".insv"}

AUDIO_EXTENSIONS = {
    ".mp3",
    ".aac",
    ".flac",
    ".wav",
    ".ogg",
    ".oga",
    ".m4a",
    ".wma",
    ".aiff",
    ".aif",
}

DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".txt",
    ".rtf",
    ".odt",
    ".ods",
    ".odp",
}

QUICKTIME_EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)

_FILENAME_PATTERNS: list[tuple[re.Pattern[str], bool]] = [
    (
        re.compile(
            r"(?P<year>[12]\d{3})(?P<month>\d{2})(?P<day>\d{2})[-_T ]?(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})"
        ),
        True,
    ),
    (
        re.compile(
            r"(?P<year>[12]\d{3})[-_](?P<month>\d{2})[-_](?P<day>\d{2})[-_T ](?P<hour>\d{2})[-_](?P<minute>\d{2})[-_](?P<second>\d{2})"
        ),
        True,
    ),
    (
        re.compile(r"(?P<year>[12]\d{3})(?P<month>\d{2})(?P<day>\d{2})"),
        False,
    ),
    (
        re.compile(r"(?P<year>[12]\d{3})[-_](?P<month>\d{2})[-_](?P<day>\d{2})"),
        False,
    ),
]


def detect_media_type(path: Path) -> MediaType:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return MediaType.IMAGE
    if suffix in VIDEO_EXTENSIONS:
        return MediaType.VIDEO
    if suffix in AUDIO_EXTENSIONS:
        return MediaType.AUDIO
    if suffix in DOCUMENT_EXTENSIONS:
        return MediaType.DOCUMENT
    return MediaType.OTHER


def resolve_category(media_type: MediaType) -> MediaCategory:
    if media_type in {MediaType.IMAGE, MediaType.VIDEO}:
        return MediaCategory.PHOTOS_VIDEOS
    if media_type == MediaType.AUDIO:
        return MediaCategory.MUSIC
    if media_type == MediaType.DOCUMENT:
        return MediaCategory.DOCUMENTS
    return MediaCategory.OTHER


def extract_metadata(path: Path) -> MediaMetadata:
    media_type = detect_media_type(path)
    # Hidden files (name starts with '.') get their own category regardless of type.
    is_hidden = path.name.startswith(".")
    category = MediaCategory.HIDDEN if is_hidden else resolve_category(media_type)
    captured_at: Optional[datetime] = None
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    timestamp_source = TimestampSource.UNKNOWN
    music_artist: Optional[str] = None
    music_title: Optional[str] = None
    music_genre: Optional[str] = None
    music_album: Optional[str] = None

    if media_type == MediaType.IMAGE:
        captured_at, camera_make, camera_model, timestamp_source = _extract_image_metadata(path)
    elif media_type == MediaType.VIDEO:
        captured_at, camera_make, camera_model, timestamp_source = _extract_video_metadata(path)
    elif media_type == MediaType.AUDIO:
        captured_at, timestamp_source, music_artist, music_title, music_genre, music_album = _extract_audio_metadata(path)
    elif media_type == MediaType.DOCUMENT:
        captured_at, timestamp_source = _extract_document_metadata(path)

    if captured_at is None:
        captured_at, timestamp_source = _extract_timestamp_from_filename(path)

    if captured_at is None:
        captured_at, timestamp_source = _filesystem_timestamp(path)

    suffix = path.suffix.lower()
    is_panoramic = suffix in PANORAMIC_360_EXTENSIONS

    # Fallback make/model for Insta360 formats when not recoverable from metadata.
    if is_panoramic and not camera_make:
        camera_make = "Arashi Vision"
    if is_panoramic and not camera_model:
        camera_model = "Insta360 X3"

    return MediaMetadata(
        source_path=path,
        media_type=media_type,
        category=category,
        captured_at=captured_at,
        camera_make=camera_make,
        camera_model=camera_model,
        original_name=path.name,
        timestamp_source=timestamp_source,
        is_panoramic=is_panoramic,
        music_artist=music_artist,
        music_title=music_title,
        music_genre=music_genre,
        music_album=music_album,
    )


# EXIF tag IDs leídos en el path de producción. Se consultan directamente (sin
# construir el dict completo) para minimizar memoria en lotes grandes.
# Usa _read_exif_dict si necesitas el volcado completo (scripts, debugging).
_EXIF_TAG_MAKE = 0x010F
_EXIF_TAG_MODEL = 0x0110
_EXIF_TAG_DATETIME = 0x0132
_EXIF_TAG_DATETIME_ORIGINAL = 0x9003
_EXIF_TAG_DATETIME_DIGITIZED = 0x9004


def _extract_image_metadata(
    path: Path,
) -> tuple[Optional[datetime], Optional[str], Optional[str], TimestampSource]:
    try:
        with Image.open(path) as img:
            exif_obj = _get_exif_object(img)
            if exif_obj is None:
                return None, None, None, TimestampSource.UNKNOWN

            make = exif_obj.get(_EXIF_TAG_MAKE)
            model = exif_obj.get(_EXIF_TAG_MODEL)
            date_main = exif_obj.get(_EXIF_TAG_DATETIME)

            date_original: object = None
            date_digitized: object = None
            try:
                sub = exif_obj.get_ifd(ExifTags.IFD.Exif)
            except (KeyError, AttributeError, OSError):
                sub = None
            if sub:
                date_original = sub.get(_EXIF_TAG_DATETIME_ORIGINAL)
                date_digitized = sub.get(_EXIF_TAG_DATETIME_DIGITIZED)
    except Exception as exc:  # pragma: no cover - PIL lanza errores variados por archivos corruptos
        logger.debug("No fue posible leer EXIF de %s: %s", path, exc)
        return None, None, None, TimestampSource.UNKNOWN

    date_value = date_original or date_digitized or date_main
    captured_at = _parse_exif_datetime(str(date_value)) if date_value else None
    timestamp_source = TimestampSource.METADATA if captured_at else TimestampSource.UNKNOWN

    return captured_at, _clean_string(make), _clean_string(model), timestamp_source


def _get_exif_object(image: Image.Image) -> Optional["Image.Exif"]:
    """Devuelve un objeto Exif utilizable o None. No construye el dict completo."""
    try:
        exif = image.getexif()  # type: ignore[attr-defined]
    except AttributeError:
        exif = None

    if exif:
        return exif

    exif_bytes = getattr(image, "info", {}).get("exif")
    if not exif_bytes or not hasattr(Image, "Exif"):
        return None
    try:
        loaded = Image.Exif()
        loaded.load(exif_bytes)
    except Exception as exc:  # pragma: no cover
        logger.debug(
            "No se pudo decodificar EXIF bytes para %s: %s",
            getattr(image, "filename", "desconocido"),
            exc,
        )
        return None
    return loaded if len(loaded) else None


def _extract_video_metadata(
    path: Path,
) -> tuple[Optional[datetime], Optional[str], Optional[str], TimestampSource]:
    captured_at, camera_make, camera_model, source = _extract_video_metadata_ffprobe(path)
    if captured_at:
        return captured_at, camera_make, camera_model, source

    container_datetime = _extract_quicktime_creation(path)
    if container_datetime:
        return container_datetime, None, None, TimestampSource.CONTAINER_METADATA

    return None, None, None, TimestampSource.UNKNOWN


def _extract_video_metadata_ffprobe(
    path: Path,
) -> tuple[Optional[datetime], Optional[str], Optional[str], TimestampSource]:
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_entries",
        (
            "format_tags=creation_time,"
            "com.apple.quicktime.creationdate,"
            "create_date,"
            "creation_date,"
            "date,"
            "make,"
            "model,"
            "com.insta360.model"
        ),
        "-show_entries",
        (
            "stream_tags=creation_time,"
            "com.apple.quicktime.creationdate,"
            "create_date,"
            "creation_date,"
            "date,"
            "make,"
            "model,"
            "com.insta360.model"
        ),
        str(path),
    ]
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        logger.debug("ffprobe no está instalado; usando marca de tiempo del sistema.")
        return None, None, None, TimestampSource.UNKNOWN
    except subprocess.CalledProcessError as exc:
        logger.debug("ffprobe falló en %s: %s", path, exc)
        return None, None, None, TimestampSource.UNKNOWN

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.debug("ffprobe devolvió una salida no válida para %s", path)
        return None, None, None, TimestampSource.UNKNOWN

    tags_sources: list[dict[str, str]] = []
    format_tags = payload.get("format", {}).get("tags")
    if isinstance(format_tags, dict):
        tags_sources.append(format_tags)

    for stream in payload.get("streams", []) or []:
        stream_tags = stream.get("tags")
        if isinstance(stream_tags, dict):
            tags_sources.append(stream_tags)

    timestamp_tag_keys = [
        "com.apple.quicktime.creationdate",
        "creation_time",
        "create_date",
        "creation_date",
        "date",
        "CreationDate",
    ]
    camera_make_keys = ["make"]
    camera_model_keys = ["com.insta360.model", "model"]

    captured_at: Optional[datetime] = None
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None

    for tags in tags_sources:
        if captured_at is None:
            for key in timestamp_tag_keys:
                value = tags.get(key)
                if value:
                    parsed = _parse_flexible_datetime(value)
                    if parsed:
                        captured_at = parsed
                        break
        if camera_make is None:
            for key in camera_make_keys:
                value = tags.get(key)
                if value and isinstance(value, str) and value.strip():
                    camera_make = value.strip()
                    break
        if camera_model is None:
            for key in camera_model_keys:
                value = tags.get(key)
                if value and isinstance(value, str) and value.strip():
                    camera_model = value.strip()
                    break

    if captured_at:
        return captured_at, camera_make, camera_model, TimestampSource.METADATA

    return None, camera_make, camera_model, TimestampSource.UNKNOWN


def _extract_quicktime_creation(path: Path) -> Optional[datetime]:
    try:
        with path.open("rb") as handle:
            return _parse_quicktime_stream(handle)
    except (OSError, ValueError) as exc:
        logger.debug("No se pudo leer metadata QuickTime en %s: %s", path, exc)
        return None


def _parse_quicktime_stream(handle: BinaryIO) -> Optional[datetime]:
    while True:
        header = handle.read(8)
        if len(header) < 8:
            return None
        size, atom_type = struct.unpack(">I4s", header)
        if size == 0:
            return None
        header_length = 8
        if size == 1:
            extended = handle.read(8)
            if len(extended) < 8:
                return None
            size = struct.unpack(">Q", extended)[0]
            header_length = 16
        payload_size = size - header_length
        if payload_size < 0:
            return None
        if atom_type == b"moov":
            data = handle.read(payload_size)
            if len(data) != payload_size:
                return None
            creation = _parse_quicktime_moov(data)
            if creation:
                return creation
        else:
            handle.seek(payload_size, 1)


def _parse_quicktime_moov(data: bytes) -> Optional[datetime]:
    offset = 0
    length = len(data)
    while offset + 8 <= length:
        size = struct.unpack(">I", data[offset : offset + 4])[0]
        atom_type = data[offset + 4 : offset + 8]
        header_length = 8
        if size == 1:
            if offset + 16 > length:
                return None
            size = struct.unpack(">Q", data[offset + 8 : offset + 16])[0]
            header_length = 16
        elif size == 0:
            size = length - offset
        if size < header_length or offset + size > length:
            return None
        start = offset + header_length
        end = offset + size
        payload = data[start:end]
        if atom_type == b"mvhd":
            creation = _parse_quicktime_header_atom(payload)
            if creation:
                return creation
        elif atom_type == b"trak":
            creation = _parse_quicktime_trak(payload)
            if creation:
                return creation
        offset += size
    return None


def _parse_quicktime_trak(data: bytes) -> Optional[datetime]:
    offset = 0
    length = len(data)
    while offset + 8 <= length:
        size = struct.unpack(">I", data[offset : offset + 4])[0]
        atom_type = data[offset + 4 : offset + 8]
        header_length = 8
        if size == 1:
            if offset + 16 > length:
                return None
            size = struct.unpack(">Q", data[offset + 8 : offset + 16])[0]
            header_length = 16
        elif size == 0:
            size = length - offset
        if size < header_length or offset + size > length:
            return None
        start = offset + header_length
        end = offset + size
        payload = data[start:end]
        if atom_type == b"tkhd":
            creation = _parse_quicktime_header_atom(payload)
            if creation:
                return creation
        offset += size
    return None


def _parse_quicktime_header_atom(data: bytes) -> Optional[datetime]:
    if len(data) < 8:
        return None
    version = data[0]
    if version == 1:
        if len(data) < 20:
            return None
        creation_value = struct.unpack(">Q", data[4:12])[0]
    else:
        creation_value = struct.unpack(">I", data[4:8])[0]
    return _quicktime_epoch_to_datetime(creation_value)


def _quicktime_epoch_to_datetime(value: int) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = QUICKTIME_EPOCH + timedelta(seconds=int(value))
    except (OverflowError, ValueError):
        return None
    return dt.astimezone()


_AUDIO_DATE_KEYS = ["TDRC", "TDOR", "TORY", "TDRL", "DATE", "Year", "YEAR", "year", "TYER", "©day"]
_AUDIO_ARTIST_KEYS = ["TPE1", "artist", "ARTIST", "©ART", "Author"]
_AUDIO_TITLE_KEYS = ["TIT2", "title", "TITLE", "©nam"]
_AUDIO_GENRE_KEYS = ["TCON", "genre", "GENRE", "©gen"]
_AUDIO_ALBUM_KEYS = ["TALB", "album", "ALBUM", "©alb"]


def _extract_audio_metadata(
    path: Path,
) -> tuple[Optional[datetime], TimestampSource, Optional[str], Optional[str], Optional[str], Optional[str]]:
    if mutagen is None:
        logger.debug("mutagen no está instalado; usando timestamp del sistema para %s", path)
        return None, TimestampSource.UNKNOWN, None, None, None, None

    try:
        audio = mutagen.File(path)  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover - mutagen lanza distintos errores según formato
        logger.debug("No fue posible leer metadatos de audio en %s: %s", path, exc)
        return None, TimestampSource.UNKNOWN, None, None, None, None

    if audio is None:
        return None, TimestampSource.UNKNOWN, None, None, None, None

    tags = getattr(audio, "tags", None)
    if not tags:
        return None, TimestampSource.UNKNOWN, None, None, None, None

    def _first_tag(keys: list[str]) -> Optional[str]:
        for key in keys:
            value = tags.get(key)
            if value is not None:
                normalized = _normalize_tag_value(value)
                if normalized:
                    return normalized
        return None

    captured_at: Optional[datetime] = None
    timestamp_source = TimestampSource.UNKNOWN
    for key in _AUDIO_DATE_KEYS:
        value = tags.get(key)
        if value is None:
            continue
        normalized = _normalize_tag_value(value)
        if not normalized:
            continue
        parsed = _parse_flexible_datetime(normalized)
        if parsed:
            captured_at = parsed
            timestamp_source = TimestampSource.METADATA
            break

    music_artist = _first_tag(_AUDIO_ARTIST_KEYS)
    music_title = _first_tag(_AUDIO_TITLE_KEYS)
    music_genre = _first_tag(_AUDIO_GENRE_KEYS)
    music_album = _first_tag(_AUDIO_ALBUM_KEYS)

    return captured_at, timestamp_source, music_artist, music_title, music_genre, music_album


def _extract_document_metadata(path: Path) -> tuple[Optional[datetime], TimestampSource]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf_metadata(path)
    if suffix in {".docx", ".pptx", ".xlsx"}:
        return _extract_office_metadata(path, "docProps/core.xml", _parse_flexible_datetime)
    if suffix in {".odt", ".ods", ".odp"}:
        return _extract_office_metadata(path, "meta.xml", _parse_odf_datetime)
    return None, TimestampSource.UNKNOWN


def _extract_pdf_metadata(path: Path) -> tuple[Optional[datetime], TimestampSource]:
    if PdfReader is None:
        logger.debug("pypdf no está instalado; usando timestamp del sistema para %s", path)
        return None, TimestampSource.UNKNOWN

    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # pragma: no cover - pypdf puede lanzar diversas excepciones
        logger.debug("No fue posible leer metadatos de PDF en %s: %s", path, exc)
        return None, TimestampSource.UNKNOWN

    metadata = getattr(reader, "metadata", None) or getattr(reader, "documentInfo", None)
    if not metadata:
        return None, TimestampSource.UNKNOWN

    candidates = [
        getattr(metadata, "creation_date", None),
        getattr(metadata, "modification_date", None),
        metadata.get("/CreationDate") if hasattr(metadata, "get") else None,
        metadata.get("/ModDate") if hasattr(metadata, "get") else None,
    ]

    for value in candidates:
        if not value:
            continue
        parsed = _parse_pdf_date(str(value))
        if parsed:
            return parsed, TimestampSource.METADATA

    return None, TimestampSource.UNKNOWN


def _extract_office_metadata(
    path: Path,
    core_path: str,
    parser: Callable[[str], Optional[datetime]],
) -> tuple[Optional[datetime], TimestampSource]:
    try:
        with zipfile.ZipFile(path) as archive:
            with archive.open(core_path) as handle:
                data = handle.read()
    except (FileNotFoundError, KeyError, zipfile.BadZipFile) as exc:
        logger.debug("No se encontró metadata %s en %s: %s", core_path, path, exc)
        return None, TimestampSource.UNKNOWN

    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        logger.debug("No se pudo parsear metadata XML en %s: %s", path, exc)
        return None, TimestampSource.UNKNOWN

    if core_path == "docProps/core.xml":
        namespaces = {
            "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
            "dc": "http://purl.org/dc/elements/1.1/",
            "dcterms": "http://purl.org/dc/terms/",
        }
        candidates = [
            root.find("dcterms:created", namespaces),
            root.find("dcterms:modified", namespaces),
        ]
    else:
        namespaces = {
            "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
            "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
        }
        candidates = [
            root.find(".//meta:creation-date", namespaces),
            root.find(".//dc:date", {"dc": "http://purl.org/dc/elements/1.1/"}),
        ]

    for node in candidates:
        if node is None or not node.text:
            continue
        parsed = parser(node.text.strip())
        if parsed:
            return parsed, TimestampSource.METADATA

    return None, TimestampSource.UNKNOWN


def _extract_timestamp_from_filename(path: Path) -> tuple[Optional[datetime], TimestampSource]:
    timestamp = _parse_timestamp_from_filename(path.stem)
    if timestamp is None:
        timestamp = _parse_timestamp_from_filename(path.name)
    if timestamp is None:
        return None, TimestampSource.UNKNOWN
    return timestamp, TimestampSource.FILENAME


def _parse_timestamp_from_filename(value: str) -> Optional[datetime]:
    if not value:
        return None
    for pattern, includes_time in _FILENAME_PATTERNS:
        match = pattern.search(value)
        if not match:
            continue
        try:
            year = int(match.group("year"))
            month = int(match.group("month"))
            day = int(match.group("day"))
        except (KeyError, ValueError):
            continue

        hour = minute = second = 0
        if includes_time:
            try:
                hour = int(match.group("hour"))
                minute = int(match.group("minute"))
                second = int(match.group("second"))
            except (KeyError, ValueError, TypeError):
                continue

        current_year = datetime.now().year
        if not (1970 <= year <= current_year + 1):
            continue

        try:
            naive = datetime(year, month, day, hour, minute, second)
        except ValueError:
            continue

        local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        return naive.replace(tzinfo=local_tz)
    return None


def _filesystem_timestamp(path: Path) -> tuple[datetime, TimestampSource]:
    stat = path.stat()
    timestamp_source = TimestampSource.UNKNOWN

    # Prefer birth/creation time when the platform exposes it.
    birthtime = getattr(stat, "st_birthtime", None)
    if birthtime:
        timestamp = birthtime
        timestamp_source = TimestampSource.FILE_CREATION
    else:
        timestamp = stat.st_mtime or stat.st_ctime
        timestamp_source = TimestampSource.FILE_MODIFICATION

    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone()
    return dt, timestamp_source


def _normalize_tag_value(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="ignore")
        except Exception:  # pragma: no cover - decodificaciones variadas
            value = value.decode("latin-1", errors="ignore")
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            normalized = _normalize_tag_value(item)
            if normalized:
                return normalized
        return None
    text = getattr(value, "text", None)
    if text is not None:
        return _normalize_tag_value(text)
    try:
        string_value = str(value)
    except Exception:
        return None
    return string_value.strip() or None


def _parse_flexible_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    cleaned = cleaned.replace("\\", " ")
    try:
        parsed = date_parser.parse(cleaned)
    except (ValueError, TypeError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def _parse_pdf_date(value: str) -> Optional[datetime]:
    if not value:
        return None
    cleaned = value.strip()
    if cleaned.startswith("D:"):
        cleaned = cleaned[2:]
    cleaned = cleaned.replace("'", "")
    cleaned = cleaned.replace(" ", "")

    if len(cleaned) >= 14 and cleaned[:14].isdigit():
        main = cleaned[:14]
        remainder = cleaned[14:]
        formatted = (
            f"{main[0:4]}-{main[4:6]}-{main[6:8]}T"
            f"{main[8:10]}:{main[10:12]}:{main[12:14]}"
        )
        if remainder:
            formatted += remainder
    else:
        formatted = cleaned

    return _parse_flexible_datetime(formatted)


def _parse_odf_datetime(value: str) -> Optional[datetime]:
    return _parse_flexible_datetime(value)


def _parse_exif_datetime(value: str) -> Optional[datetime]:
    try:
        dt = datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
    except (ValueError, TypeError):
        logger.debug("Formato EXIF inesperado: %s", value)
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone()


def _merge_exif_into(exif_obj: "Image.Exif", out: dict[str, object]) -> None:
    for tag_id, value in exif_obj.items():
        out[ExifTags.TAGS.get(tag_id, str(tag_id))] = value

    # DateTimeOriginal/DateTimeDigitized viven en ExifTags.IFD.Exif; GPS en GPSInfo.
    # En HEIC (vía pillow-heif) Pillow suele poblar SOLO los sub-IFDs, por eso es
    # imprescindible fusionarlos.
    sub_ifds = (
        (ExifTags.IFD.Exif, ExifTags.TAGS),
        (ExifTags.IFD.GPSInfo, ExifTags.GPSTAGS),
    )
    for ifd_id, tag_map in sub_ifds:
        try:
            sub = exif_obj.get_ifd(ifd_id)
        except (KeyError, AttributeError, OSError):
            continue
        if not sub:
            continue
        for tag_id, value in sub.items():
            out[tag_map.get(tag_id, str(tag_id))] = value


def _read_exif_dict(image: Image.Image) -> dict[str, object]:
    """Devuelve un diccionario legible de etiquetas EXIF."""
    exif_data: dict[str, object] = {}
    raw_exif = None
    try:
        raw_exif = image.getexif()  # type: ignore[attr-defined]
    except AttributeError:
        raw_exif = None

    if raw_exif:
        _merge_exif_into(raw_exif, exif_data)

    if not exif_data:
        exif_bytes = getattr(image, "info", {}).get("exif")
        if exif_bytes and hasattr(Image, "Exif"):
            try:
                exif = Image.Exif()
                exif.load(exif_bytes)
                _merge_exif_into(exif, exif_data)
            except Exception as exc:  # pragma: no cover - Pillow puede no soportar ciertos EXIF
                logger.debug(
                    "No se pudo decodificar EXIF bytes para %s: %s",
                    getattr(image, "filename", "desconocido"),
                    exc,
                )

    return exif_data


def _clean_string(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return str(value).strip() or None

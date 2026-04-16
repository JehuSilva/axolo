# Media Organizer

Automatic organizer for photos, videos, audio and documents with configurable destination templates.

## Características

- Extrae metadatos (EXIF, ID3, PDF, Office) para ordenar archivos multimedia y documentos.
- Clasifica automáticamente en categorías (`Fotos y Videos`, `Musica`, `Documentos`, `Otros`).
- Dentro de cada categoría organiza por subcarpeta (Fotos/, Videos/, 360/…) y luego por año/mes (personalizable mediante plantillas).
- Agrupa fotografías y videos en eventos sugeridos mediante clustering temporal configurable.
- Identifica fotografías potencialmente duplicadas usando hashing perceptual.
- Visualiza la cantidad de capturas por periodo (hora, día, semana, mes o año) sin mover archivos.
- Modo `dry-run` para validar resultados sin mover archivos.
- Soporte para HEIC mediante `pillow-heif` y compatibilidad ampliada con videos (ffprobe y tags DJI).
- Soporte nativo para cámaras 360° (Insta360 X3): formatos `.insp`, `.insv` y `.dng`; archivos 360 se ubican en `Fotos_y_Videos/360/`; los pares de lentes (`_00_`/`_10_`) se agrupan como un solo asset en los reportes.
- Archivos sin fecha confiable se ubican automáticamente en `unknown_date/` dentro de su categoría.

## Requisitos

- Python 3.10 o superior.
- [FFmpeg](https://ffmpeg.org/) instalado y disponible en el `PATH` para extraer metadatos de video/audio.
- Las dependencias se instalan con `pip install -e .` e incluyen `mutagen` (audio) y `pypdf` (PDF).

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate   # En Windows: .venv\Scripts\activate
pip install -e ".[dev]"     # incluye dependencias de desarrollo y pruebas
```

## Configuración (`config.yaml`)

El método recomendado es definir la configuración de ejecución en un archivo YAML. Copia el ejemplo incluido y ajústalo:

```bash
cp profiles.sample.yaml config.yaml
```

Estructura mínima:

```yaml
source: ~/Media
destination: /mnt/organizado
action: copy          # move | copy | link
dry_run: false
recursive: true
follow_symlinks: false
```

### Perfiles por categoría (`profiles:`)

La sección `profiles:` del YAML controla qué template de carpeta (y opcionalmente de renombrado) aplica cada tipo de archivo. Las claves válidas son:

| Clave | Descripción | Subcarpeta dentro de la categoría |
|-------|-------------|-----------------------------------|
| `fotos` | Fotos no panorámicas | `Fotos_y_Videos/Fotos/` |
| `videos` | Videos no panorámicos | `Fotos_y_Videos/Videos/` |
| `360-fotos` | Fotos panorámicas (.insp) | `Fotos_y_Videos/360/Fotos/` |
| `360-videos` | Videos panorámicos (.insv) | `Fotos_y_Videos/360/Videos/` |
| `musica` | Audio (también acepta `music`) | `Musica/` |
| `documentos` | Documentos (también acepta `docs`) | `Documentos/` |
| `otros` | Todo lo demás (también acepta `other`) | `Otros/` |

Ejemplo completo con todos los perfiles:

```yaml
source: ~/Media
destination: /mnt/organizado
action: copy

profiles:
  - name: fotos
    template: year_month_cap
    # → Fotos_y_Videos/Fotos/2026/Abril/foto.jpg

  - name: videos
    template: year_month_cap
    # → Fotos_y_Videos/Videos/2026/Abril/video.mp4

  - name: 360-fotos
    template: year_month_cap
    # → Fotos_y_Videos/360/Fotos/2026/Abril/img.insp

  - name: 360-videos
    template: year_month_cap
    # → Fotos_y_Videos/360/Videos/2026/Abril/vid.insv

  - name: musica
    template: music_genre
    filename_template: "{music_artist} - {music_title}"
    # → Musica/rock/the-beatles_let-it-be.mp3

  - name: documentos
    template: year_month_cap
    # → Documentos/2026/Abril/contrato.pdf

  - name: otros
    template: year_month_name
    # → Otros/2026/abril/archivo.zip

dry_run: false
recursive: true
follow_symlinks: false
```

### Defaults por categoría (sin config YAML)

Si no se especifica template para una categoría, se aplican estos valores por defecto:

| Categoría | Default | Ejemplo de salida |
|-----------|---------|-------------------|
| Fotos | `{year}/{month_name_cap}` | `Fotos_y_Videos/Fotos/2026/Abril/foto.jpg` |
| Videos | `{year}/{month_name_cap}` | `Fotos_y_Videos/Videos/2026/Abril/video.mp4` |
| Fotos 360 | `{year}/{month_name_cap}` | `Fotos_y_Videos/360/Fotos/2026/Abril/img.insp` |
| Videos 360 | `{year}/{month_name_cap}` | `Fotos_y_Videos/360/Videos/2026/Abril/vid.insv` |
| Música | `{music_genre}/{music_artist}` + renombrado `Artista_Titulo` | `Musica/rock/the-beatles/the-beatles_let-it-be.mp3` |
| Documentos | `{year}/{month_name_cap}` | `Documentos/2026/Abril/contrato.pdf` |
| Otros | `{year}/{month_name}` | `Otros/2026/abril/archivo.zip` |

## Uso rápido

```bash
# Con archivo de configuración
media-organizer run --config config.yaml --dry-run

# Sin archivo de configuración (el CLI solicita los campos faltantes)
media-organizer run --source ~/Media --destination /mnt/organizado --dry-run

# Sobreescribir configuración del YAML desde la línea de comandos
media-organizer run --config config.yaml --dry-run --action copy
```

### Validar la configuración

El flag `--dry-run` nunca modifica archivos. Úsalo para revisar el plan antes de ejecutar:

```bash
media-organizer run --config config.yaml --dry-run
```

La salida muestra una tabla con el archivo de origen, el destino calculado, la categoría y el estado. Comprueba que los destinos sean los esperados antes de quitar `--dry-run`.

### CLI completo

- `run` organiza físicamente los archivos según el template configurado.
- `cluster` genera agrupaciones sugiriendo álbumes sin mover archivos.
- `similars` detecta fotos parecidas o duplicadas mediante hashing perceptual.
- `timeline` resume cuántas capturas hay por periodo y permite exportar la serie temporal.
- `--config` ruta al archivo YAML de configuración (fuente, destino, acción y perfiles por categoría).
- `--template` acepta un formato personalizado que se aplica como fallback a todas las categorías.
- `--extra clave=valor` agrega variables adicionales para usar en templates (ej: `--extra evento=Boda2026`).
- `--dry-run` muestra el plan sin mover archivos.

Los archivos que no tengan una fecha de captura confiable se agrupan en `unknown_date/` dentro de su categoría.

## Templates disponibles

### Templates con nombre

| Nombre | Patrón | Ejemplo |
|--------|--------|---------|
| `default` | `{year}/{month_name_cap}` | `2026/Abril` |
| `year_month_cap` | `{year}/{month_name_cap}` | `2026/Abril` |
| `year_month` | `{year}/{month:02d}` | `2026/04` |
| `year_month_name` | `{year}/{month_name}` | `2026/abril` |
| `year_month_name_day` | `{year}/{month_name_cap}/{month_name_cap} {day}` | `2026/Abril/Abril 15` |
| `year_month_day` | `{year}/{month:02d}/{day:02d}` | `2026/04/15` |
| `music_genre` | `{music_genre}` | `rock` |
| `music_genre_artist` | `{music_genre}/{music_artist}` | `rock/the-beatles` |
| `camera` | `{camera_make}/{camera_model}/{year}/{month:02d}` | `canon/eos-r5/2026/04` |

### Perfiles built-in (con `--profile`)

| Nombre | Template | Descripción |
|--------|----------|-------------|
| `fotos-cronologico` | `{year}/{month_name_cap}/{month_name_cap} {day}` | Año, mes y día |
| `fotos-compacto` | `{year}/{month:02d}/{day:02d}` | Carpetas numéricas |
| `fotos-por-camara` | `{camera_make}/{camera_model}/{year}/{month:02d}` | Agrupado por cámara |
| `musica` | `{music_genre}/{music_artist}` + `Artista_Titulo` | Género y artista |
| `musica-con-album` | `{music_genre}/{music_artist}/{music_album}` | Con álbum |
| `musica-por-artista` | `{music_artist}/{music_album}` | Sin género |
| `documentos` | `{year}/{month:02d}` | Año y mes numérico |
| `documentos-por-mes` | `{year}/{month_name_cap}` | Mes en español |
| `eventos` | `{year}/{month:02d}/{evento}` | Requiere `--extra evento=NombreEvento` |

### Placeholders disponibles

| Placeholder | Descripción | Ejemplo |
|-------------|-------------|---------|
| `{year}` | Año de captura | `2026` |
| `{month}` / `{month:02d}` | Mes numérico | `4` / `04` |
| `{day}` / `{day:02d}` | Día numérico | `5` / `05` |
| `{hour}`, `{minute}`, `{second}` | Hora de captura | `18`, `24`, `46` |
| `{month_name}` | Nombre de mes (español, minúsculas) | `abril` |
| `{month_name_short}` | Mes abreviado (español) | `abr` |
| `{month_name_cap}` | Mes capitalizado (español) | `Abril` |
| `{stem}` | Nombre de archivo sin extensión | `IMG_20260415` |
| `{ext}` | Extensión sin punto | `jpg` |
| `{camera_make}` | Marca de cámara (slug) | `canon` |
| `{camera_model}` | Modelo de cámara (slug) | `eos-r5` |
| `{music_artist}` | Artista (slug, desde ID3/Vorbis/MP4) | `the-beatles` |
| `{music_title}` | Título de canción (slug) | `let-it-be` |
| `{music_genre}` | Género musical (slug) | `rock` |
| `{music_album}` | Álbum (slug) | `abbey-road` |
| `{category}` | Nombre de carpeta de categoría | `Fotos_y_Videos` |
| `{category_label}` | Etiqueta legible de categoría | `Fotos y Videos` |
| `{category_slug}` | Slug de la categoría | `fotos-y-videos` |

## Comandos de análisis

### Agrupamiento de álbumes

```bash
media-organizer cluster \
  --source ~/Media \
  --time-window 120 \
  --min-samples 3 \
  --dry-run
```

- `--time-window` ventana temporal en minutos para considerar dos fotos parte del mismo evento (DBSCAN).
- `--min-samples` mínimo de elementos para formar un clúster.
- `--output clusters.json` guarda el resultado en JSON.

### Fotografías similares

```bash
media-organizer similars \
  --source ~/Media \
  --threshold 5 \
  --hash-size 16 \
  --output similitudes.json
```

- `--threshold` distancia Hamming máxima entre hashes (menor = más estricta).
- `--hash-size` sensibilidad del hash perceptual (8–16 suelen funcionar bien).
- `--method` elige entre `phash`, `ahash`, `dhash` o `whash`.

### Línea de tiempo de capturas

```bash
media-organizer timeline \
  --source ~/Media \
  --granularity month \
  --limit 40 \
  --output timeline.csv \
  --chart timeline.html
```

- Granularidades disponibles: `hour`, `day`, `week`, `month`, `year`.
- Exporta JSON/CSV/TSV y genera HTML con gráfico interactivo (Chart.js).

## Compatibilidad con cámaras 360 (Insta 360 X3)

| Extensión | Tipo | Metadatos |
|-----------|------|-----------|
| `.insp` | Foto 360 (JPEG con datos 360 al final) | EXIF vía Pillow |
| `.insv` | Video 360 (contenedor MP4) | QuickTime atoms + ffprobe |
| `.dng` | RAW (Adobe DNG) — no 360 | Nombre de archivo como fallback |

Los archivos `.insp` e `.insv` se organizan dentro de `Fotos_y_Videos/360/` separados del resto.

La cámara genera **pares de archivos por lente** (`_00_` y `_10_`). Los comandos `cluster`, `similars` y `timeline` los cuentan como un único asset. El comando `run` mueve o copia ambos archivos físicamente.

## Notas sobre HEIC

El paquete incluye `pillow-heif`. Si no está disponible en tu entorno, los archivos HEIC se procesarán con capacidades reducidas.

## Pruebas

```bash
pytest
pytest tests/test_organizer.py -v          # solo un archivo
pytest tests/test_organizer.py::test_media_organizer_resolves_collisions  # un test específico
pytest --cov=media_organizer               # con cobertura
```

# Axolo Data

Axolo Data — regenera el orden de tus archivos multimedia con plantillas configurables.

## Características

- Extrae metadatos (EXIF, ID3, PDF, Office, QuickTime) para ordenar archivos multimedia y documentos.
- Clasifica automáticamente en categorías (`Fotos y Videos`, `Musica`, `Documentos`, `Otros`).
- Dentro de cada categoría organiza por subcarpeta (Fotos/, Videos/, 360/…) y luego por año/mes (personalizable mediante plantillas).
- **Paralelismo**: metadata, hashing y movimiento de archivos en hilos concurrentes (`--workers N`).
- **Modo `dry-run`** para validar resultados sin mover archivos (activo por defecto en `duplicates`, `sync` y `undo`).
- **Journal de operaciones** (SQLite) para deshacer cualquier `run`, `duplicates` o `sync` ejecutado.
- **Sincronización dedup-aware**: `sync` añade solo contenido nuevo al destino nunca borra.
- **Asistente interactivo** (`tui`): menú guiado para todos los comandos sin necesidad de recordar flags.
- Soporte para HEIC mediante `pillow-heif` y compatibilidad ampliada con videos (ffprobe y tags DJI).
- Soporte nativo para cámaras 360° (Insta360 X3): formatos `.insp`, `.insv`; archivos 360 se ubican en `Fotos_y_Videos/360/`; los pares de lentes (`_00_`/`_10_`) se agrupan como un solo asset en los reportes.
- Archivos sin fecha confiable se ubican automáticamente en `unknown_date/` dentro de su categoría.

## Requisitos

- Python 3.10 o superior.
- [FFmpeg](https://ffmpeg.org/) instalado y disponible en el `PATH` para extraer metadatos de video/audio.
- Las dependencias se instalan con `pip install -e .` e incluyen `mutagen` (audio), `pypdf` (PDF), `questionary` (TUI) y `rich` (consola).

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate   # En Windows: .venv\Scripts\activate
pip install -e ".[dev]"     # incluye dependencias de desarrollo y pruebas
```

## Comandos disponibles

| Comando | Descripción |
|---------|-------------|
| `run` | Organiza archivos en la estructura de carpetas configurada |
| `duplicates` | Detecta y gestiona duplicados exactos (byte a byte) |
| `sync` | Sincroniza dos carpetas sin duplicar contenido |
| `undo` | Revierte operaciones de un run anterior |
| `tui` | Asistente interactivo que guía por todos los comandos |

---

## `run` — organizar archivos

```bash
# Con archivo de configuración
axolo run --config config.yaml --dry-run

# Sin archivo de configuración (el CLI solicita los campos faltantes)
axolo run --source ~/Media --destination /mnt/organizado --dry-run

# Mover archivos reales con 8 hilos
axolo run --source ~/Media --destination /mnt/organizado --action move --workers 8
```

### Flags principales

| Flag | Descripción | Default |
|------|-------------|---------|
| `--source` / `-s` | Directorio de origen | — |
| `--destination` / `-d` | Directorio de destino | — |
| `--action` | `move` \| `copy` \| `link` | Solicitado |
| `--link-kind` | `hard` \| `symbolic` (para `--action link`) | `symbolic` |
| `--template` | Template de carpeta o nombre de perfil | Solicitado |
| `--profile` / `-p` | Alias de `--template` | — |
| `--config` / `-c` | Ruta al archivo YAML de configuración | — |
| `--dry-run` / `--no-dry-run` | Simula sin modificar archivos | Solicitado |
| `--workers N` | Hilos paralelos (1–32) | `min(cpu_count, 8)` |
| `--no-journal` | Desactiva el registro de operaciones | Journal activo |
| `--quiet` | Suprime salida en consola | off |
| `--verbose` | Activa logging DEBUG | off |
| `--json-logs` | Emite logs en formato JSON Lines a stdout | off |
| `--extra clave=valor` | Variables adicionales para el template | — |

El flag `--dry-run` nunca modifica archivos. La salida muestra una tabla con el origen, destino calculado, categoría y estado.

---

## `duplicates` — detectar y gestionar duplicados

```bash
# Solo detección (no modifica nada)
axolo duplicates --source ~/Media

# Guardar reporte JSON
axolo duplicates --source ~/Media --output duplicates.json

# Mover duplicados a cuarentena (dry-run activo por defecto)
axolo duplicates --source ~/Media --action move --quarantine ~/Media/_dup --dry-run

# Ejecutar movimiento real
axolo duplicates --source ~/Media --action move --quarantine ~/Media/_dup --no-dry-run

# Reemplazar duplicados con hard links
axolo duplicates --source ~/Media --action link --no-dry-run

# Priorizar archivos en un directorio específico como "canónico"
axolo duplicates --source ~/Media --prefer-under ~/Media/Archivo
```

### Flags principales

| Flag | Descripción | Default |
|------|-------------|---------|
| `--source` / `-s` | Directorio a analizar | Requerido |
| `--algorithm` | `blake2b` \| `sha256` \| `md5` | `blake2b` |
| `--min-size` | Tamaño mínimo en bytes para comparar | `1` |
| `--prefer-under PATH` | Directorio cuyo contenido se considera canónico | — |
| `--action` | `move` \| `link` \| `delete` | — (solo reporta) |
| `--quarantine PATH` | Destino para `--action move` | — |
| `--link-kind` | `hard` \| `symbolic` (para `--action link`) | `hard` |
| `--dry-run` / `--no-dry-run` | Simula acciones sin ejecutarlas | `--dry-run` |
| `--output` / `-o` | Ruta para guardar el reporte JSON | — |
| `--workers N` | Hilos paralelos para hashing | `min(cpu_count, 8)` |

### Selección del archivo canónico

El canónico de cada grupo se elige con esta prioridad:
1. Archivos bajo el path indicado con `--prefer-under` (evita borrar el original si tiene una ruta más larga que la copia).
2. Archivo con `mtime` más antiguo (el más probable de ser el original).
3. Orden lexicográfico del path (desempate determinista).

---

## `sync` — sincronizar carpetas

Copia (o mueve) al destino únicamente el contenido que aún no existe allí, identificado por hash. Nunca borra archivos del destino.

```bash
# Ver qué se añadiría sin tocar nada
axolo sync --source ~/NuevosArchivos --destination ~/Archivo --dry-run

# Sincronizar de verdad
axolo sync --source ~/NuevosArchivos --destination ~/Archivo --action copy --no-dry-run

# Guardar el plan como JSON
axolo sync --source ~/A --destination ~/B --output plan.json
```

### Política de resolución de conflictos

| Situación | Resultado |
|-----------|-----------|
| Hash idéntico en destino | Archivo omitido (ya existe el contenido) |
| Nombre libre, hash nuevo | Archivo añadido normalmente |
| Nombre ocupado, contenido distinto | Archivo renombrado con sufijo `_<hash8>` |

### Flags principales

| Flag | Descripción | Default |
|------|-------------|---------|
| `--source` / `-s` | Directorio de origen | Requerido |
| `--destination` / `-d` | Directorio de destino | Requerido |
| `--action` | `copy` \| `move` | `copy` |
| `--algorithm` | `blake2b` \| `sha256` \| `md5` | `blake2b` |
| `--template` | Template de carpeta destino | `default` |
| `--dry-run` / `--no-dry-run` | Simula sin modificar archivos | `--dry-run` |
| `--output` / `-o` | Ruta para guardar el plan JSON | — |
| `--workers N` | Hilos paralelos | `min(cpu_count, 8)` |

---

## `undo` — deshacer operaciones

Revierte en orden inverso todas las operaciones de un `run`, `duplicates` o `sync` anterior.

```bash
# Ver los runs registrados en el journal
axolo undo --list

# Previsualizar qué desharía el último run
axolo undo --dry-run

# Deshacer un run específico de verdad
axolo undo --run-id <uuid> --no-dry-run
```

### Qué puede y no puede deshacer

| Acción original | Resultado del undo |
|-----------------|--------------------|
| `move` A → B | Mueve B de vuelta a A |
| `copy` A → B | Elimina B (la copia) |
| `link` (hard/sym) | Elimina el enlace creado |
| `delete` | No reversible; se reporta el error |

### Flags

| Flag | Descripción | Default |
|------|-------------|---------|
| `--run-id ID` | Run a revertir | Último run no revertido |
| `--list` | Lista runs recientes y sale | — |
| `--dry-run` / `--no-dry-run` | Simula sin modificar | `--dry-run` |
| `--limit N` | Número de runs a mostrar con `--list` | `10` |

### Journal

Las operaciones se guardan automáticamente en `~/.axolo/journal.db` (SQLite). Puedes cambiar la ruta con la variable de entorno `MEDIA_ORGANIZER_JOURNAL`.

---

## `tui` — asistente interactivo

Menú guiado que permite ejecutar cualquier comando sin recordar flags.

```bash
axolo tui
```

El asistente ofrece:

1. **Organizar archivos** — wizard para el comando `run`.
2. **Buscar duplicados** — wizard para `duplicates` con previsualización.
3. **Sincronizar carpetas** — wizard para `sync`.
4. **Ver historial y deshacer** — lista runs del journal, permite seleccionar uno y ejecutar `undo`.
5. **Salir**.

---

## Configuración (`config.yaml`)

El método recomendado es definir la configuración de ejecución en un archivo YAML:

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

| Clave | Descripción | Subcarpeta dentro de la categoría |
|-------|-------------|-----------------------------------|
| `fotos` | Fotos no panorámicas | `Fotos_y_Videos/Fotos/` |
| `videos` | Videos no panorámicos | `Fotos_y_Videos/Videos/` |
| `360-fotos` | Fotos panorámicas (.insp) | `Fotos_y_Videos/360/Fotos/` |
| `360-videos` | Videos panorámicos (.insv) | `Fotos_y_Videos/360/Videos/` |
| `musica` | Audio (alias: `music`) | `Musica/` |
| `documentos` | Documentos (alias: `docs`) | `Documentos/` |
| `otros` | Todo lo demás (alias: `other`) | `Otros/` |

Ejemplo completo:

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

  - name: musica
    template: music_genre
    filename_template: "{music_artist} - {music_title}"
    # → Musica/rock/the-beatles - let-it-be.mp3

  - name: documentos
    template: year_month_cap
    # → Documentos/2026/Abril/contrato.pdf

dry_run: false
recursive: true
```

### Defaults por categoría

| Categoría | Template por defecto | Ejemplo |
|-----------|---------------------|---------|
| Fotos | `{year}/{month_name_cap}` | `Fotos_y_Videos/Fotos/2026/Abril/foto.jpg` |
| Videos | `{year}/{month_name_cap}` | `Fotos_y_Videos/Videos/2026/Abril/video.mp4` |
| Fotos 360 | `{year}/{month_name_cap}` | `Fotos_y_Videos/360/Fotos/2026/Abril/img.insp` |
| Videos 360 | `{year}/{month_name_cap}` | `Fotos_y_Videos/360/Videos/2026/Abril/vid.insv` |
| Música | `{music_genre}/{music_artist}` + renombrado `Artista - Titulo` | `Musica/rock/the-beatles/` |
| Documentos | `{year}/{month_name_cap}` | `Documentos/2026/Abril/contrato.pdf` |
| Otros | `{year}/{month_name}` | `Otros/2026/abril/archivo.zip` |

---

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

| Nombre | Descripción |
|--------|-------------|
| `fotos-cronologico` | Año / mes / día en español |
| `fotos-compacto` | Carpetas numéricas `YYYY/MM/DD` |
| `fotos-por-camara` | Agrupado por marca y modelo de cámara |
| `musica` | Género y artista; renombra a `Artista - Titulo` |
| `musica-con-album` | Género / artista / álbum |
| `musica-por-artista` | Artista / álbum (sin género) |
| `documentos` | Año y mes numérico |
| `documentos-por-mes` | Año y mes en español |
| `eventos` | Requiere `--extra evento=NombreEvento` |
| `year-month` | `YYYY/MM` |
| `year-month-name` | `YYYY/nombre-mes` |

### Placeholders disponibles

| Placeholder | Descripción | Ejemplo |
|-------------|-------------|---------|
| `{year}` | Año de captura | `2026` |
| `{month}` / `{month:02d}` | Mes numérico | `4` / `04` |
| `{day}` / `{day:02d}` | Día numérico | `5` / `05` |
| `{hour}`, `{minute}`, `{second}` | Hora de captura | `18`, `24`, `46` |
| `{month_name}` | Mes en español (minúsculas) | `abril` |
| `{month_name_short}` | Mes abreviado (español) | `abr` |
| `{month_name_cap}` | Mes capitalizado (español) | `Abril` |
| `{stem}` | Nombre de archivo sin extensión | `IMG_20260415` |
| `{ext}` | Extensión sin punto | `jpg` |
| `{camera_make}` | Marca de cámara (slug) | `canon` |
| `{camera_model}` | Modelo de cámara (slug) | `eos-r5` |
| `{music_artist}` | Artista (desde ID3/Vorbis/MP4) | `the-beatles` |
| `{music_title}` | Título de canción | `let-it-be` |
| `{music_genre}` | Género musical | `rock` |
| `{music_album}` | Álbum | `abbey-road` |
| `{category}` | Carpeta de categoría | `Fotos_y_Videos` |
| `{category_label}` | Etiqueta legible de categoría | `Fotos y Videos` |
| `{category_slug}` | Slug de la categoría | `fotos-y-videos` |

Variables adicionales se pueden inyectar con `--extra clave=valor`.

---

## Compatibilidad con cámaras 360 (Insta 360 X3)

| Extensión | Tipo | Metadatos |
|-----------|------|-----------|
| `.insp` | Foto 360 (JPEG con datos 360 al final) | EXIF vía Pillow |
| `.insv` | Video 360 (contenedor MP4) | QuickTime atoms + ffprobe |

Los archivos `.insp` e `.insv` se organizan dentro de `Fotos_y_Videos/360/`. Los pares de lentes (`_00_`/`_10_`) se cuentan como un único asset en `duplicates` y `sync`.

---

## FAQ

### ¿Por qué mis fotos van a `unknown_date/`?

El organizador no encontró una fecha confiable (ni EXIF, ni nombre de archivo con patrón de fecha). Puedes renombrar los archivos con una fecha (`YYYYMMDD_*.jpg`) o añadir metadatos EXIF para que se clasifiquen correctamente.

### ¿Los timestamps EXIF tienen zona horaria?

No. El EXIF estándar almacena la hora local sin zona horaria. El organizador la trata como hora local del sistema. Si necesitas cambiar este comportamiento, existe un flag `--assume-tz` pendiente de implementación completa.

### ¿Dónde se guarda el journal?

En `~/.axolo/journal.db`. Cambia la ruta con la variable de entorno `MEDIA_ORGANIZER_JOURNAL`.

### ¿Cómo desactivo el journal?

Usa `--no-journal` en cualquier comando.

### ¿`sync` puede borrar archivos del destino?

No. `sync` es una operación de solo adición (política union). Nunca borra ni sobreescribe archivos en el destino; los conflictos de nombre se resuelven renombrando el archivo nuevo con un sufijo `_<hash8>`.

---

## Pruebas

```bash
# Ejecutar todos los tests
pytest

# Solo un archivo
pytest tests/test_organizer.py -v

# Un test específico
pytest tests/test_organizer.py::test_axolo_resolves_collisions

# Con cobertura
pytest --cov=axolo

# Ignorar tests que requieren archivos reales o ffprobe
pytest --ignore=tests/test_metadata_example_files.py --ignore=tests/test_metadata_insta360.py
```

La cobertura de la suite es ≥ 80% excluyendo los tests que requieren archivos multimedia reales.

# Media Organizer

Automatic organizer for photos, videos, audio and documents with configurable destination templates.

## Características

- Extrae metadatos (EXIF, ID3, PDF, Office) para ordenar archivos multimedia y documentos.
- Clasifica automáticamente en categorías (`Fotos y Videos`, `Musica`, `Documentos`, `Otros`).
- Dentro de cada categoría organiza por año/mes (personalizable mediante plantillas).
- Agrupa fotografías y videos en eventos sugeridos mediante clustering temporal configurable.
- Identifica fotografías potencialmente duplicadas usando hashing perceptual.
- Visualiza la cantidad de capturas por periodo (hora, día, semana, mes o año) sin mover archivos.
- Modo `dry-run` para validar resultados sin mover archivos.
- Soporte para HEIC mediante `pillow-heif` y compatibilidad ampliada con videos (ffprobe y tags DJI).
- Soporte nativo para cámaras 360° (Insta360 X3): formatos `.insp`, `.insv` y `.dng`; archivos 360 se ubican en `Fotos_y_Videos/360/`; los pares de lentes (`_00_`/`_10_`) se agrupan como un solo asset en los reportes.
- Archivos sin fecha confiable se ubican automáticamente en `unknown_date/` dentro de su categoría.
- Empaquetado multiplataforma mediante PyInstaller (scripts incluidos posteriormente).

## Requisitos

- Python 3.10 o superior.
- [FFmpeg](https://ffmpeg.org/) instalado y disponible en el `PATH` para extraer metadatos de video/audio.
- Las dependencias se instalan con `pip install -e .` e incluyen `mutagen` (audio) y `pypdf` (PDF).

## Uso rápido

```bash
python -m venv .venv
source .venv/bin/activate  # En Windows: .venv\Scripts\activate
pip install -e .
media-organizer run --source /ruta/origen --destination /ruta/destino --dry-run
```

### CLI

- `run` organiza físicamente los archivos según la plantilla configurada.
- `cluster` genera agrupaciones sugiriendo álbumes sin mover archivos.
- `similars` detecta fotos parecidas o duplicadas mediante hashing perceptual.
- `timeline` resume cuántas capturas hay por periodo y permite exportar la serie temporal.
- `--profile` permite elegir un template predefinido (`default`, `year_month_day`, `year_month_name`, `camera`).
- `--template` acepta un formato personalizado que se interpreta dentro de la categoría (p. ej. `"{year}/{month_name}"`).
- `--extra clave=valor` agrega variables adicionales para usar en templates (requiere nombrarlas en el template).
- `--dry-run` muestra el plan sin mover archivos.
- Al finalizar, se muestran tablas con el detalle de cada archivo, un resumen por estado y otro por categoría.
- Placeholders adicionales disponibles: `{category}`, `{category_label}`, `{category_slug}`.

Ejemplo:

```bash
media-organizer \
  run \
  --source ~/Media \
  --destination /mnt/organizado \
  --profile year_month_name \
  --dry-run
```

El ejemplo anterior generará rutas como:

- `Fotos_y_Videos/2023/mayo/...`
- `Musica/2020/julio/...`
- `Documentos/2019/12/...`
- `Otros/unknown_date/...`

Puedes añadir perfiles personalizados en un YAML (ver `profiles.sample.yaml`) y cargarlos con `--profiles-path`.

Los archivos que no tengan una fecha de captura confiable se agrupan en `unknown_date/` dentro de su categoría para que puedas revisarlos manualmente.

### Agrupamiento de álbumes

Usa el comando `cluster` para detectar eventos antes de etiquetar o mover archivos. No se modifica ningún archivo; se trabaja únicamente con los metadatos.

```bash
media-organizer cluster \
  --source ~/Media \
  --time-window 120 \
  --min-samples 3 \
  --dry-run
```

- `--time-window` controla la ventana temporal (en minutos) para considerar que dos fotos forman parte del mismo evento (se utiliza DBSCAN).
- `--min-samples` define cuántos elementos mínimos debe tener un clúster.
- `--output clusters.json` guarda el resultado en un archivo JSON (omitido en `--dry-run`).
- `--show-noise` muestra en consola las fotos/vídeos que no han quedado dentro de ningún clúster.

La salida en consola presenta una tabla con los clústeres detectados, el rango temporal, etiquetas sugeridas (por fechas y cámara predominante) y ejemplos de archivos. El JSON generado es ideal para consumirlo desde otras herramientas o para etiquetar posteriormente.

### Fotografías similares

```bash
media-organizer similars \
  --source ~/Media \
  --threshold 5 \
  --hash-size 16 \
  --output similitudes.json
```

- `--threshold` controla la distancia Hamming máxima entre hashes (menor = más estricta).
- `--hash-size` ajusta la sensibilidad del hash perceptual (8–16 suelen funcionar bien).
- `--method` permite elegir entre `phash`, `ahash`, `dhash` o `whash`.
- `--max-pairs` limita los pares mostrados en consola; el JSON siempre contiene el total.

### Línea de tiempo de capturas

```bash
media-organizer timeline \
  --source ~/Media \
  --granularity month \
  --limit 40 \
  --output timeline.csv \
  --chart timeline.html
```

- Las opciones de granularidad disponibles son `hour`, `day`, `week`, `month` y `year`.
- Se pueden exportar los datos en JSON/CSV/TSV y generar un HTML con un gráfico interactivo (requiere acceso a CDN para Chart.js).
- El gráfico y la tabla no modifican archivos originales; únicamente se basan en los metadatos detectados.

## Notas sobre HEIC

El paquete incluye `pillow-heif`. En caso de que la librería no esté disponible en tu entorno, el programa seguirá funcionando, pero los archivos HEIC se procesarán con capacidades reducidas.

## Empaquetado

Se recomiendan herramientas como PyInstaller o Briefcase para generar ejecutables nativos. Las recetas específicas se documentarán una vez integrado el flujo de build.

Ejemplo básico con PyInstaller (desde un entorno virtual):

```bash
pyinstaller --name media-organizer --onefile -p src media_organizer/cli.py
```

## Compatibilidad con cámaras 360 (Insta 360 X3)

Los formatos propietarios de la Insta 360 X3 son reconocidos automáticamente:

| Extensión | Tipo | Metadatos |
|-----------|------|-----------|
| `.insp` | Foto 360 (JPEG con datos 360 al final) | EXIF vía Pillow |
| `.insv` | Video 360 (contenedor MP4) | QuickTime atoms + ffprobe si está disponible |
| `.dng` | RAW (Adobe DNG) | Nombre de archivo como fallback (Pillow no soporta DNG nativamente) |

Los archivos `.insp` e `.insv` se organizan dentro de `Fotos_y_Videos/360/` en lugar de la raíz de esa categoría, manteniendo los archivos 360 separados del resto.

La cámara genera **pares de archivos por lente** para cada captura de video (sufijos `_00_` y `_10_`). Los comandos `cluster`, `similars` y `timeline` los cuentan como un único asset para evitar duplicados en los reportes. El comando `run` mueve o copia ambos archivos físicamente.

## Pruebas

```bash
pytest
```

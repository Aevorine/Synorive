<div align="center">

# Synorive

**Búsqueda semántica local sobre todos tus archivos — y un investigador web verificado.**

Busca tus documentos, código, PDF, imágenes y vídeos **por significado**, no por nombre de archivo.
Después consulta la web abierta en varios motores, busca activamente pruebas en contra y obtén un
informe donde **cada línea es una cita literal con su fuente**.

Funciona completamente sin conexión. Tus archivos nunca salen de tu equipo.
Incluye **24 herramientas MCP** para Claude Code.

[English](../../README.md) · [简体中文](README.zh-CN.md) · [Français](README.fr.md) · **Español** · [Русский](README.ru.md) · [العربية](README.ar.md)

[![Descargar](https://img.shields.io/badge/download-v0.1.4-0F4C8C)](https://github.com/Aevorine/Synorive/releases/latest)
[![Licencia](https://img.shields.io/badge/license-AGPL--3.0-1E9E76)](../../LICENSE)
[![Plataforma](https://img.shields.io/badge/platform-Windows%20%7C%20Android-0F4C8C)](https://github.com/Aevorine/Synorive/releases/latest)
[![Motor](https://img.shields.io/badge/engine-Python%203.13%20%2B%20FastAPI-1E9E76)](../../engine)
[![Escritorio](https://img.shields.io/badge/desktop-Electron%2041%20%2B%20React%2019-0F4C8C)](../../apps/desktop)
[![Sin conexión](https://img.shields.io/badge/offline-100%25-1E9E76)](#decisiones-de-diseño-que-no-son-obvias)
[![MCP](https://img.shields.io/badge/MCP-24%20tools-C8871B)](../../mcp)

### ⬇️ Descarga

| | |
|---|---|
| **Instalador de Windows** | [`Synorive-Setup-0.1.4.exe`](https://github.com/Aevorine/Synorive/releases/latest) — con el runtime de Python incluido, **actualización automática dentro de la app** |
| **Windows portable** | [`Synorive-0.1.4-portable.exe`](https://github.com/Aevorine/Synorive/releases/latest) — sin instalación; esta forma no admite actualización automática |
| **Android** | [`app-release.apk`](https://github.com/Aevorine/Synorive/releases/latest) — cliente ligero, se comunica por red local con el motor de tu PC |

**No hace falta instalar Python.** El intérprete y todas las dependencias del motor viajan dentro
del instalador, así que un equipo recién estrenado sin Python, sin acceso a pip y sin conexión
a Internet arranca igualmente.

</div>

![Banco de investigación de Synorive — búsqueda web multi-motor con clasificación de confianza por fuente](../screenshots/research-light.png)

<div align="center"><sub>

El banco de investigación: búsqueda multi-motor, clasificación de confianza por fuente y un cajón
de resultados excluidos que siempre te dice *por qué* algo se filtró.
Tema oscuro: [captura](../screenshots/research-dark.png)

</sub></div>

---

## Qué hace

| | |
|---|---|
| 🔍 | **Búsqueda semántica sobre tus propios archivos** — documentos, código fuente, PDF (indexados sección por sección), imágenes (OCR), vídeo (con precisión de segundo), páginas web archivadas |
| 🖼 | **Búsqueda intermodal** — encuentra una imagen describiéndola, o averigua *de qué vídeo procede un fotograma y en qué segundo* |
| 🌐 | **Búsqueda web multi-motor** — Bing / Baidu / 360 / Mojeek / Wikipedia, más Google y DuckDuckGo mediante un SearXNG autoalojado |
| 🛡 | **Busca activamente pruebas en contra** — rastrea desmentidos, remonta una afirmación hasta su fuente más antigua, señala artículos retractados |
| 📋 | **Informes solo por extracción** — cada línea es una cita literal con su fuente. Las afirmaciones contradictorias se muestran **una al lado de la otra, sin decidir** |
| 🔌 | **24 herramientas MCP para Claude Code** — deja que tu agente busque en tu biblioteca y verifique afirmaciones por ti |
| 🔒 | **Valla de privacidad** — la búsqueda web y la inferencia en la nube son **dos interruptores separados**, porque una filtra *lo que preguntas* y la otra *lo que tienes* |
| ❓ | **Haz una pregunta, obtén respuestas citadas** — la respuesta se compone *solo* con frases que ya existen en tus archivos, cada una con su fuente. Nada se genera, nada se reformula |
| 📝 | **Borrador en un clic** — elige los resultados que quieras y obtén un borrador en Markdown / texto plano / PDF con citas numeradas y anclas en las que se puede hacer clic |
| ⚡ | **Buscable en segundos** — un archivo nuevo se puede buscar por palabra clave en cuanto se trocea; la indexación semántica se completa en segundo plano en vez de hacerte esperar |
| 🎚 | **Un orden que tú controlas** — ocho deslizadores (semántico, palabra clave, novedad, confianza de la fuente, popularidad, aciertos en el título, diversidad de resultados, penalización de fragmentos cortos), cinco ajustes predefinidos, y puedes guardar los tuyos |
| 📖 | **Comodidad de lectura** — un tema papel, tres escalas de densidad y un área de entrada principal lo bastante grande para una pregunta larga |

**Palabras clave:** búsqueda semántica local · motor de búsqueda con IA sin conexión · RAG multimodal ·
base de conocimiento personal · búsqueda documental · búsqueda vectorial · búsqueda híbrida ·
verificación de hechos · detección de desinformación · servidor MCP · Claude Code · SQLite FTS5 ·
sqlite-vec · HNSW · OCR · búsqueda en vídeo · PLN chino · aplicación de escritorio Electron ·
privacidad primero · autoalojado

---

## ¿Por qué otra herramienta de búsqueda?

La mayoría de las herramientas de «busca en tus archivos» se quedan en la coincidencia de palabras
clave, y la mayoría de las de «investigación con IA» te entregan un resumen fluido que no puedes
verificar. Synorive rechaza ambas cosas:

- **El rendimiento se mide, no se afirma.** Cada cifra de más abajo se midió con datos reales
  —**incluidas las dos que no alcanzaron su objetivo**—. Aparecen en la tabla junto con el motivo,
  en lugar de retirarse discretamente.
- **Nada se descarta en silencio.** Los resultados filtrados por baja calidad van a un cajón de
  «excluidos» con el motivo; un clic los recupera.
- **Nunca te dirá que algo es falso.** Encuentra quién discute una afirmación y te muestra ambas
  partes. Juzgar la verdad no es una capacidad que tenga, y fingir lo contrario sería lo más
  peligroso que podría hacer.

---

## Qué funciona hoy

Las fases 1–3, 5 y 8 están completas. **La aplicación es realmente usable ahora mismo.**

### Buscar en tus propios archivos

- Suelta una carpeta que mezcle documentos, código, imágenes y vídeo → se indexa de forma
  concurrente en segundo plano; la interfaz nunca se congela
- Búsqueda semántica sobre documentos en chino e inglés — describe el contenido, sin necesidad de
  recordar el nombre del archivo
- Sintaxis de consulta directamente en el buscador: `type:pdf date:last7days -borrador "frase exacta"`
- Busca texto *dentro* de las imágenes (OCR, cobertura de caracteres medida al 100 %)
- Encuentra imágenes similares a partir de una imagen, o **de qué vídeo procede un fotograma y en
  qué segundo**
- Busca una línea de diálogo y salta directamente al minuto 3:24 del vídeo
- Artículos indexados por sección (Abstract / Method / Results); los resultados se etiquetan como
  `página 2 · Background`
- Pregúntale a un PDF **«¿qué preguntas puedes responder?»** y haz clic para desplegar el pasaje original

### Buscar en la web y comprobarla

- Varios motores en paralelo (cn.bing / Baidu / 360 / Mojeek / Wikipedia); con un SearXNG
  autoalojado, **Google y DuckDuckGo también funcionan**
- La investigación profunda **lee la primera ronda antes de decidir qué preguntar después**, y
  entonces vuelve a buscar
- Una consulta en chino recibe automáticamente una variante en inglés, enviada a los motores con
  mejor cobertura en ese idioma — las fuentes primarias suelen estar en inglés
- Busca activamente a la inversa «desmentido / cuestionado / retractado / polémica» y te pone las
  pruebas en contra delante
- Remonta una afirmación **hasta su fuente más antigua**; una docena de sitios publicando lo mismo
  en dos días se marca como ráfaga de redifusión
- Un artículo citado **que ha sido retractado se marca en rojo** (vía OpenAlex)
- Cinco fuentes académicas fusionadas por DOI, con número de citas y enlaces al PDF

### Usarlo desde Claude Code

Tras `claude mcp add synorive`, Claude Code puede buscar en tu biblioteca, verificar una afirmación
y comparar **lo que tú tienes** con **lo que dice la web** — en la misma respuesta.

El diseño técnico completo y el menú de 76 funciones están en
[`docs/00-技术方案.md`](../00-技术方案.md). Cada objetivo de rendimiento, cómo cuenta como medido y
**cuáles siguen sin probarse** se declaran en el código, en
[`engine/synorive/metrics.py`](../../engine/synorive/metrics.py); los scripts de medición están en
[`engine/tests/`](../../engine/tests) (`bench_g_series` / `bench_research` / `bench_ingest_stages`).

---

## Mediciones (medidas, no estimadas)

| | Medido | Objetivo |
|---|---|---|
| Arranque en frío hasta poder buscar | **1,30 s** | ≤2,0 s ✅ |
| Primeros resultados @ 102 000 bloques | P50 **45 ms** / P95 **186 ms** | ≤80 / ≤200 ✅ |
| Recuperación completa @ 102 000 bloques | P95 **373 ms** | ≤500 ✅ |
| Fluidez de desplazamiento | **59,9 fps** | ≥55 ✅ |
| Disco para 100 000 bloques | **374 MB** | ≤3 GB ✅ |
| Reanudar tras interrupción | 54/54 omitidos, 1067× más rápido | ✅ |
| Ingesta de imágenes (OCR diferido) | **19,35 imágenes/s** | — |
| OCR de imágenes (pasada en segundo plano) | 1,2–1,5 imágenes/s | ← limitado por el GIL de Python |
| Vídeo, vía rápida | **88,6× tiempo real** | — |
| Vídeo con transcripción | 5,97× tiempo real | ≥6 ⚠️ |
| Vectorización de texto (un worker) | **19,8 bloques/s** (antes 12,6; lote 16→8 dio **1,57×**) | ⚠️ ver abajo |
| Informe de investigación profunda P95 | **8,29 s** (antes 23,79 s; un plazo global lo recortó un **65 %**) | ≤8,0 ⚠️ faltan 0,29 s |
| Acierto en caché caliente | P50 **17,6 ms** | ≤200 ✅ |
| De soltar el archivo a poder buscarlo | P95 **0,8 s** | ≤3,0 ✅ |
| Búsqueda web rápida P95 | **2,4 s** | ≤3,0 ✅ |

⚠️ El rendimiento de ingesta está limitado por esta máquina (i5-1155G7, sin GPU dedicada). El
cronometraje por etapas muestra que **el embedding por sí solo supone el 97,7 %** del coste; las
otras cinco etapas juntas son el 2,3 %. Ir más rápido a partir de aquí exige un modelo cuantizado
o una GPU, no más ajustes.

⚠️ El P95 de investigación profunda bajó de 23,79 s a 8,29 s, pero **el precio fue que 20 de 20
ejecuciones se saltaron la segunda ronda de repregunta**. La cifra y su coste hay que leerlos juntos.

Consulta el campo `how` de A6/A7 en
[`engine/synorive/metrics.py`](../../engine/synorive/metrics.py) — allí la columna de objetivo dice
«⚠️ pendiente de redefinir» en lugar de un número, y es deliberado: un objetivo que todos saben
inalcanzable es peor que admitir que aún no se ha fijado.

---

## Primeros pasos

### Preparación, una sola vez

```bash
# 1. Dependencias de Node (Node ≥20)
npm install

# 2. Generar el subconjunto de fuentes (6 MB, no está en el repo, hay que generarlo una vez)
python scripts/build_fonts.py

# 3. Generar iconos (ya versionados; solo si cambias la imagen de origen)
python scripts/build_icons.py

# 4. Entorno Python del motor (Python ≥3.11)
py -3.13 -m venv engine/.venv
engine/.venv/Scripts/python.exe -m pip install -e engine
```

### Desarrollo

```bash
npm run dev              # Electron + Vite con HMR; el motor se arranca solo
```

### Compilación

```bash
npm run build            # todos los workspaces
npm run build:desktop    # solo escritorio
npm run pack:win         # instalador de Windows + portable
```

### Publicación y actualización automática

Escritorio y Android comprueban actualizaciones contra las **GitHub Releases** de este repositorio.

```bash
npm run version:check      # ¿están sincronizados los cuatro números de versión?
npm run version:set 0.1.4  # cambia los cuatro de una vez — nunca los edites a mano
npm run android:keystore   # solo la primera vez: generar el keystore de Android (fuera del repo)
npm run release            # compilar ambos artefactos, SIN subirlos
npm run release:publish    # compilar y crear una GitHub Release (requiere gh autenticado)
```

Hay cuatro maneras de romper la cadena de actualización **sin que aparezca error alguno**.
`scripts/release.mjs` bloquea cada una:

| Qué falta | Qué ve el usuario |
|---|---|
| No se subió `latest.yml` | El escritorio dice **«ya estás al día»**, no un error — la actualización nunca llega |
| El tag no coincide con `package.json` | El actualizador devuelve 404 |
| No se subió el APK | El móvil ve la nueva versión pero no puede descargarla |
| No se incrementó el `versionCode` de Android | El móvil dice **«ya estás al día»** |

**Límites de seguridad del canal de actualización** — dichos con claridad en lugar de esquivados:

| | Escritorio | Android |
|---|---|---|
| Transporte | HTTPS | HTTPS, y el código rechaza tajantemente cualquier host que no sea GitHub |
| Integridad | sha512 de `latest.yml`; si no coincide, se rechaza la instalación | El recuento de bytes debe igualar el tamaño que declara GitHub (un servidor que corta antes también devuelve −1 en `read()`, así que sin comprobar la longitud te quedas con un paquete truncado) |
| Autenticidad | ⚠️ **Sin firma de código.** No se compró certificado, así que la verificación Authenticode se omite y SmartScreen avisará de un editor desconocido | ✅ El sistema verifica la firma; un APK firmado con la clave equivocada sencillamente no se instala |

Cerrar la brecha del escritorio tiene exactamente un camino: comprar un certificado de firma de
código y definir `publisherName` en `electron-builder.yml`.
**Hasta entonces, no lo presentes como «actualizaciones seguras».**

Dos limitaciones conocidas, ambas por diseño y no errores:

- **El exe portable no puede autoactualizarse.** Un ejecutable autoextraíble de un solo archivo se
  ejecuta desde una carpeta temporal y no puede reemplazar la copia de sí mismo que está corriendo.
  La aplicación lo dice explícitamente y enlaza a la página de descargas, en lugar de dar un error
  y hacerte reintentar.
- **Android exige confirmación manual de instalación.** La distribución fuera de una tienda solo
  puede invocar el instalador del sistema, que la primera vez pide «permitir aplicaciones
  desconocidas». La aplicación detecta ese permiso y te lleva directamente a esa pantalla.

### Ejecutar el motor por separado (depuración, CLI, MCP)

```bash
engine/.venv/Scripts/python.exe -m synorive.main --port 8731 --data-dir ./data
# Documentación de la API en http://127.0.0.1:8731/docs
```

### Conectar con Claude Code

```bash
npm run build --workspace=@aevorine/synorive-mcp
node scripts/install-claude-integration.mjs
```

Después abre una sesión nueva de Claude Code y pregunta «¿guardé algo sobre X?» — la búsqueda se
dispara automáticamente.

**Las 24 herramientas:**

- **Biblioteca local** — `search` / `ingest` / `analyze` / `get_content` / `similar` / `timeline` /
  `graph` / `status` / `questions`
- **Web** — `web_search` / `research` / `scholar` / `read_url` / `web_engines` / `verify` /
  `unified_search`
- **Literatura** — `scholar_review` (revisión temática, solo extracción), `scholar_table` (una misma
  métrica en varios artículos), `citations` (co-citación para hallar los artículos fundacionales),
  `harvest` (descarga masiva de texto completo en acceso abierto, simulación por defecto)
- **Verificación y memoria** — `check_numbers` (contrastar cada cifra con el texto original),
  `memory` (¿qué consulté ya sobre este tema?)
- **Medios locales** — `compare` (en qué se diferencian dos archivos), `chapters` (índice de
  capítulos de un vídeo largo)

Todo lo que se devuelve a Claude **lleva un desglose de confianza y una fuente**, y las descripciones
de las herramientas declaran los límites de lo que pueden hacer («no puede juzgar si una afirmación
es objetivamente cierta», «la extracción literal no es una paráfrasis», «que existan pruebas en
contra ≠ la afirmación original es falsa»). Sin eso, Claude trataría una granja de contenidos y una
especificación oficial como igual de fiables, y transmitiría ambas con la misma seguridad.

La dirección del motor se descubre automáticamente desde `data/engine.json`: si la app de escritorio
está abierta, el servidor MCP se conecta al mismo motor; si no, arranca el suyo. `SYNORIVE_ENGINE_URL`
permite forzarla.

### Hacer que Google y DuckDuckGo funcionen (opcional, muy recomendable)

Medido en agosto de 2026: Google ya exige JavaScript (con HTTP puro solo se obtiene una página de
redirección), el endpoint html de DuckDuckGo pasó a ser una landing en JS, Yandex sirve un captcha,
y **las siete instancias públicas de SearXNG devolvieron 429/403**. En la práctica queda exactamente
un camino gratuito hacia esos motores: **ejecutar tu propio SearXNG.**

```bash
node scripts/setup-searxng.mjs            # muestra lo que piensa hacer (simulación, no toca nada)
node scripts/setup-searxng.mjs --apply    # instalarlo de verdad (necesita Docker)
node scripts/setup-searxng.mjs --status   # ¿sigue vivo?
```

El motor **lo descubre y lo activa en el arranque en frío** — ningún ajuste que buscar. Medido tras
instalarlo: `google cse` aportó 20 resultados por sí solo y DuckDuckGo 10, mientras que ambos
devuelven cero al consultarlos directamente.

---

## Decisiones de diseño que no son obvias

- **La búsqueda web y la inferencia en la nube son dos interruptores, no uno.** La búsqueda web
  filtra *lo que estás preguntando*; la inferencia en la nube filtra *lo que ya tienes*. Son riesgos
  distintos, así que fundirlos en un único «modo privado» te dejaría apagar el que te importaba
  mientras el otro sigue encendido.
- **La ventana flotante de imagen del portapapeles nunca sale a la red** — ni siquiera con la
  previsualización web activada. Enviar texto es una frase; enviar una captura es una imagen que
  puede contener cualquier cosa.
- **Los resultados filtrados se pliegan, no se borran.** El mismo artículo hallado por cinco motores
  y republicado por tres sitios son ocho resultados pero una sola cosa. Plegarlo y anotar
  «5 motores, 3 sitios» conserva el número que necesita la verificación cruzada; borrarlo lo tira.
- **Limitado por tasa ≠ roto.** Un motor que devuelve un captcha no es un analizador roto. Se
  cuentan por separado, porque «ve más despacio» y «este adaptador está muerto» exigen respuestas
  opuestas.
- **Las mediciones que no llegaron a su objetivo siguen en la tabla.** Dos filas más arriba llevan
  un ⚠️ en vez de haber sido eliminadas.

---

## Licencia

[AGPL-3.0-or-later](../../LICENSE). Si ejecutas una versión modificada como servicio en red, debes
publicar tus modificaciones.

<div align="center">

# Synorive

**Búsqueda multimodal local sobre tus propios archivos — y un investigador web que busca activamente pruebas en contra.**

Busca tus documentos, código, imágenes, vídeos y archivos web **por significado**, no por
nombre de archivo. Después consulta la web abierta en varios motores, contrasta cada afirmación
y obtén un informe donde **cada frase es una cita literal de una fuente real**.

Funciona completamente sin conexión. Tus archivos nunca salen de tu equipo.
Expone 24 herramientas a **Claude Code** mediante MCP.

[English](../../README.md) · [简体中文](README.zh-CN.md) · [Français](README.fr.md) · **Español** · [Русский](README.ru.md) · [العربية](README.ar.md)

[![Descargar](https://img.shields.io/badge/descargar-v0.1.1-0F4C8C)](https://github.com/Aevorine/Synorive/releases/latest)
[![Licencia](https://img.shields.io/badge/licencia-AGPL--3.0-1E9E76)](../../LICENSE)
[![Plataforma](https://img.shields.io/badge/plataforma-Windows%20%7C%20Android-0F4C8C)](https://github.com/Aevorine/Synorive/releases/latest)

</div>

![Synorive](../screenshots/research-light.png)

---

## Qué hace

| | |
|---|---|
| 🔍 | **Búsqueda semántica sobre tus archivos** — documentos, código fuente, PDF (divididos por sección), imágenes (OCR), vídeo (con precisión de segundos), archivos web |
| 🖼 | **Búsqueda intermodal**: encuentra una imagen describiéndola, o averigua *de qué vídeo procede un fotograma y en qué segundo* |
| 🌐 | **Búsqueda web multimotor** — Bing / Baidu / 360 / Mojeek / Wikipedia, y además Google y DuckDuckGo mediante una instancia SearXNG propia |
| 🛡 | **Busca activamente pruebas en contra** — rastrea desmentidos, remonta una afirmación a su fuente más antigua, señala artículos retractados |
| 📋 | **Informes solo de extractos** — cada línea es una cita literal con su fuente. Las afirmaciones contradictorias se muestran **una junto a otra, sin decidir por ti** |
| 🔌 | **24 herramientas MCP para Claude Code** — tu agente consulta tu biblioteca y verifica afirmaciones por ti |
| 🔒 | **Barrera de privacidad** — la búsqueda web y la inferencia en la nube son **dos** interruptores distintos: uno revela *qué preguntas*, el otro *qué tienes* |

---

## Por qué otra herramienta de búsqueda

La mayoría de las herramientas de «busca en tus archivos» se quedan en la coincidencia de palabras
clave, y la mayoría de las de «investigación con IA» te entregan un resumen fluido que no puedes
verificar. Synorive rechaza ambas cosas:

- **El rendimiento se mide, no se proclama.** Cada cifra de este README se midió con datos reales,
  **incluidas las dos que no alcanzaron su objetivo**: aparecen con su motivo en lugar de
  eliminarse discretamente.
- **Nada se descarta en silencio.** Los resultados filtrados por baja calidad van a un cajón de
  «excluidos» con el motivo, recuperables con un clic.
- **Nunca te dice que algo es falso.** Encuentra quién cuestiona una afirmación y te muestra ambas
  partes: juzgar la verdad no es una capacidad que tenga, y fingir lo contrario sería lo más
  peligroso que podría hacer.

---

## Instalación

**Windows** — desde [Releases](https://github.com/Aevorine/Synorive/releases/latest):

- `Synorive-Setup-0.1.1.exe` — instalador, **con actualización automática integrada**
- `Synorive-0.1.1-portable.exe` — versión portátil (sin actualización automática: sustituye el archivo a mano)

> El entorno de Python va incluido en el instalador.
> **No necesitas instalar Python previamente.**

**Android** — descarga `app-release.apk` y permite la instalación desde orígenes desconocidos.
La app móvil es un cliente ligero: se conecta al motor que corre en tu PC dentro de la misma red local.

---

## Desde el código fuente

```bash
npm install                                   # Node ≥20
python scripts/build_fonts.py                 # subconjuntos de fuentes (6 MB, fuera del repo)
python -m venv engine/.venv                   # Python ≥3.11
engine/.venv/Scripts/pip install -e "engine[docs,media,sync,ann]"
npm run dev                                   # Electron + Vite; el motor arranca solo
```

Solo comprobaciones de compilación: `npm run typecheck` · `node scripts/check-hardcoded-style.mjs`

---

## Conectar con Claude Code

```bash
claude mcp add synorive -- node <ruta-del-repo>/mcp/dist/index.js
```

Después basta con pedir: «busca “por qué la búsqueda vectorial es lenta” en mi biblioteca y
contrasta lo que encuentres».

---

## Algunas decisiones de diseño no evidentes

- **Recuperación en cascada de tres niveles** (palabras clave → semántica → reordenación):
  la primera pantalla aparece de inmediato y los niveles lentos reordenan después. Nunca esperas.
- **El chino exige segmentación previa**: sin jieba, las palabras de dos caracteres como «搜索»
  tienen una tasa de acierto de cero.
- **El informe de extractos y el generado están separados**: citas literales a la izquierda,
  texto generado por IA a la derecha, comparables en todo momento. Una frase generada nunca
  se disfraza de cita.
- **Búsqueda web e inferencia en la nube son dos interruptores**: mucha gente acepta uno y
  rechaza rotundamente el otro; unificarlos sería obligar a elegir.

---

## Licencia

[AGPL-3.0-or-later](../../LICENSE). Documentación completa (mediciones reales, estructura de
directorios, limitaciones conocidas) en el [README en inglés](../../README.md).

<div align="center">

# Synorive

**Recherche multimodale locale sur vos propres fichiers — et un agent de recherche web qui cherche activement les contre-preuves.**

Cherchez vos documents, votre code, vos images, vos vidéos et vos archives web **par le sens**,
pas par nom de fichier. Puis interrogez le web ouvert sur plusieurs moteurs, recoupez chaque
affirmation et obtenez une synthèse où **chaque phrase est une citation littérale d'une source réelle**.

Fonctionne entièrement hors ligne. Vos fichiers ne quittent jamais votre machine.
Expose 24 outils à **Claude Code** via MCP.

[English](../../README.md) · [简体中文](README.zh-CN.md) · **Français** · [Español](README.es.md) · [Русский](README.ru.md) · [العربية](README.ar.md)

[![Télécharger](https://img.shields.io/badge/télécharger-v0.1.1-0F4C8C)](https://github.com/Aevorine/Synorive/releases/latest)
[![Licence](https://img.shields.io/badge/licence-AGPL--3.0-1E9E76)](../../LICENSE)
[![Plateforme](https://img.shields.io/badge/plateforme-Windows%20%7C%20Android-0F4C8C)](https://github.com/Aevorine/Synorive/releases/latest)

</div>

![Synorive](../screenshots/research-light.png)

---

## Ce que ça fait

| | |
|---|---|
| 🔍 | **Recherche sémantique sur vos fichiers** — documents, code source, PDF (découpés par section), images (OCR), vidéo (à la seconde près), archives web |
| 🖼 | **Recherche intermodale** : retrouvez une image en la décrivant, ou déterminez *de quelle vidéo provient une image et à quelle seconde* |
| 🌐 | **Recherche web multi-moteurs** — Bing / Baidu / 360 / Mojeek / Wikipédia, plus Google et DuckDuckGo via une instance SearXNG auto-hébergée |
| 🛡 | **Cherche activement les contre-preuves** — recherche les démentis, remonte une affirmation à sa source la plus ancienne, signale les articles rétractés |
| 📋 | **Synthèses par extraits uniquement** — chaque ligne est une citation littérale avec sa source. Les affirmations contradictoires sont présentées **côte à côte, sans trancher** |
| 🔌 | **24 outils MCP pour Claude Code** — votre agent interroge votre bibliothèque et vérifie les affirmations à votre place |
| 🔒 | **Barrière de confidentialité** — la recherche web et l'inférence cloud sont **deux** interrupteurs distincts : l'un révèle *ce que vous demandez*, l'autre *ce que vous possédez* |

---

## Pourquoi un outil de plus

La plupart des outils « cherchez dans vos fichiers » s'arrêtent aux mots-clés, et la plupart
des outils « recherche IA » vous livrent un résumé fluide mais invérifiable. Synorive refuse les deux :

- **Les performances sont mesurées, pas proclamées.** Chaque chiffre du README a été mesuré
  sur des données réelles, **y compris les deux qui n'ont pas atteint leur objectif** — ils
  figurent avec leur explication au lieu d'être discrètement retirés.
- **Rien n'est écarté en silence.** Les résultats filtrés comme peu fiables vont dans un tiroir
  « exclus » avec le motif, récupérables en un clic.
- **Il ne vous dit jamais qu'une chose est fausse.** Il trouve qui conteste une affirmation et
  vous montre les deux versions — juger du vrai n'est pas une capacité qu'il possède, et
  prétendre le contraire serait la chose la plus dangereuse qu'il puisse faire.

---

## Installation

**Windows** — depuis les [Releases](https://github.com/Aevorine/Synorive/releases/latest) :

- `Synorive-Setup-0.1.1.exe` — programme d'installation, **mise à jour automatique intégrée**
- `Synorive-0.1.1-portable.exe` — version portable (pas de mise à jour automatique : remplacez le fichier manuellement)

> L'environnement Python est inclus dans le programme d'installation.
> **Aucune installation préalable de Python n'est nécessaire.**

**Android** — téléchargez `app-release.apk` et autorisez l'installation depuis des sources inconnues.
L'application mobile est un client léger : elle se connecte au moteur qui tourne sur votre PC,
sur le même réseau local.

---

## Depuis les sources

```bash
npm install                                   # Node ≥20
python scripts/build_fonts.py                 # sous-ensembles de polices (6 Mo, hors dépôt)
python -m venv engine/.venv                   # Python ≥3.11
engine/.venv/Scripts/pip install -e "engine[docs,media,sync,ann]"
npm run dev                                   # Electron + Vite ; le moteur démarre tout seul
```

Vérifications de compilation uniquement : `npm run typecheck` · `node scripts/check-hardcoded-style.mjs`

---

## Brancher sur Claude Code

```bash
claude mcp add synorive -- node <chemin-du-dépôt>/mcp/dist/index.js
```

Ensuite, demandez simplement : « cherche “pourquoi la recherche vectorielle est lente” dans ma
bibliothèque et recoupe ce que tu trouves ».

---

## Quelques choix de conception non évidents

- **Récupération en cascade à trois niveaux** (mots-clés → sémantique → reclassement) : le premier
  écran s'affiche immédiatement, les niveaux plus lents réordonnent ensuite. Vous n'attendez jamais.
- **Le chinois exige une segmentation préalable** : sans jieba, les mots de deux caractères
  comme « 搜索 » ont un taux de rappel de zéro.
- **Synthèse par extraits et synthèse générée sont séparées** : citations littérales à gauche,
  texte généré par IA à droite — comparables à tout moment. Une phrase générée n'est jamais
  déguisée en citation.
- **Recherche web et inférence cloud sont deux interrupteurs** : beaucoup acceptent l'un et
  refusent absolument l'autre ; les fusionner reviendrait à imposer un choix binaire.

---

## Licence

[AGPL-3.0-or-later](../../LICENSE). Documentation complète (mesures réelles, arborescence,
limitations connues) dans le [README anglais](../../README.md).

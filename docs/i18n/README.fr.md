<div align="center">

# Synorive

**Recherche sémantique locale sur tous vos fichiers — et un agent de recherche web vérifié.**

Cherchez vos documents, votre code, vos PDF, vos images et vos vidéos **par le sens**,
pas par le nom de fichier. Puis interrogez le web ouvert sur plusieurs moteurs, cherchez
activement les contre-preuves, et obtenez une synthèse où **chaque ligne est une citation
mot pour mot avec sa source**.

Fonctionne entièrement hors ligne. Vos fichiers ne quittent jamais votre machine.
Fournit **24 outils MCP** pour Claude Code.

[English](../../README.md) · [简体中文](README.zh-CN.md) · **Français** · [Español](README.es.md) · [Русский](README.ru.md) · [العربية](README.ar.md)

[![Télécharger](https://img.shields.io/badge/download-v0.1.5-0F4C8C)](https://github.com/Aevorine/Synorive/releases/latest)
[![Licence](https://img.shields.io/badge/license-AGPL--3.0-1E9E76)](../../LICENSE)
[![Plateforme](https://img.shields.io/badge/platform-Windows%20%7C%20Android-0F4C8C)](https://github.com/Aevorine/Synorive/releases/latest)
[![Moteur](https://img.shields.io/badge/engine-Python%203.13%20%2B%20FastAPI-1E9E76)](../../engine)
[![Bureau](https://img.shields.io/badge/desktop-Electron%2041%20%2B%20React%2019-0F4C8C)](../../apps/desktop)
[![Hors ligne](https://img.shields.io/badge/offline-100%25-1E9E76)](#décisions-de-conception-non-évidentes)
[![MCP](https://img.shields.io/badge/MCP-24%20tools-C8871B)](../../mcp)

### ⬇️ Téléchargement

| | |
|---|---|
| **Installateur Windows** | [`Synorive-Setup-0.1.5.exe`](https://github.com/Aevorine/Synorive/releases/latest) — runtime Python inclus, **mise à jour automatique dans l'application** |
| **Windows portable** | [`Synorive-0.1.5-portable.exe`](https://github.com/Aevorine/Synorive/releases/latest) — sans installation ; la mise à jour automatique n'est pas disponible dans cette forme |
| **Android** | [`app-release.apk`](https://github.com/Aevorine/Synorive/releases/latest) — client léger, dialogue avec le moteur de votre PC via le réseau local |

**Aucune installation de Python requise.** L'interpréteur et toutes les dépendances du moteur
sont livrés dans l'installateur : une machine neuve sans Python, sans accès à pip et sans
connexion Internet démarre quand même.

</div>

![Atelier de recherche Synorive — recherche web multi-moteurs avec classement de confiance par source](../screenshots/research-light.png)

<div align="center"><sub>

L'atelier de recherche : recherche multi-moteurs, classement de confiance par source, et un tiroir
de résultats exclus qui vous dit toujours *pourquoi* quelque chose a été filtré.
Thème sombre : [capture d'écran](../screenshots/research-dark.png)

</sub></div>

---

## Ce qu'il fait

| | |
|---|---|
| 🔍 | **Recherche sémantique sur vos propres fichiers** — documents, code source, PDF (indexés section par section), images (OCR), vidéo (à la seconde près), pages web archivées |
| 🖼 | **Recherche intermodale** — retrouvez une image en la décrivant, ou trouvez *de quelle vidéo provient une image et à quelle seconde* |
| 🌐 | **Recherche web multi-moteurs** — Bing / Baidu / 360 / Mojeek / Wikipédia, plus Google et DuckDuckGo via un SearXNG auto-hébergé |
| 🛡 | **Cherche activement les contre-preuves** — recherche les démentis, remonte une affirmation à sa source la plus ancienne, signale les articles rétractés |
| 📋 | **Synthèses par extraction uniquement** — chaque ligne est une citation littérale avec sa source. Les affirmations contradictoires sont présentées **côte à côte, sans trancher** |
| 🔌 | **24 outils MCP pour Claude Code** — laissez votre agent fouiller votre bibliothèque et vérifier des affirmations à votre place |
| 🔒 | **Barrière de confidentialité** — la recherche web et l'inférence dans le cloud sont **deux interrupteurs séparés**, car l'une révèle *ce que vous demandez* et l'autre *ce que vous possédez* |
| ❓ | **Posez une question, obtenez des réponses citées** — la réponse est assemblée *uniquement* à partir de phrases qui existent déjà dans vos fichiers, chacune avec sa source. Rien n'est généré, rien n'est reformulé |
| 📝 | **Brouillon en un clic** — choisissez les résultats voulus, obtenez un brouillon Markdown / texte brut / PDF avec des citations numérotées et des ancres cliquables |
| ⚡ | **Trouvable en quelques secondes** — un nouveau fichier est cherchable par mot-clé dès qu'il est découpé ; l'indexation sémantique se complète en arrière-plan au lieu de vous faire attendre |
| 🎚 | **Un classement que vous contrôlez** — huit curseurs (sémantique, mot-clé, fraîcheur, confiance de la source, popularité, titre, diversité des résultats, pénalité des fragments courts), cinq préréglages, et vous pouvez enregistrer les vôtres |
| 📖 | **Confort de lecture** — un thème papier, trois échelles de densité, et une zone de saisie principale assez grande pour une longue question |

**Mots-clés :** recherche sémantique locale · moteur de recherche IA hors ligne · RAG multimodal ·
base de connaissances personnelle · recherche documentaire · recherche vectorielle · recherche hybride ·
vérification des faits · détection de désinformation · serveur MCP · Claude Code · SQLite FTS5 ·
sqlite-vec · HNSW · OCR · recherche vidéo · NLP chinois · application de bureau Electron ·
confidentialité d'abord · auto-hébergé

---

## Pourquoi un outil de recherche de plus ?

La plupart des outils « cherchez dans vos fichiers » s'arrêtent à la correspondance de mots-clés,
et la plupart des outils de « recherche assistée par IA » vous tendent un résumé fluide que vous
ne pouvez pas vérifier. Synorive refuse les deux :

- **Les performances sont mesurées, pas affirmées.** Chaque chiffre ci-dessous a été mesuré sur
  des données réelles — **y compris les deux qui n'ont pas atteint leur objectif**. Ils figurent
  dans le tableau avec la raison, plutôt que d'être discrètement retirés.
- **Rien n'est écarté en silence.** Les résultats filtrés comme peu fiables vont dans un tiroir
  « exclus » avec la raison ; un clic suffit à les récupérer.
- **Il ne vous dira jamais qu'une chose est fausse.** Il trouve qui conteste une affirmation et
  vous montre les deux camps. Juger de la vérité n'est pas une capacité qu'il possède, et
  prétendre le contraire serait la chose la plus dangereuse qu'il puisse faire.

---

## Ce qui fonctionne aujourd'hui

Les phases 1 à 3, 5 et 8 sont terminées. **L'application est réellement utilisable dès maintenant.**

### Chercher dans vos propres fichiers

- Déposez un dossier mêlant documents, code, images et vidéos → indexation concurrente en
  arrière-plan, l'interface ne se fige jamais
- Recherche sémantique sur des documents en chinois et en anglais — décrivez le contenu, inutile
  de vous souvenir du nom de fichier
- Syntaxe de requête directement dans le champ : `type:pdf date:last7days -brouillon "phrase exacte"`
- Cherchez le texte *à l'intérieur* des images (OCR, couverture de caractères mesurée à 100 %)
- Trouvez des images similaires à partir d'une image, ou **de quelle vidéo provient une image et
  à quelle seconde**
- Cherchez une réplique parlée et sautez directement à 3 min 24 s de la vidéo
- Articles indexés par section (Abstract / Method / Results) ; les résultats sont étiquetés
  `page 2 · Background`
- Demandez à un PDF **« à quelles questions peux-tu répondre ? »** et cliquez pour déplier le
  passage source

### Chercher sur le web et le vérifier

- Plusieurs moteurs en parallèle (cn.bing / Baidu / 360 / Mojeek / Wikipédia) ; avec un SearXNG
  auto-hébergé, **Google et DuckDuckGo fonctionnent aussi**
- La recherche approfondie **lit le premier tour avant de décider quoi demander ensuite**, puis
  relance une recherche
- Une requête en chinois reçoit automatiquement une variante anglaise, envoyée aux moteurs à
  meilleure couverture anglophone — les sources primaires sont le plus souvent en anglais
- Recherche activement à rebours « démenti / contesté / rétracté / controverse » et met les
  contre-preuves sous vos yeux
- Remonte une affirmation **jusqu'à sa source la plus ancienne** ; une douzaine de sites publiant
  la même histoire en deux jours est signalée comme une reprise en cascade
- Un article cité **qui a été rétracté est signalé en rouge** (via OpenAlex)
- Cinq sources académiques fusionnées par DOI, avec nombre de citations et liens PDF

### L'utiliser depuis Claude Code

Après `claude mcp add synorive`, Claude Code peut fouiller votre bibliothèque, vérifier une
affirmation, et comparer **ce que vous possédez** à **ce que dit le web** — dans la même réponse.

La conception technique complète et le menu de 76 fonctionnalités se trouvent dans
[`docs/00-技术方案.md`](../00-技术方案.md). Chaque objectif de performance, la manière dont il est
considéré comme mesuré, et **ceux qui ne sont pas encore testés**, sont déclarés dans le code à
[`engine/synorive/metrics.py`](../../engine/synorive/metrics.py) ; les scripts de mesure sont dans
[`engine/tests/`](../../engine/tests) (`bench_g_series` / `bench_research` / `bench_ingest_stages`).

---

## Mesures (mesurées, pas estimées)

| | Mesuré | Objectif |
|---|---|---|
| Démarrage à froid jusqu'à recherche possible | **1,30 s** | ≤2,0 s ✅ |
| Premiers résultats @ 102 000 blocs | P50 **45 ms** / P95 **186 ms** | ≤80 / ≤200 ✅ |
| Recherche complète @ 102 000 blocs | P95 **373 ms** | ≤500 ✅ |
| Fluidité du défilement | **59,9 fps** | ≥55 ✅ |
| Disque pour 100 000 blocs | **374 Mo** | ≤3 Go ✅ |
| Reprise après interruption | 54/54 ignorés, 1067× plus rapide | ✅ |
| Ingestion d'images (OCR différé) | **19,35 images/s** | — |
| OCR d'images (passe en arrière-plan) | 1,2–1,5 image/s | ← limité par le GIL de Python |
| Vidéo, voie rapide | **88,6× temps réel** | — |
| Vidéo avec transcription | 5,97× temps réel | ≥6 ⚠️ |
| Vectorisation de texte (un worker) | **19,8 blocs/s** (12,6 auparavant ; lot 16→8 = **1,57×**) | ⚠️ voir ci-dessous |
| Synthèse de recherche approfondie P95 | **8,29 s** (23,79 s auparavant ; une échéance globale a réduit de **65 %**) | ≤8,0 ⚠️ manque 0,29 s |
| Cache chaud | P50 **17,6 ms** | ≤200 ✅ |
| Du dépôt à la recherche | P95 **0,8 s** | ≤3,0 ✅ |
| Recherche web rapide P95 | **2,4 s** | ≤3,0 ✅ |

⚠️ Le débit d'ingestion est limité par cette machine (i5-1155G7, sans GPU dédié). Le chronométrage
étape par étape montre que **l'embedding représente à lui seul 97,7 %** du coût ; les cinq autres
étapes réunies font 2,3 %. Aller plus vite implique un modèle quantifié ou un GPU, pas davantage
de réglages.

⚠️ Le P95 de la recherche approfondie est passé de 23,79 s à 8,29 s, mais **le prix a été que
20 exécutions sur 20 ont sauté le second tour de relance**. Le chiffre et son coût doivent se lire
ensemble.

Voir le champ `how` de A6/A7 dans
[`engine/synorive/metrics.py`](../../engine/synorive/metrics.py) — la colonne objectif y indique
« ⚠️ à redéfinir » plutôt qu'un nombre, et c'est délibéré : un objectif que tout le monde sait
inatteignable est pire que d'admettre qu'il n'a pas été fixé.

---

## Démarrage

### Préparation, une seule fois

```bash
# 1. Dépendances Node (Node ≥20)
npm install

# 2. Générer le sous-ensemble de polices (6 Mo, absent du dépôt, à générer une fois)
python scripts/build_fonts.py

# 3. Générer les icônes (déjà versionnées ; utile seulement si vous changez l'image source)
python scripts/build_icons.py

# 4. Environnement Python du moteur (Python ≥3.11)
py -3.13 -m venv engine/.venv
engine/.venv/Scripts/python.exe -m pip install -e engine
```

### Développement

```bash
npm run dev              # Electron + Vite avec HMR ; le moteur se lance tout seul
```

### Construction

```bash
npm run build            # tous les espaces de travail
npm run build:desktop    # bureau uniquement
npm run pack:win         # installateur Windows + portable
```

### Publication et mise à jour automatique

Le bureau et Android vérifient tous deux les mises à jour via les **GitHub Releases** de ce dépôt.

```bash
npm run version:check      # les quatre numéros de version sont-ils cohérents ?
npm run version:set 0.1.5  # change les quatre d'un coup — ne les modifiez jamais à la main
npm run android:keystore   # première fois : générer le keystore Android (conservé hors du dépôt)
npm run release            # construire les deux artefacts, SANS envoi
npm run release:publish    # construire et créer une GitHub Release (gh doit être connecté)
```

Il existe quatre façons de casser la chaîne de mise à jour **sans la moindre erreur visible**.
`scripts/release.mjs` bloque chacune :

| Ce qui manque | Ce que voit l'utilisateur |
|---|---|
| `latest.yml` non envoyé | Le bureau affiche **« vous êtes à jour »**, pas une erreur — la mise à jour n'arrive jamais |
| Le tag ne correspond pas à `package.json` | L'updater renvoie 404 |
| APK non envoyé | Le téléphone voit la nouvelle version mais ne peut pas la télécharger |
| `versionCode` Android non incrémenté | Le téléphone affiche **« vous êtes à jour »** |

**Limites de sécurité du canal de mise à jour** — énoncées franchement plutôt qu'esquivées :

| | Bureau | Android |
|---|---|---|
| Transport | HTTPS | HTTPS, et le code rejette catégoriquement tout hôte non-GitHub |
| Intégrité | sha512 issu de `latest.yml` ; en cas d'écart, l'installation est refusée | Le nombre d'octets doit égaler la taille annoncée par GitHub (un serveur qui coupe tôt renvoie aussi −1 depuis `read()`, donc sans contrôle de longueur on obtient un paquet tronqué) |
| Authenticité | ⚠️ **Pas de signature de code.** Aucun certificat n'a été acheté, la vérification Authenticode est donc ignorée et SmartScreen avertira d'un éditeur inconnu | ✅ Le système vérifie la signature ; un APK signé avec la mauvaise clé ne s'installe tout simplement pas |

Combler l'écart côté bureau n'a qu'une seule voie : acheter un certificat de signature de code
et renseigner `publisherName` dans `electron-builder.yml`.
**En attendant, ne présentez pas cela comme des « mises à jour sécurisées ».**

Deux limitations connues, toutes deux voulues plutôt que des bogues :

- **L'exe portable ne peut pas se mettre à jour tout seul.** Un exécutable auto-extractible mono-fichier
  tourne depuis un dossier temporaire et ne peut pas remplacer la copie de lui-même en cours
  d'exécution. L'application le dit explicitement et renvoie vers la page de téléchargement,
  au lieu d'afficher une erreur et de vous faire réessayer.
- **Android exige une confirmation manuelle d'installation.** Une distribution hors magasin ne peut
  qu'invoquer l'installateur système, qui demande la première fois d'« autoriser les applications
  inconnues ». L'application détecte cette permission et vous emmène directement au bon écran.

### Lancer le moteur seul (débogage, CLI, MCP)

```bash
engine/.venv/Scripts/python.exe -m synorive.main --port 8731 --data-dir ./data
# Documentation de l'API : http://127.0.0.1:8731/docs
```

### Connexion à Claude Code

```bash
npm run build --workspace=@aevorine/synorive-mcp
node scripts/install-claude-integration.mjs
```

Ouvrez ensuite une nouvelle session Claude Code et demandez « ai-je enregistré quelque chose à
propos de X ? » — la recherche se déclenche automatiquement.

**Les 24 outils :**

- **Bibliothèque locale** — `search` / `ingest` / `analyze` / `get_content` / `similar` /
  `timeline` / `graph` / `status` / `questions`
- **Web** — `web_search` / `research` / `scholar` / `read_url` / `web_engines` / `verify` /
  `unified_search`
- **Littérature** — `scholar_review` (revue thématique, extraction seule), `scholar_table`
  (une même mesure sur plusieurs articles), `citations` (co-citation pour trouver les articles
  fondateurs), `harvest` (récupération en masse du texte intégral en accès ouvert, simulation par défaut)
- **Vérification et mémoire** — `check_numbers` (recontrôler chaque chiffre dans le texte source),
  `memory` (qu'ai-je déjà cherché sur ce sujet ?)
- **Médias locaux** — `compare` (ce qui diffère entre deux fichiers), `chapters` (sommaire d'une
  longue vidéo)

Tout ce qui est renvoyé à Claude **porte un détail de confiance et une source**, et les descriptions
d'outils énoncent les limites de ce que l'outil peut faire (« ne peut pas juger si une affirmation
est factuellement vraie », « une extraction littérale n'est pas une reformulation »,
« l'existence de contre-preuves ≠ l'affirmation d'origine est fausse »). Sans cela, Claude traiterait
une ferme à contenu et une spécification officielle comme aussi fiables l'une que l'autre, et
relaierait les deux avec la même assurance.

L'adresse du moteur est découverte automatiquement depuis `data/engine.json` : si l'application de
bureau tourne, le serveur MCP se connecte au même moteur ; sinon il en démarre un.
`SYNORIVE_ENGINE_URL` permet de forcer l'adresse.

### Faire fonctionner Google et DuckDuckGo (optionnel, vivement recommandé)

Mesuré en août 2026 : Google exige désormais JavaScript (en HTTP simple on n'obtient qu'une page de
redirection), le point d'entrée html de DuckDuckGo est devenu une page d'atterrissage JS, Yandex
sert un captcha, et **les sept instances publiques SearXNG ont toutes renvoyé 429/403**. En pratique,
il ne reste qu'un seul chemin gratuit vers ces moteurs : **faire tourner votre propre SearXNG.**

```bash
node scripts/setup-searxng.mjs            # montrer ce qu'il compte faire (simulation, ne change rien)
node scripts/setup-searxng.mjs --apply    # installer réellement (nécessite Docker)
node scripts/setup-searxng.mjs --status   # est-il toujours en vie ?
```

Le moteur **le découvre et l'active au démarrage à froid** — aucun réglage à chercher. Mesuré après
installation : `google cse` a contribué à lui seul 20 résultats et DuckDuckGo 10, là où les deux
renvoient zéro en accès direct.

---

## Décisions de conception non évidentes

- **Recherche web et inférence cloud sont deux interrupteurs, pas un.** La recherche web révèle
  *ce que vous demandez* ; l'inférence cloud révèle *ce que vous possédez déjà*. Ce sont des risques
  différents : les fondre en un unique « mode privé » vous laisserait couper celui qui vous
  importait pendant que l'autre reste actif.
- **La fenêtre flottante d'image du presse-papiers ne va jamais sur le réseau** — même avec l'aperçu
  web activé. Envoyer du texte, c'est une phrase ; envoyer une capture d'écran, c'est une image qui
  peut contenir n'importe quoi.
- **Les résultats filtrés sont repliés, pas supprimés.** Le même article trouvé par cinq moteurs et
  republié par trois sites fait huit résultats mais une seule chose. Le replier en notant
  « 5 moteurs, 3 sites » conserve le nombre dont la vérification croisée a besoin ; le supprimer
  le jette.
- **Limité en débit ≠ cassé.** Un moteur qui renvoie un captcha n'est pas un analyseur cassé.
  Les deux sont comptés séparément, parce que « ralentis » et « cet adaptateur est mort » appellent
  des réponses opposées.
- **Les mesures qui ratent leur objectif restent dans le tableau.** Deux lignes plus haut portent
  un ⚠️ au lieu d'avoir été retirées.

---

## Licence

[AGPL-3.0-or-later](../../LICENSE). Si vous exploitez une version modifiée comme service en réseau,
vous devez publier vos modifications.

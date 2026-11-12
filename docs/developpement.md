# Développement

[Accueil](../README.md) · [Documentation](README.md) · [Architecture](architecture.md) · [Contribuer](../CONTRIBUTING.md)

## Tests Python

Utiliser **Linux ou WSL**, avec **Python 3.13.2 ou plus récent**. Les tests d’intégration chargent un vrai Home Assistant via `pytest-homeassistant-custom-component` ; Home Assistant n’est pas prévu pour Windows.

Depuis la racine du dépôt :

```sh
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements_test.txt
python -m pytest
```

La version du paquet de test est fixée dans [requirements_test.txt](../requirements_test.txt). `aiousbwatcher` et `pyserial` couvrent les dépendances USB que Home Assistant installerait normalement au démarrage.

| Fichiers | Ce qu’ils vérifient |
|---|---|
| `tests/test_parser.py` | paquets BTHome et valeurs décodées |
| `tests/test_aim.py` | géométrie, surfaces, refus des cas ambigus |
| `tests/test_vision.py` | réponses Jev, données invalides et photos annotées |
| `tests/test_init.py` | orchestration des sorts, délais, déduplication et persistance |
| `tests/test_remote.py` | réglages et commandes Bluetooth |
| `tests/test_config_flow.py`, `tests/test_panel.py` | configuration et commandes websocket |

Les adaptateurs Bluetooth, le réseau caméra et Jev sont simulés. La plupart des tests neutralisent les dépendances Home Assistant ; un test dédié charge le vrai serveur HTTP, enregistre le panneau et vérifie que son module JavaScript est servi à l’URL annoncée. Le rendu dans un navigateur et les communications avec les cartes demandent encore une vérification manuelle.

## Publier une version (Marketplace)

Le Marketplace de Home Assistant (2026.11+, ex-HACS) installe la dernière **release GitHub** du dépôt. Pour en publier une :

1. Mettre la même version dans `custom_components/cornelius_wand/manifest.json` (`version`) et dans le [CHANGELOG](../CHANGELOG.md).
2. Vérifier que la CI passe, y compris **hassfest** (manifeste) et **HACS** (ce qu’exige le Marketplace : `hacs.json`, icône `brand/icon.png`, description, topics).
3. Créer la release avec un tag **identique** à la version (`6.0.1`, sans `v`) ; son texte est affiché dans la mise à jour chez les utilisateurs, actions à faire en premier.

`hacs.json` (racine) donne le nom et la version minimale de Home Assistant. Sans release, le Marketplace installerait le dernier commit de `main`.

## Panneau JavaScript

Avec **Node.js 24**, sans installation npm :

```sh
node --check custom_components/cornelius_wand/www/panel.js
node --test tests/frontend/*.test.cjs
```

Ces tests utilisent le moteur JavaScript de Node et des éléments DOM simulés. Ils vérifient notamment la resynchronisation après un changement d’options dans un autre onglet, ainsi que les réponses tardives lors d’un changement de baguette. Ils ne remplacent pas un essai visuel du panneau dans Home Assistant.

## Vérifications automatiques

La CI utilise **Ubuntu 24.04, Python 3.13 et Node.js 24**. Elle exécute les tests Python, les tests du panneau et Ruff. Les versions et commandes exactes sont définies dans les [workflows](../.github/workflows/).

Pour lancer aussi l’analyse statique en local :

```sh
python -m pip install ruff==0.15.0
python -m ruff check custom_components tests bench
```

Un résultat de CI confirme ces contrôles, pas un test sur les cartes. Les sketches Arduino ne sont pas compilés par ce workflow.

## Firmware et modèle

Le [guide complet](guide.md) donne les cartes, bibliothèques Arduino, options de compilation et étapes de téléversement. Le modèle Edge Impulse est une bibliothèque Arduino exportée ; celui d’Alohomora est versionné dans [model/MagicWand_inferencing-1.0.4.zip](../model/MagicWand_inferencing-1.0.4.zip). Pour le remplacer, exporter le nouveau modèle et remplacer ce zip (un seul modèle à la fois). Le nom de son en-tête doit correspondre à celui inclus par `wand/gestures.cpp` et ses classes aux libellés de `wand/config.h`. Le firmware refuse à la compilation un modèle qui n’utilise pas **100 Hz** ou dont la fenêtre dépasse **256 échantillons**.

La compilation pour Nano 33 BLE nécessite le cœur **Arduino Mbed OS Nano Boards**, les bibliothèques **ArduinoBLE** et **Arduino_BMI270_BMM150**, ainsi que l’export Edge Impulse.

Les essais matériels couvrent notamment :

- modification d’un réglage Bluetooth et conservation de sa valeur après redémarrage ;
- calibration, annonce de présence, pause de visée, préparation, sort et veille ;
- interruption d’une connexion de réglages quand un geste commence ;
- photo récente et photo passée de la caméra, puis comportement quand la caméra ou Jev ne répondent plus.

Les essais matériels doivent préciser les versions de cartes, de cœurs Arduino, de bibliothèques et de modèle utilisées. Ne pas publier les clés ni les photos privées avec ces résultats.

## Mesurer la vision

Le dossier [bench/](../bench/) permet de capturer puis analyser des photos sans redémarrer Home Assistant. Les positions de référence restent dans le fichier local `bench/truth.json`, exclu de Git. Le modèle [truth.example.json](../bench/truth.example.json) contient des exemples synthétiques à copier pour créer ce fichier. Le [guide](guide.md#banc-dessai) décrit le protocole de mesure. La précision et le temps de réponse dépendent des images, de la configuration et du réseau ; ils sont à mesurer pour chaque installation.

`bench/.env`, `bench/truth.json`, `bench/photos/`, `bench/results/` et `camera/secrets.h` sont exclus du dépôt. Vérifier également le contenu des captures d’écran et des journaux avant de les partager.

# Guide de la baguette Cornelius

[Accueil](../README.md) · [Documentation](README.md) · [Architecture](architecture.md) · [Développement](developpement.md)

Ce guide décrit la collecte des gestes, le montage, la configuration et l’utilisation. La baguette fonctionne avec Home Assistant ; le serveur du miroir Cornelius n’est pas nécessaire.

Baguette magique pour Home Assistant : on vise une lampe, on lance le sort, elle s'allume.

- La **baguette** (Arduino Nano 33 BLE Rev2) reconnaît le geste et mesure son inclinaison.
- La **caméra au plafond** (ESP32-WROVER CAM) garde les dernières photos de la pièce.
- L'**intégration Home Assistant** fait analyser la photo du moment où l'on visait par **Jev** (TypeSafe) : la direction du bras, de la tête à la main, désigne la cible, et l'inclinaison de la baguette départage le sol, le mur et le plafond.

## Démarrer avec le modèle fourni

Le dépôt contient un modèle prêt à l’emploi : [model/MagicWand_inferencing-1.0.4.zip](../model/MagicWand_inferencing-1.0.4.zip), la bibliothèque Arduino exportée par Edge Impulse (projet « MagicWand », version 1.0.4). Il reconnaît deux classes, `Alohomora` et `Repos`, à 100 Hz sur une fenêtre de 1,1 s : exactement les libellés déjà déclarés dans [wand/config.h](../wand/config.h) (`Alohomora` sur le bouton 1). Avec lui, **les étapes 1 et 2 sont inutiles** : passer directement à l’[étape 3](#étape-3--firmware-de-la-baguette).

- Il a été appris sur les gestes d’une seule personne, baguette tenue USB vers soi. Si Alohomora est mal reconnu chez vous (score affiché par la commande `d` du moniteur série), enregistrez quelques exemples de votre geste et réentraînez (étapes 1 et 2).
- Pour ajouter d’autres sorts, il faut de toute façon votre propre modèle : un modèle ne reconnaît que les classes avec lesquelles il a été entraîné.
- À l’ajout du .ZIP, l’Arduino IDE peut proposer d’installer **Arduino_LSM9DS1**, **PDM** et **Arduino_OV767X** : ce sont les exemples d’Edge Impulse pour d’autres cartes, la baguette n’en a pas besoin.

## Étape 1 : collecter les gestes

Seulement pour entraîner votre propre modèle (autres sorts, ou Alohomora mal reconnu).

### Prérequis

1. Arduino IDE : installer le cœur **Arduino Mbed OS Nano Boards** et la bibliothèque **Arduino_BMI270_BMM150**.
2. [Node.js](https://nodejs.org) LTS, puis :
   ```bash
   npm install -g edge-impulse-cli
   ```
3. Un compte gratuit sur [edgeimpulse.com](https://edgeimpulse.com) et un nouveau projet « cornelius-wand ».

### Collecte

1. Téléverser [collect/collect.ino](../collect/collect.ino) sur la carte.
2. **Fermer le moniteur série** de l'IDE (le port ne peut être ouvert que par un seul programme).
3. Lancer le forwarder et le relier au projet :
   ```bash
   edge-impulse-data-forwarder --frequency 100
   ```
   Nommer les axes : `ax,ay,az,gx,gy,gz`.
4. Dans Edge Impulse, onglet **Data acquisition** : longueur d'échantillon 2000 ms, un libellé par sort.

### Conseils d'enregistrement

- Tenir la carte toujours dans le même sens, comme le manche de la baguette (port USB vers soi).
- 40 à 50 exemples par sort, en variant un peu vitesse et amplitude.
- 4 sorts bien distincts au plus, par exemple : `cercle`, `zigzag`, `coup_haut`, `spirale`.
- Une classe `Repos` : baguette immobile, gestes anodins, se gratter la tête, poser la baguette…
- Les libellés de [wand/config.h](../wand/config.h) (`SPELLS`, et `IDLE_LABEL` pour `Repos`) doivent reprendre exactement ceux d'Edge Impulse, majuscules comprises.

## Étape 2 : entraîner le modèle

Dans **Create impulse** : fenêtre de 1500 ms, pas de 200 ms, bloc **Spectral Analysis**, bloc **Classification**. Entraîner, vérifier la matrice de confusion, puis **Deployment → Arduino library**. La durée de la fenêtre dépend du modèle exporté ; la baguette l’affiche au démarrage. Le firmware vérifie à la compilation la fréquence de 100 Hz et une fenêtre de 256 échantillons au maximum.

Pour tester le modèle seul, sans BLE ni veille : [test-model/test-model.ino](../test-model/test-model.ino) affiche les scores de chaque classe toutes les 200 ms.

## Étape 3 : firmware de la baguette

Le sketch [wand/](../wand/) fait tourner tout le cycle :

1. **Réveil par mouvement** : le BMI270 surveille les mouvements pendant que le nRF52840 est éteint (veille profonde). Son interruption INT1, reliée à P0.11, rallume la carte.
2. **Visée** : on vise la cible, on marque une courte pause, puis on fait le geste. Pendant la pause, l'accéléromètre donne l'**inclinaison** de la baguette, et la baguette retient **quand** la pause s'est finie : Home Assistant prendra la photo de ce moment-là, bras tendu vers la cible. **Dès que la pause finit** (la baguette se remet à bouger), une annonce **« visée »** part : Home Assistant analyse la photo de la pause pendant tout le geste, pour que la réponse de Jev puisse être disponible dès le sort. Une seule règle sert à l'annonce et au sort : une pause de visée est une immobilité d'au moins la **Pause de visée minimale**, et d'une immobilité commencée avant le dernier sort, seule compte la part qui suit le sort. Arrêté assez longtemps après un sort, on enchaîne vers la même cible ; plus brièvement, on baisse la baguette et cet arrêt ne vise rien. Au-delà de 3 s sans le moindre tremblement, la baguette était posée : ce n'est pas une visée, et le geste qui la ramasse (y compris au réveil) n'en a pas non plus ; tenue à la main, même longtemps, c'est une visée. Sans pause de visée, la baguette prend le moment le plus calme juste avant le geste (après le dernier sort, hors de ces arrêts). Une pause finie plus de 2 s (**Âge maximal de la visée**) avant la fin du geste est ignorée plutôt que de reprendre la cible précédente. Si un arrêt plus bref suit la dernière pause (on a pivoté vers une autre cible), c'est cet arrêt qui vise ; l'analyse lancée à la fin de la pause ne sert alors pas, et le sort attend la sienne. Si l'inclinaison de la visée et celle de la fin du geste diffèrent de plus de 60°, la pause était un repos (baguette pendante) et le geste la levait vers la cible : le sort compte comme sans visée. **Un sort reconnu sans visée est ignoré** (réglage **Ignorer les sorts sans visée**, activé par défaut) : c'est le plus souvent le mouvement qui lève la baguette vers la cible. Rien n'est envoyé, la LED clignote brièvement en rouge, et la visée reste au vrai sort. Pour que ce mouvement ne soit pas reconnu une seconde fois, la reconnaissance s'interrompt jusqu'à ce qu'il soit sorti de la fenêtre du modèle (une durée de fenêtre après sa fin), ou plus tôt dès que le score des sorts retombe sous la moitié du **Seuil de préparation** (le mouvement n'est plus reconnu) ; la visée, elle, est annoncée sans attendre. Réglage coupé, le sort part sans cible (LED jaune).
3. **Reconnaissance du sort** : une inférence Edge Impulse toutes les 100 ms sur la fenêtre du modèle (durée affichée au démarrage). Dès que le score d'un sort dépasse 30 %, une annonce **« préparation »** part : si la visée n'a pas été reçue, la photo est analysée pendant que le geste se termine. Le sort est validé après 2 inférences d'accord au-dessus de 60 %, puis lancé quand la baguette s'arrête (100 ms d'immobilité, ou 1,5 s au plus tard). Le sort suivant attend 1,5 s après le lancement ; la préparation se réarme à la fin de cette pause. Sans visée, avec **Ignorer les sorts sans visée**, la préparation n'est pas envoyée.
4. **Annonce BTHome** du sort, en rafale d'une seconde (0,4 s pour une préparation, 0,3 s pour une visée) : le sort, l'inclinaison, l'âge de la visée (remis à jour toutes les 60 ms) et la tension de la pile. Une visée remplace aussitôt une préparation ou une autre visée ; pendant la rafale d'un sort, elle attend sa fin (une plus récente la remplace), puis part avec son âge à jour : le sort n'est jamais écourté. Une visée, une préparation ou un sort coupe une connexion de réglages en cours.
5. **Veille** après 30 s d'immobilité, uniquement sur batterie : branchée en USB, la carte reste éveillée pour le moniteur série et les téléversements.

La LED clignote en bleu au réveil, en vert pour un sort avec visée, brièvement en rouge pour un sort ignoré faute de visée, et en jaune pour un sort envoyé sans visée (réglage Ignorer les sorts sans visée coupé). Le vibreur, s'il y en a un (`VIBRATION_PIN`), suit la LED. Le retour d'un sort ne bloque pas la baguette : pendant le clignotement, l'âge de la visée reste à jour et la visée suivante est annoncée.

### Installation

1. Bibliothèques : **Arduino_BMI270_BMM150**, **ArduinoBLE** et le modèle : [model/MagicWand_inferencing-1.0.4.zip](../model/MagicWand_inferencing-1.0.4.zip) ou votre propre export Edge Impulse (Croquis → Inclure une bibliothèque → Ajouter la bibliothèque .ZIP). Pour remplacer un modèle déjà installé, supprimer d’abord son dossier `MagicWand_inferencing` dans le dossier `libraries` du carnet de croquis.
2. Si le nom de l'export n'est pas `MagicWand_inferencing.h`, corriger l'`#include` en haut de [wand/gestures.cpp](../wand/gestures.cpp).
3. Dans [wand/config.h](../wand/config.h), associer chaque libellé Edge Impulse, écrit exactement comme dans Edge Impulse (majuscules comprises), à un numéro de bouton (de 1 à 4) dans `SPELLS`. Au démarrage, le moniteur série liste les classes du modèle et signale celles absentes de `SPELLS`.
4. Carte **Arduino Mbed OS Nano Boards → Arduino Nano 33 BLE**, puis téléverser. Les réglages se font ensuite depuis Home Assistant, sans rebrancher l’USB.

### Calibration de l'axe de visée (une seule fois)

La baguette doit savoir quel axe de la carte est sa pointe. Depuis Home Assistant : bouton **Calibrer l'axe de visée** ; la commande part dès que la baguette est réveillée (la bouger si elle dort). **LED bleue** : lever la baguette vers le plafond, puis la tenir immobile 1,5 s ; **verte** : c'est enregistré ; **rouge** : à refaire. Il faut la lever après le passage au bleu : posée sur la table ou déjà immobile, elle ne compte pas, et sans mouvement puis immobilité dans les 30 s, c'est le rouge. Le capteur **Calibration** donne le résultat, relu par Home Assistant 30 s après l'envoi de la commande.

Ou bien au moniteur série (115200 bauds) :

| Commande | Effet |
|---|---|
| `c` | calibrer l'axe de visée (au bleu, lever vers le plafond puis ne plus bouger) |
| `o` | afficher ou masquer l'inclinaison en direct |
| `i` | calibration, tension et durée d'immobilité |
| `t` | envoyer un sort de test (bouton 1), même sans visée (jaune) : Ignorer les sorts sans visée ne s'y applique pas, et il prend la visée en cours |
| `d` | afficher ou masquer les scores du modèle à chaque inférence |
| `s` | passer en veille tout de suite |

### Annonces BTHome

| Objet | Contenu |
|---|---|
| `0x00` paquet | numéro de paquet, nouveau à chaque visée, préparation et sort |
| `0x0C` tension | tension de la pile (V) |
| `0x3A` × 4 | un bouton par sort : `hold_press` quand le sort commence (préparation), `press` quand il est lancé ; aucun événement pour une visée |
| `0x3F` rotation | inclinaison en degrés (−90 à +90), **−180 si la visée est inconnue** |
| `0x42` durée | depuis combien de temps la pause de visée est finie (ms), remis à jour pendant la rafale |

Une **visée** se reconnaît à une inclinaison sans événement de bouton. Entre deux sorts, tant qu'elle est réveillée, la baguette émet une annonce de présence toutes les 500 ms, sans événement de bouton ni visée (−180), avec le numéro de la dernière annonce et la tension à jour : Home Assistant sait ainsi qu'elle est joignable pour les réglages. Les annonces sont **connectables**.

Les annonces BTHome et les réglages Bluetooth ne sont pas authentifiés. Il faut un adaptateur Bluetooth ou un proxy Bluetooth ESPHome à portée de la baguette. Pour la découvrir, il doit scanner en **actif** (le réglage par défaut de Home Assistant et d'ESPHome) : le nom « Cornelius » n'est envoyé qu'en réponse à un scan actif. Pour les réglages, il doit aussi savoir se connecter (adaptateur de l'hôte, ou proxy ESPHome avec `bluetooth_proxy: active: true`, qui autorise les connexions et n'a rien à voir avec le scan actif). Home Assistant la découvre aussi comme appareil BTHome : ignorer cette découverte, l'intégration la remplace.

## Étape 4 : caméra au plafond

Le dossier [camera/](../camera/) est le firmware d'une **Freenove ESP32-WROVER CAM** (OV2640, objectif grand-angle) fixée au plafond, objectif vers le bas. Elle range chaque image du capteur (10 à 15 par seconde) dans un tampon de 3 Mo en PSRAM, en effaçant les plus anciennes, et garde toujours au moins les 2,6 dernières secondes ; avec de grosses photos, elle n'en range qu'une partie, à intervalles réguliers. Elle les sert à Home Assistant :

- `http://<caméra>/capture.jpg` : la photo la plus récente, en JPEG 1024 × 768 ; `?ago=800` donne la plus proche d'il y a 0,8 s. L'en-tête `X-Photo-Age-Ms` donne son âge.
- Le serveur HTTP de la caméra ne demande pas d’authentification : garder la caméra sur un réseau de confiance, sans exposition Internet.
- Le serveur web tourne dans sa propre tâche : il répond tout de suite, sans attendre la prochaine image de la caméra. Il traite une requête à la fois.
- `http://<caméra>/` : la photo en direct, pour cadrer la caméra. La fermer une fois la caméra cadrée : elle occupe le serveur.
- Une photo de plus de 120 Ko est jetée (signalé au moniteur série). Sans nouvelle photo pendant 5 s, la caméra redémarre.

### Installation

1. Copier [camera/secrets.example.h](../camera/secrets.example.h) en `camera/secrets.h` et y mettre le Wi-Fi (et un mot de passe OTA, facultatif). Ce fichier contient des mots de passe : ne pas le partager.
2. Dans l'IDE Arduino : carte **ESP32 Wrover Module**, **PSRAM : Enabled**, **Partition Scheme : Minimal SPIFFS (1.9MB APP with OTA)**. Téléverser [camera/camera.ino](../camera/camera.ino) en USB la première fois.
3. Ensuite, mise à jour sans fil : dans **Outils → Port**, choisir le port réseau **cornelius-camera**.
4. Fixer la caméra au plafond, au-dessus de la zone où l'on lance les sorts, et vérifier le cadrage sur `http://cornelius-camera.local/`.

## Étape 5 : intégration Home Assistant « Baguette Cornelius »

Le dossier [custom_components/cornelius_wand/](../custom_components/cornelius_wand/) est une intégration dédiée. Elle écoute les annonces de la baguette, fait analyser la photo par Jev, désigne la cible et déclenche l'entité **Sort**. Les actions se font avec des automatisations classiques.

### Installation

1. **Avec le Marketplace** (Home Assistant 2026.11 et plus, qui y intègre HACS) : [ouvrir le dépôt dans le Marketplace](https://my.home-assistant.io/redirect/hacs_repository/?owner=lfpoulain&repository=Cornelius-Wand&category=integration), ou **Paramètres → Marketplace → ⋮ → Dépôts personnalisés**, adresse `https://github.com/lfpoulain/Cornelius-Wand`, catégorie **Intégration**. Installer **Baguette Cornelius** : les nouvelles versions apparaissent ensuite comme des mises à jour, avec leurs notes. Le Marketplace avertit que l’intégration vient d’un auteur tiers, pas du projet Home Assistant.
   **À la main** (versions plus anciennes, ou sans le Marketplace) : copier le dossier `custom_components/cornelius_wand` dans `/config/custom_components/` (Samba, SSH ou app File editor), puis redémarrer.
2. Lancer un sort, ou simplement bouger la baguette pour la réveiller : Home Assistant propose **Baguette Cornelius** dans les appareils découverts (il faut un scan actif, voir l'étape 3). Sinon : **Paramètres → Appareils et services → Ajouter une intégration → Baguette Cornelius**.
3. Dans les options de l'intégration :

| Option | Rôle |
|---|---|
| Noms des sorts | dans l'ordre des boutons du firmware, par exemple `Alohomora, Lumos` (4 au plus). Deux noms ne peuvent pas donner le même identifiant (« Lumos, lumos »), et `Inconnu` est réservé aux boutons sans sort |
| Adresse de la caméra | par exemple `http://cornelius-camera.local` |
| Clé API TypeSafe | gardée par Home Assistant |

Sans adresse de caméra ou sans clé, la vision est coupée : les sorts partent sans cible, et plus aucune photo ne part chez TypeSafe. Vider le champ suffit.

4. Calibrer l'axe de visée (étape 3), puis placer les cibles.

### Placer les cibles : le panneau « Baguette »

L'intégration ajoute **Baguette** dans la barre latérale (administrateurs). On y voit la photo de la caméra ; avec plusieurs baguettes, une liste à côté du titre choisit celle dont on place les cibles :

1. **Ajouter** une cible par son nom (« Lampadaire »), et dire sur quelle **surface** elle est si besoin : sol, mur ou plafond ;
2. la choisir dans la liste, puis, une fois la photo affichée, **cliquer sur l'objet**. La cible suivante à placer est ensuite choisie toute seule. Hors champ : cliquer dans la marge hachurée autour de la photo, là où serait l'objet (au bord de la photo, la direction ne serait juste que depuis un seul endroit) ; la marge ne prolonge la photo que d'environ 11 % sur les côtés et 15 % en haut et en bas (pour la photo 4/3 actuelle), et une cible placée plus loin est dessinée au bord. Caché derrière un meuble : cliquer là où il se trouve sur la photo ;
3. **Vérifier la visée** : 3 s pour pointer une cible, bras tendu, puis la photo est analysée. La baguette n'y sert pas : son inclinaison n'est pas lue, et les cibles de toutes les surfaces sont comparées. Le panneau dessine les épaules (vert), la main (rouge), la direction du bras et la tolérance ; une carte donne la cible retenue, l'écart avec chaque cible, la confiance de Jev et son temps de réponse. Le bouton est grisé tant que la vision n'est pas configurée. Après un changement d’options ou de cibles dans un autre onglet, cliquer sur **Photo** : l’état est relu et le bouton d’analyse est remis à jour. **Vérifier la visée** relit aussi la configuration avant de commencer.

L'heure de prise de la photo est affichée à côté du titre de la carte, en rouge si la photo a plus de 0,3 s (la caméra ne range plus de photo ; la vérification de la visée la refuserait aussi).

La clé de la cible, affichée sous son nom, devient l'attribut `cible` de l'événement Sort : le nom en minuscules, sans accents, espaces et ponctuation remplacés par `_` (« Prise TV » → `prise_tv`). Un nom sans lettre ni chiffre est refusé.

### Comment la cible est choisie

1. Sur la photo prise au moment où l'on visait, Jev trouve la **tête et les épaules** de la personne qui tient la baguette (pour savoir si quelqu'un est vraiment là), **sa tête** seule et **la main** qui tient la baguette.
2. Vue depuis la tête, la **direction du bras** (tête → main) est comparée à celle de chaque cible. Vu du plafond, le cadre « tête et épaules » couvre souvent tout le buste et décale le départ du bras ; celui de la tête seule est plus petit et mieux placé (erreur de position 0,023 contre 0,030 sur le banc). Si Jev ne trouve pas la tête, le bras part du centre des épaules. La main est le centre de son cadre (Jev ne doit encadrer que la main, pas le bras) ; si ce cadre est quand même au moins 2,5 fois plus long que large, il contient l'avant-bras, et la main est son coin le plus éloigné de la tête. La plus proche gagne si elle est à moins de la **tolérance** (25° par défaut).
3. L'**inclinaison** de la baguette donne la surface visée : plafond (≥ **Seuil du plafond**, 45° par défaut), sol (≤ **Seuil du sol**, −30° par défaut), mur sinon. Seules les cibles de cette surface, ou sans surface, sont comparées : on distingue ainsi une lampe et la prise juste en dessous.
4. **Au plafond**, si le bras levé vers la caméra paraît trop court pour donner une direction (la main trop près des épaules sur la photo), la cible placée au plafond la plus proche de la personne est retenue, à condition que la suivante soit au moins 1,3 fois plus loin : sinon aucune n'est retenue (« bras levé : … à distance presque égale, vise moins à la verticale »). La main doit avoir été détectée : une main absente ou un cadre invalide ne permet jamais ce choix de proximité. Une cible sans surface ne compte pas ici : sans cible placée au plafond, le sort part sans cible. Si le bras donne une direction, elle est comparée comme ailleurs aux cibles du plafond et à celles sans surface.
5. **Sans visée** (aucune pause de visée retenue avant le geste, ni de moment assez calme, ou pause finie plus de 2 s avant la fin du geste), la baguette ignore le sort ; réglage Ignorer les sorts sans visée coupé, il part sans cible et sans appeler Jev : une photo d'un autre moment ne désignerait rien.

La photo est analysée dès l'annonce « visée », à la fin de la pause, pendant tout le geste (le temps de réponse dépend de la caméra, du réseau et de Jev). La préparation et le sort reprennent cette analyse s'ils désignent le même instant (à 0,2 s près, pour une analyse de moins de 4 s) ; la préparation en lance une si la visée n'a pas été reçue, et le sort aussi si la pause retenue a changé. Si la caméra n'a pas donné la photo de l'analyse reprise (caméra injoignable), le sort en relance une sur la photo du même instant, une seule fois, s'il lui reste au moins 0,4 s et si la caméra garde encore cette photo ; une erreur de Jev n'est pas relancée (elle coûterait un second appel pour la même réponse). Les attributs `analyse` (`visée`, `préparation` ou `sort` : l'annonce qui a lancé l'analyse), `avance_ms` (son avance sur l'annonce du sort) et `attente_ms` (le temps passé à attendre la vision, quelques millisecondes quand l'analyse avait assez d'avance) permettent de le vérifier.

Le sort part toujours, au plus tard 1,5 s après son annonce. Si la caméra ou Jev ne répondent pas à temps (`vision trop lente`), si la caméra n'a plus la photo du moment visé (plus de 300 ms d'écart), ou si la réponse de Jev est illisible, il part sans cible, avec l'erreur dans l'attribut `vision`.

Une annonce reçue il y a plus de 3 s (rejouée par Home Assistant depuis son historique, au démarrage ou au rechargement de l'intégration) ne lance ni sort, ni analyse, ni connexion.

### Entités

| Entité | Rôle |
|---|---|
| **Sort** (`event`) | se déclenche à chaque sort. Type d'événement : le nom du sort (`alohomora`), ou `inconnu` pour un bouton sans sort. Attributs : `sort`, `nom`, `bouton`, `cible`, `nom_cible`, `inclinaison`, `surface`, `direction` (du bras sur la photo), `ecart`, `refus` (pourquoi aucune cible), `position_x`, `position_y` (la personne sur la photo), `vision` (`ok`, l'erreur, ou `null` sans visée), `attente_ms`, `analyse`, `avance_ms` |
| **Cible visée** | nom de la cible du dernier sort ; en attributs `cible` (sa clé), `direction`, `ecart`, `refus` |
| **Statut** | message du dernier sort, réglage ou commande ; au-delà de 255 caractères, l'état est tronqué et le texte complet est dans l'attribut `message` |
| **Cibles** | nombre de cibles ; leur nom, leur position et leur surface en attribut `cibles` |
| **Vision** | position de la personne à la dernière analyse ; en attributs `presence_probabilite`, `epaules_confiance`, `main`, `main_confiance`, `latence_ms`, `age_photo_ms` |
| **Photo analysée** | la photo de la dernière analyse (sort, ou « Vérifier la visée » dans le panneau), avec les épaules (vert), la tête (bleu), la main (rouge), la direction du bras depuis la tête et les cibles (au bord, « hors champ », pour celles placées dans la marge) |
| **Inclinaison** | inclinaison de la dernière visée |
| **Tolérance de la visée** | écart maximal entre le bras et la cible (25° par défaut, de 5 à 60°) |
| **Mode sort uniquement** | allumé, le geste suffit : ni photo ni appel à Jev, l'événement Sort part dès l'annonce de la baguette, sans cible (`refus` et `vision` valent `mode sort uniquement`). Pour déclencher une action par sort, sans viser : une automatisation sur le type d'événement, sans condition sur `cible`. Le panneau peut toujours vérifier la visée. Pour qu'un sort lancé sans visée parte aussi, couper **Ignorer les sorts sans visée** |
| **Seuil du plafond**, **Seuil du sol** | inclinaison à partir de laquelle la baguette vise le plafond (45° par défaut, de 15 à 85°) ou le sol (−30° par défaut, de −85 à −5°) ; à monter si une lampe haute sur un mur passe pour une cible du plafond |
| **Appels à Jev** | nombre total de photos envoyées à Jev (visées, préparations, sorts et vérifications du panneau), gardé après un redémarrage |
| **Appels à Jev depuis le réveil** | les mêmes, depuis le dernier réveil de la baguette ; en attribut `reveil` (son heure). Home Assistant tient la baguette pour endormie après 5 s sans annonce |
| **Calibration** | `calibrée` ou `à calibrer`, lu sur la baguette ; en attributs `derniere_calibration`, `firmware` (version du firmware : 7 pour celui de ce dépôt, avec l'intégration 6.0.1) et `reglages_en_attente` (nombre) |
| **Calibrer l'axe de visée** | commande envoyée à la baguette dès qu'elle est réveillée |
| **Réglages par défaut** | met tous les réglages de la baguette en attente à leur valeur par défaut, envoyés comme des réglages modifiés ; un réglage modifié ensuite, avant l'envoi, passe par-dessus. Les réglages propres à Home Assistant (tolérance, seuils des surfaces, mode sort uniquement) ne changent pas |
| Réglages de la baguette | voir ci-dessous ; attribut `en_attente` tant que le réglage n'est pas arrivé sur la baguette |
| **Tension** | tension de la pile, à chaque annonce |

Après un redémarrage de Home Assistant, **Inclinaison**, **Cible visée** et **Tension** gardent leur valeur jusqu'à ce que la baguette en donne une nouvelle.

### Associer sort + cible → action

#### Avec le blueprint (le plus simple)

Le [blueprint](../blueprints/automation/cornelius_wand/sort.yaml) crée une automatisation par sort : on choisit l’entité **Sort**, le sort (`alohomora`), puis, pour chaque cible (6 au plus), sa clé et l’action à lancer. Une seule action part par sort, dans cet ordre : **Baguette levée** (inclinaison de la visée au-dessus d’un seuil, par exemple 60° pour un plafonnier, quelle que soit la cible), puis la **cible visée**, sinon l’action **Sans cible**.

Pour l’installer, copier le fichier dans `/config/blueprints/automation/cornelius_wand/sort.yaml`, puis **Paramètres → Automatisations et scènes → Blueprints → Recharger**. Une fois le dépôt public, ce lien l’importe directement : [importer le blueprint](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Flfpoulain%2FCornelius-Wand%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fcornelius_wand%2Fsort.yaml). Ensuite : **Créer une automatisation** depuis le blueprint « Baguette Cornelius - un sort, une action par cible ».

#### À la main

Une automatisation par sort, déclenchée par l'entité **Sort**, avec une branche par cible :

```yaml
alias: Baguette - Alohomora
mode: queued
triggers:
  - trigger: event.received
    target:
      entity_id: event.baguette_cornelius_sort
    options:
      event_type:
        - alohomora
actions:
  - choose:
      - alias: Lampadaire
        conditions:
          - condition: state
            entity_id: event.baguette_cornelius_sort
            attribute: cible
            state: lampadaire
        sequence:
          - action: switch.toggle
            target:
              entity_id: switch.lampe
      # … une branche par cible
```

Les conditions portent sur l'attribut `cible` de l'entité **Sort** elle-même, pas sur le capteur **Cible visée** : c'est l'état de l'événement qui déclenche l'automatisation. (Depuis l'intégration 6.0.1, le capteur est aussi mis à jour avant l'événement.) Sans cible reconnue, l'attribut `cible` vaut `null` : une branche `default` peut gérer ce cas. L'identifiant de l'entité Sort dépend du nom et de la pièce de l'appareil. Chaque sort émet aussi l'événement `cornelius_wand_spell` sur le bus, avec les mêmes données et l'adresse Bluetooth de la baguette (`address`).

### Réglages de la baguette, sans USB

Les réglages marqués `[HA]` dans [wand/config.h](../wand/config.h) se changent depuis l'appareil Baguette Cornelius (section Configuration). La baguette les garde en flash ; `config.h` ne donne que les valeurs par défaut.

| Réglage | Rôle | Défaut |
|---|---|---|
| **Seuil du sort** | confiance du modèle pour valider un sort | 60 % |
| **Seuil de préparation** | score qui déclenche l'annonce « préparation » | 30 % |
| **Arrêt en fin de geste** | immobilité qui fait partir le sort | 100 ms |
| **Seuil d'immobilité** | vitesse de rotation sous laquelle la baguette est immobile | 15 °/s |
| **Pause de visée minimale** | durée d'une pause de visée (après le dernier sort pour l'arrêt qui le suit), mesurée par blocs de 100 ms | 200 ms |
| **Seuil de visée sans pause** | sans pause, moment le plus calme retenu s'il reste sous cette vitesse | 40 °/s |
| **Âge maximal de la visée** | au-delà, la visée est ignorée ; 2,2 s au plus | 2000 ms |
| **Veille après** | immobilité avant la veille profonde | 30 s |
| **Seuil de réveil** | mouvement qui réveille la baguette | 60 mg |
| **Ignorer les sorts sans visée** (interrupteur) | un sort reconnu sans visée n'est pas envoyé (bref rouge) ; coupé, il part sans cible (jaune) | activé |

La caméra garde toujours au moins 2,6 s de photos. Au sort, Home Assistant demande la photo d'il y a au plus **Âge maximal de la visée** + **Arrêt en fin de geste** + 150 ms (2,25 s par défaut) : en montant ces deux réglages au-delà de 2,6 s, la photo du moment visé peut ne plus être disponible et le sort part alors sans cible.

Un réglage modifié reste **en attente** (attribut `en_attente`) jusqu'à ce que la baguette soit réveillée ; si elle dort, il suffit de la bouger. Dès que Home Assistant reçoit sa balise de présence, il s'y connecte, écrit les réglages, relit tout, puis se déconnecte. Il ne se connecte pas en plein geste : il attend 3 s de calme après le réveil et après chaque visée, préparation ou sort, si bien que des sorts enchaînés toutes les 2 à 3 s repoussent la connexion. Un sort lancé pendant la connexion la coupe : il passe en premier, et l'échange est retenté 15 s plus tard, ou au réveil suivant. Une fin de pause de visée ou une préparation, elle, attend la fin de la connexion : tenir la baguette à la main pendant l'échange ne l'interrompt plus. **Réglages par défaut** et **Calibrer l'axe de visée** partent de la même façon. La commande de calibration part avant la relecture des réglages, est retentée toutes les 5 s en cas d'échec, et est **abandonnée au bout de 60 s** si la baguette reste injoignable (statut « Commande abandonnée ») : elle ne se déclenche plus des minutes plus tard, au réveil suivant. Un échange Bluetooth bloqué est abandonné au bout de 30 s. Après une calibration, Home Assistant relit la baguette 30 s plus tard.

### Ajouter un sort

1. L'enregistrer dans Edge Impulse, réentraîner et exporter le modèle.
2. L'ajouter dans `SPELLS` de [wand/config.h](../wand/config.h).
3. L'ajouter, dans le même ordre, dans les options de l'intégration.
4. Créer son automatisation avec le blueprint (même entité Sort, le nouveau sort), ou sur le modèle d'Alohomora.

### Jev

[Jev](https://docs.typesafe.ai) (TypeSafe) répond à des questions typées sur la photo, toutes évaluées en parallèle (`POST https://api.typesafe.ai/v1/systemone`, modèle `jev-1.14.0`) : y a-t-il quelqu'un (`noul`), où sont la tête et les épaules de la personne qui tient la baguette (`box`), où est sa tête seule (`box`), où est la main qui la tient (`box`). Comme le recommande sa documentation, les consignes sont courtes et en anglais, le contexte de la scène est dans un champ nommé et la photo part en multipart. Un `box` renvoie toujours un cadre, même sur une pièce vide : la présence doit être un nombre fini compris entre 0 et 1 (absente ou invalide, les cadres sont ignorés), et Jev doit voir quelqu'un : présence d'au moins 85 %, **ou** cadre des épaules sûr à au moins 40 %. La présence seule ne suffit pas : pendant un geste réel, elle peut tomber à 19 % alors que la personne est bien sur la photo, tandis que sur la pièce vide elle monte jusqu'à 77 % ; la confiance des épaules, elle, reste sous 22 % sur la pièce vide et au-dessus de 55 % avec quelqu'un. Quand personne n'est retenu, le statut l'indique avec les deux valeurs (« aucune personne détectée (présence 26 %, épaules 18 %) »). Les images sont encore **expérimentales** chez TypeSafe (en-tête `TypeSafe-Danger-Zone-Images: true`). Jev a 10 s pour répondre à une vérification depuis le panneau ; pour un sort, la vision doit tenir en 1,5 s.

**Vie privée :** une photo de la pièce part chez TypeSafe à chaque fin de pause de visée (immobilité d'au moins la Pause de visée minimale, comptée après le dernier sort, puis mouvement ; pas pour une baguette restée posée), tant que la baguette est réveillée, même si aucun sort ne suit. Une autre part à la préparation si la visée n'a pas été reçue ou a changé, au sort si la visée a changé entre-temps, ou si la photo de la visée n'a pas pu être prise (caméra injoignable) et que la caméra l'a encore (une fois au plus), et une à chaque vérification depuis le panneau. Rien quand la baguette dort ou reste immobile, pour un sort sans visée, ni en **Mode sort uniquement** (sauf vérification depuis le panneau). Allonger la **Pause de visée minimale** réduit ces envois ; vider l'adresse de la caméra ou la clé TypeSafe les arrête. Dans un lieu de travail, les personnes filmées doivent en être informées.

### Banc d'essai

Le dossier [bench/](../bench/) mesure Jev sans redémarrer Home Assistant. `capture.py` prend des séries de photos ; `bench.py` les analyse avec le code de l’intégration et compare les résultats aux positions de référence des épaules et de la main dans `truth.json`. Le fichier [truth.example.json](../bench/truth.example.json) contient uniquement des exemples synthétiques : le copier en `bench/truth.json`, puis y annoter les images du jeu local. `truth.json` est exclu du suivi Git. L’option `bench.py --aide` produit des copies quadrillées pour annoter ces positions.

Un jeu de mesure peut inclure une pièce vide, une personne sans baguette, puis plusieurs positions de visée avec la baguette. Les mesures utiles sont l’erreur de position, l’écart angulaire du bras, le nombre de personnes ou de mains manquées et la latence. Les photos, annotations et résultats propres à une installation restent des données locales ; aucun résultat de précision n’est garanti pour une autre pièce.

### Tests

Le [guide de développement](developpement.md) regroupe les commandes Python, JavaScript et les limites de validation du firmware.

Les tests utilisent `pytest-homeassistant-custom-component` (un vrai Home Assistant, Python 3.13.2 ou plus) :

```bash
pip install -r requirements_test.txt
```

```bash
python -m pytest
```

[requirements_test.txt](../requirements_test.txt) ajoute `aiousbwatcher` et `pyserial` : l'intégration `bluetooth` dépend de `usb`, dont Home Assistant n'installe les paquets qu'au démarrage, ce que les tests ne font pas.

Les tests Home Assistant s’exécutent sous Linux ou WSL ; l’exécution native sous Windows n’est pas prise en charge.

# Historique

[Accueil](README.md) · [Guide](docs/guide.md)

## 6.0.1

Version de l’intégration associée au **firmware 7**.

- Installation depuis le **Marketplace** de Home Assistant 2026.11 (ex-HACS) : `hacs.json`, icône de l’intégration, manifeste validé par hassfest et l’action HACS dans la CI.

- Correction : le capteur **Cible visée** est mis à jour avant l’événement **Sort**. Une automatisation qui le lisait en condition obtenait la cible du sort précédent (une action de retard).
- Correction : la calibration demandée depuis Home Assistant n’attend plus indéfiniment. La commande est abandonnée après 60 s sans baguette joignable, retentée toutes les 5 s, envoyée avant la relecture des réglages et jamais renvoyée une fois reçue ; un échange Bluetooth bloqué est abandonné au bout de 30 s.
- Correction : une personne bien présente n’est plus écartée quand la présence donnée par Jev chute pendant le geste (jusqu’à 19 % mesuré) : un cadre des épaules sûr à 40 % suffit. Le statut dit « aucune personne détectée » avec la présence et la confiance des épaules, au lieu de l’ambigu « personne sur la photo ».
- La direction du bras part de la **tête** (nouvelle question à Jev), et non plus du centre du cadre tête + épaules, qui couvre souvent tout le buste vu du plafond : position mesurée 0,023 contre 0,030 sur le banc. Les épaules servent encore à décider si quelqu’un est là, et d’origine si la tête manque. La tête est dessinée en bleu sur la photo annotée et dans le panneau.
- Main mieux placée : Jev ne doit plus encadrer que la main, sans le bras (écart de direction médian 1,6° contre 2,7° au banc). Un cadre de main resté très allongé (au moins 2,5 fois plus long que large) contient l’avant-bras : la main est alors son coin le plus loin de la tête. Sur un sort réel vers la Cuisine, la direction passe de 12,7° (cible ratée) à 38,9°.
- Correction : les noms de cibles accentués (« Lumière ») ne s’affichent plus avec un carré sur la photo annotée.
- Modèle **Alohomora** fourni : [model/MagicWand_inferencing-1.0.4.zip](model/MagicWand_inferencing-1.0.4.zip), à ajouter dans l’Arduino IDE sans collecte ni entraînement. La documentation explique quand entraîner son propre modèle.
- [Blueprint](blueprints/automation/cornelius_wand/sort.yaml) Home Assistant : une automatisation par sort, une action par cible, une action « baguette levée » prioritaire et une action sans cible.
- Firmware 7 : une fin de pause de visée ou une préparation ne coupe plus la connexion de réglages de Home Assistant ; elle part à la fin de la connexion. Seul un sort la coupe encore.
- Refus des résultats de vision dont la présence est absente ou invalide ; une main absente ne désigne plus une cible au plafond par proximité.
- Resynchronisation des options et cibles depuis le bouton **Photo** ; vérification de la configuration avant une analyse manuelle.
- Tests de régression JavaScript du panneau, sans dépendances npm, et test d’enregistrement du panneau sur le vrai serveur HTTP de Home Assistant.
- Vérification à la compilation de la fréquence du modèle Edge Impulse (100 Hz) et de la taille de sa fenêtre (256 échantillons au maximum).
- Accueil du dépôt, bannière et documentation organisée ; le guide détaillé auparavant dans le README est conservé dans [docs/guide.md](docs/guide.md).

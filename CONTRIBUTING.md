# Contribuer à la baguette Cornelius

[Accueil](README.md) · [Architecture](docs/architecture.md) · [Développement](docs/developpement.md)

Commencer par un problème concret : le geste effectué, le résultat attendu et ce qui s’est passé. Pour un défaut de visée ou de Bluetooth, préciser le firmware, la version de l’intégration, la carte et les réglages qui permettent de le reproduire.

## Proposer une modification

1. Garder une modification centrée sur un problème ou un comportement.
2. Ajouter un test de régression lorsqu’un bug affecte le protocole, le choix de cible, les tâches asynchrones ou la sécurité.
3. Exécuter les vérifications décrites dans [Développement](docs/developpement.md). Dire clairement ce qui a été essayé sur la carte et ce qui a seulement été testé en simulation.
4. Mettre à jour le [guide](docs/guide.md) si une option, une commande, une étape d’installation ou un comportement visible change.
5. Décrire le résultat et les limites de validation dans la proposition de modification.

## Quelques invariants utiles

- Un numéro de paquet répété ne doit pas lancer plusieurs fois le même sort.
- Une visée absente ou invalide ne doit pas inventer de cible.
- Une erreur de caméra ou de vision doit permettre au sort de partir sans cible, dans son délai.
- Une réponse d’une autre baguette ne doit pas remplacer les cibles ou la photo du panneau courant.
- Un changement de protocole exige une vérification cohérente du firmware et de l’intégration.

Ne pas joindre de clés API, de mots de passe Wi-Fi ou de photos privées. Pour illustrer un bug de vision, privilégier des coordonnées synthétiques ou une photo dont le partage a été autorisé.

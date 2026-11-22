# Julesfress.github.io

Site perso hébergé sur GitHub Pages. La page d'accueil (`index.html`) liste mes
projets ; chaque projet vit dans son propre dossier avec sa propre page
(ex. `Sondpres/index.html`). HTML et CSS statiques, sans framework.

| Projet | Source des données | Mise à jour |
| --- | --- | --- |
| `Sondpres/` — sondages présidentielle | Wikipédia | toutes les heures |
| `Hemicycle/` — carte des députés | open data de l'Assemblée nationale | tous les jours |
| `Instituts/` — précision des instituts de sondage | Wikipédia | à la main, après chaque scrutin national |

Chaque projet a son script Python, qui régénère ses PNG, et (sauf `Instituts/`,
dont les données ne bougent qu'à chaque scrutin) son workflow dans
`.github/workflows/` qui ne committe que si l'image a réellement changé. Les
scripts sont donc tenus d'être déterministes : deux exécutions sur les mêmes
données doivent produire des fichiers identiques au bit près, sinon le dépôt se
remplit de commits vides. C'est pourquoi `Hemicycle/hemicycle.py` fixe
explicitement le sens de ses axes, qu'une diagonalisation ne définit qu'au signe
près.

Les cinq couleurs de `assets/css/style.css` sont exactement celles des
graphiques matplotlib (constantes `INK`, `INK_SECOND`, `MUTED`, `GRID`,
`SURFACE` dans `Sondpres/sondages_2027.py`) : le fond de page est le fond des
PNG, donc les graphiques n'ont pas de bord visible et la seule couleur du site
est celle des courbes. Si la palette change d'un côté, changer l'autre.

## Ajouter un nouveau projet

1. Créer un dossier à la racine pour le projet (code, données, assets), avec
   un `index.html` qui reprend le style du site (voir `Sondpres/index.html`
   comme modèle : `.bar`, `.doc-head`, puis le contenu).
2. Dans `index.html` (racine), remplacer la ligne `.item--off` — ou en ajouter
   une — par un bloc `<li class="item"><a class="row" href="MonDossier/">`
   avec un numéro d'ordre, un titre et une phrase courte (pas de tag
   générique, pas de pavé explicatif).
3. Ne pas faire pointer une ligne directement vers une image ou un fichier
   brut — le clic doit amener sur la page du projet, qui peut ensuite offrir
   un lien secondaire vers le fichier en taille réelle ou le code source.
4. Ne pas écrire dans le HTML un chiffre que le script recalcule (nombre de
   scrutins, part de variance, dernière date) : la page ne se régénère pas avec
   les PNG et le chiffre deviendrait faux en silence. Ces valeurs vivent dans
   l'image, qui est refaite à chaque exécution.

## Entretien de `Sondpres/`

Les graphiques « par bloc » (`sondages_XXXX_blocs.png`) rangent chaque colonne
des tableaux Wikipédia dans un bloc d'après l'étiquette de parti de son en-tête
(constantes `BLOCS` et `HORS_BLOCS` de `sondages_2027.py`). Si un candidat d'un
nouveau parti apparaît, le script le signale à chaque exécution
(« ⚠ colonnes hors de tout bloc ») : tant qu'on ne l'a pas rangé, il manque au
total de son bloc.

## Entretien de `Hemicycle/`

La légende des sigles de groupe est écrite à la main dans `Hemicycle/index.html`,
faute de place sur les graphiques. Le script affiche à chaque exécution la liste
des groupes et leurs effectifs : si un groupe apparaît ou disparaît (l'UDR s'est
constituée en cours de législature), c'est là qu'on le voit, et la légende est à
compléter. Au changement de législature, mettre à jour la constante
`LEGISLATURE` — les données des législatures précédentes restent en ligne sous
le même schéma d'URL.

## Entretien de `Instituts/`

Pas de workflow : les scrutins passés ne bougent plus. Le script lit les pages
Wikipédia dans `Instituts/.cache/` (ignoré par git) et ne les télécharge que
si elles n'y sont pas ; `python instituts.py --refresh` les retélécharge toutes
(utile si une page a été corrigée).

Après un nouveau scrutin national, une fois la ligne « Résultats » ajoutée sur
Wikipédia : ajouter une entrée à `SCRUTINS` dans `instituts.py` (page, jour du
vote, titre de section qui mène au tableau), relancer, committer les PNG et
`derniers_sondages.csv`.

À surveiller à chaque exécution :

- « ⚠ unités retirées faute d'être publiées par tous » : un candidat qu'un des
  instituts ne publie pas sort de la comparaison pour tous. Sans conséquence
  pour un « divers centre » à 1 %, mais si c'est un candidat important, c'est
  sans doute la colonne de cet institut qui est mal lue.
- « sans sondage dans les 14 derniers jours » : un institut absent d'un
  scrutin. Normal pour Cluster17 avant 2022 ; ailleurs, vérifier que son nom
  est reconnu (constante `INSTITUTS`, comparée sans accents ni espaces).

La méthode est résumée en trois paragraphes dans `Instituts/index.html`, qui
citent les deux réglages du script (`JOURS_MAX` = deux semaines,
`SEUIL_VOIX` = 1 %) : si l'un change, changer le texte.

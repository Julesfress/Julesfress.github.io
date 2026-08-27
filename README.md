# Julesfress.github.io

Site perso hébergé sur GitHub Pages. La page d'accueil (`index.html`) liste mes
projets ; chaque projet vit dans son propre dossier avec sa propre page
(ex. `Sondpres/index.html`). HTML et CSS statiques, sans framework.

| Projet | Source des données | Mise à jour |
| --- | --- | --- |
| `Sondpres/` — sondages présidentielle | Wikipédia | toutes les heures |
| `Hemicycle/` — carte des députés | open data de l'Assemblée nationale | tous les jours |

Chaque projet a son script Python, qui régénère ses PNG, et son workflow dans
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

## Entretien de `Hemicycle/`

La légende des sigles de groupe est écrite à la main dans `Hemicycle/index.html`,
faute de place sur les graphiques. Le script affiche à chaque exécution la liste
des groupes et leurs effectifs : si un groupe apparaît ou disparaît (l'UDR s'est
constituée en cours de législature), c'est là qu'on le voit, et la légende est à
compléter. Au changement de législature, mettre à jour la constante
`LEGISLATURE` — les données des législatures précédentes restent en ligne sous
le même schéma d'URL.

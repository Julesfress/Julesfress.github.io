# Julesfress.github.io

Site perso hébergé sur GitHub Pages. La page d'accueil (`index.html`) liste mes
projets ; chaque projet vit dans son propre dossier avec sa propre page
(ex. `Sondpres/index.html`). HTML et CSS statiques, sans framework.

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

# Julesfress.github.io

Site perso hébergé sur GitHub Pages. La page d'accueil (`index.html`) liste mes
projets sous forme de tuiles colorées ; chaque projet vit dans son propre
dossier avec sa propre page (ex. `Sondpres/index.html`).

## Ajouter un nouveau projet

1. Créer un dossier à la racine pour le projet (code, données, assets), avec
   un `index.html` qui reprend le style du site (voir `Sondpres/index.html`
   comme modèle : header, `.project-hero`, `.project-figure`, `.project-body`).
2. Dans `index.html` (racine), remplacer la tuile `tile--ghost` — ou en
   ajouter une — par un bloc `<a class="tile" style="--tile-bg: var(--couleur);
   --tile-rot: ...deg;" href="MonDossier/">`, avec une icône (emoji), un titre
   et une phrase courte (pas de tag générique, pas de pavé explicatif).
   Couleurs disponibles : `--yellow`, `--coral`, `--sky`, `--mint`, `--lilac`,
   `--peach` (définies dans `assets/css/style.css`).
3. Ne pas faire pointer une tuile directement vers une image ou un fichier
   brut — le clic doit amener sur la page du projet, qui peut ensuite offrir
   un lien secondaire vers le fichier en taille réelle ou le code source.

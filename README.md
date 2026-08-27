# Julesfress.github.io

Site perso hébergé sur GitHub Pages. La page d'accueil (`index.html`) est un
sommaire de projets ; chaque projet vit dans son propre dossier avec sa propre
page (ex. `Sondpres/index.html`). Tout est en HTML et CSS statiques, sans
framework, sans JavaScript et sans traceur.

## Le parti pris graphique

Du papier imprimé : fond chaud, filets fins d'un cheveu, beaucoup de blanc,
et **une seule** couleur d'accent (`--accent`, une encre sienne) réservée aux
sur-titres, aux liens et aux états de survol. Le reste est en niveaux de gris.
Pas de carte, pas d'ombre portée décorative, pas de dégradé : la hiérarchie se
fait à la typo et à l'espace.

- **Fraunces** pour les titres (axes variables `SOFT` et `WONK` activés, c'est
  ce qui lui donne son grain) ;
- **Newsreader** pour le texte courant — et pour les italiques *à l'intérieur*
  d'un titre : le contraste entre les deux serifs fait l'accent, sans changer
  de corps (`.hero__title em`) ;
- **IBM Plex Mono** pour tout le petit texte en capitales : sur-titres,
  étiquettes, sources, légendes, pied de page.

Les couleurs de `assets/css/style.css` sont celles des graphiques matplotlib
(constantes `INK` / `INK_SECOND` / `MUTED` / `GRID` / `SURFACE` dans
`Sondpres/sondages_2027.py`). En particulier `--paper-2` **est** le fond exact
des PNG : une figure posée dans la page a l'air d'y avoir été imprimée. Si la
palette d'un côté change, changer l'autre.

Le site est en clair uniquement (`color-scheme: light`), volontairement : les
graphiques sont générés sur fond clair et n'auraient pas d'équivalent sombre.

## Ajouter un nouveau projet

1. Créer un dossier à la racine (code, données, images) avec un `index.html`
   calqué sur `Sondpres/index.html` : `.masthead`, `.article__head`
   (sur-titre `.kicker`, `h1.article__title`, `p.lede`, `ul.meta`), puis des
   sections `.section`, et enfin `.actions` + `.colophon`.
2. Sur une page projet, **tous** les conteneurs utilisent `shell shell--wide` :
   le texte, les figures et le bandeau partagent ainsi le même bord gauche.
   C'est `.prose` (68 ch) et `.lede` (46 ch) qui bornent la longueur de ligne,
   pas le conteneur.
3. Dans `index.html` (racine), remplacer l'entrée `.entry--soon` — ou en
   ajouter une avant — par un bloc `.entry > a.entry__link` : numéro d'ordre
   (`01`, `02`, …), titre, une phrase courte (pas de tag générique, pas de
   pavé explicatif), deux ou trois étiquettes, et une vignette.
4. La vignette est un **détail agrandi** de l'image du projet, pas l'image
   entière : à 280 px de large, une figure complète n'est plus lisible. Le
   cadrage se règle avec `transform-origin` sur `.entry__thumb img`.
5. Ne pas faire pointer une entrée directement vers une image ou un fichier
   brut — le clic doit amener sur la page du projet, qui offre ensuite un lien
   secondaire vers le fichier en taille réelle ou le code source.

## Les sondages

`Sondpres/sondages_2027.py` relit la page Wikipédia des sondages, lisse les
courbes et réécrit les PNG. `.github/workflows/update-sondages.yml` le relance
toutes les heures et ne committe que si le graphique 2027 a changé.

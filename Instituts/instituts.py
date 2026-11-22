#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Les instituts de sondage face aux urnes : pour chaque scrutin national depuis
2017, le dernier sondage de sept instituts (Ifop, Ipsos, OpinionWay, Harris
Interactive, Elabe, Odoxa, Cluster17) est confronté au résultat officiel.
Sondages et résultats sont lus sur Wikipédia, dans les tableaux de sondages
qui se terminent par une ligne « Résultats ».

La méthode, en trois temps :

1. Le sondage retenu. Pour chaque institut et chaque scrutin, son DERNIER
   sondage, à condition que le terrain se soit achevé dans les JOURS_MAX jours
   précédant le vote. Plusieurs sondages achevés le même jour (deux
   commanditaires, un rolling et une vague classique) sont moyennés. On ne
   moyenne pas avec les sondages antérieurs : quand l'opinion bouge en fin de
   campagne (Mélenchon en 2022), cela pénaliserait les instituts qui publient
   souvent, sans rien dire de la justesse de leur dernière photographie.

2. L'erreur d'un institut sur un scrutin. Les candidats, listes ou blocs
   ayant obtenu au moins SEUIL_VOIX % des suffrages sont comparés un à un,
   et l'erreur est la moyenne quadratique des écarts (racine de la moyenne des
   carrés, en points) : un écart de 4 points sur un candidat compte plus que
   quatre écarts d'un point. En dessous du seuil, les sondages ne publient
   souvent qu'un « <0,5 % », et l'écart ne serait que de l'arrondi.
   Tous les instituts d'un même scrutin sont jugés sur les MÊMES unités :
   - quand un institut regroupe deux colonnes du tableau (le RN et les LR
     ralliés à Ciotti en 2024, LR et divers droite), tout le monde est
     comparé sur le regroupement ;
   - une unité qu'un des instituts ne publie pas sort de la comparaison
     pour tous (elle est signalée à l'exécution).

3. Le score. Les scrutins n'ont pas tous la même difficulté (tous les
   instituts ont manqué la poussée de Mélenchon en 2022) et tous les
   instituts ne les ont pas tous couverts (Cluster17 n'existait pas en 2017).
   Sur chaque scrutin, on retranche donc à l'erreur de chaque institut
   l'erreur moyenne des instituts présents ; le score final est la moyenne de
   ces écarts. Négatif : l'institut se trompe moins que ses concurrents.

Sorties (dans le dossier du script) :
    instituts_classement.png   le score de chaque institut, avec le détail par scrutin
    instituts_scrutins.png     l'erreur de chaque institut, scrutin par scrutin
    derniers_sondages.csv      les sondages retenus, candidat par candidat

Usage :
    python instituts.py              # pages Wikipédia lues dans .cache/ si déjà téléchargées
    python instituts.py --refresh    # retélécharge toutes les pages

Dépendances : requests, lxml, numpy, matplotlib   (voir requirements.txt)
"""
from __future__ import annotations

import argparse
import copy
import csv
import math
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import lxml.html as LH
import matplotlib
import numpy as np
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ICI = Path(__file__).resolve().parent
CACHE = ICI / ".cache"
API_URL = "https://fr.wikipedia.org/w/api.php"
USER_AGENT = "instituts-sondage-chart/1.0 (script pédagogique)"

JOURS_MAX = 14       # le dernier sondage doit s'achever au plus tard 14 jours avant le vote
SEUIL_VOIX = 1.0     # % des suffrages en dessous duquel un candidat n'est pas comparé

# Nom affiché, clé de reconnaissance dans la colonne « Sondeur » (comparée
# sans accents, sans espaces ni ponctuation : « Opinion Way », « Cluster 17 »,
# « Ifop-Fiducial », « Ipsos Sopra Steria », « Toluna Harris Interactive »...).
INSTITUTS = [
    ("Ifop", "ifop"),
    ("Ipsos", "ipsos"),
    ("OpinionWay", "opinionway"),
    ("Harris Interactive", "harris"),
    ("Elabe", "elabe"),
    ("Odoxa", "odoxa"),
    ("Cluster17", "cluster17"),
]


@dataclass
class Scrutin:
    titre: str        # « Présidentielle »
    detail: str       # « 2017 · 1er tour »
    jour: date        # jour du vote
    page: str         # page Wikipédia
    section: str      # extrait du chemin de titres menant au tableau

    @property
    def nom(self) -> str:
        return f"{self.titre} {self.detail}"


# Un scrutin à ajouter (présidentielle 2027...) : une ligne de plus, dès que
# la page Wikipédia a sa ligne « Résultats ». `section` désigne le tableau :
# le premier tableau doté d'une ligne « Résultats » dont le chemin de titres
# (« h2 > h3 > h4 ») contient ce texte.
SCRUTINS = [
    Scrutin("Présidentielle", "2017 · 1er tour", date(2017, 4, 23),
            "Liste de sondages sur l'élection présidentielle française de 2017",
            "concernant le premier tour"),
    Scrutin("Présidentielle", "2017 · 2d tour", date(2017, 5, 7),
            "Liste de sondages sur l'élection présidentielle française de 2017",
            "concernant le second tour"),
    Scrutin("Législatives", "2017 · 1er tour", date(2017, 6, 11),
            "Liste de sondages sur les élections législatives françaises de 2017",
            "Premier tour"),
    Scrutin("Européennes", "2019", date(2019, 5, 26),
            "Sondages sur les élections européennes de 2019",
            "France > Sondages en % des voix"),
    Scrutin("Présidentielle", "2022 · 1er tour", date(2022, 4, 10),
            "Liste de sondages sur l'élection présidentielle française de 2022",
            "concernant le premier tour"),
    Scrutin("Présidentielle", "2022 · 2d tour", date(2022, 4, 24),
            "Liste de sondages sur l'élection présidentielle française de 2022",
            "concernant le second tour"),
    Scrutin("Législatives", "2022 · 1er tour", date(2022, 6, 12),
            "Liste de sondages sur les élections législatives françaises de 2022",
            "Premier tour"),
    Scrutin("Européennes", "2024", date(2024, 6, 9),
            "Élections européennes de 2024 en France",
            "Intentions de vote"),
    Scrutin("Législatives", "2024 · 1er tour", date(2024, 6, 30),
            "Liste de sondages sur les élections législatives françaises de 2024",
            "Sondages nationaux"),
]

# Colonnes qui ne sont pas un candidat : repérées par leur en-tête (à
# n'importe quel niveau), comparé sans accents ni majuscules.
COLONNES_HORS_CANDIDATS = ("sondeur", "institut", "source", "date", "echantillon",
                           "panel", "abstention", "indecis", "autres", "divers",
                           "blancs", "nuls", "ecart")
EN_TETES_FOURRE_TOUT = {"div"}   # « DIV » (divers) des législatives 2022

# Jeu de couleurs « encre » du site : exactement celui de Sondpres/sondages_2027.py
# et de assets/css/style.css (cf. README).
INK = "#0b0b0b"
INK_SECOND = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
SURFACE = "#fcfcfb"
# Divergente : bleu = moins d'erreur que la moyenne, orange = plus. Pas de
# rouge : sur un site de sondages, rouge et bleu se liraient gauche et droite.
# Pôles de la palette du skill dataviz, milieu gris neutre.
MIEUX = "#2a78d6"
PIRE = "#eb6834"
NEUTRE = "#f0efec"

MOIS = {
    "janvier": 1, "janv": 1, "février": 2, "fevrier": 2, "févr": 2, "fevr": 2,
    "mars": 3, "avril": 4, "avr": 4, "mai": 5, "juin": 6, "juillet": 7,
    "juil": 7, "août": 8, "aout": 8, "septembre": 9, "sept": 9, "octobre": 10,
    "oct": 10, "novembre": 11, "nov": 11, "décembre": 12, "decembre": 12,
    "déc": 12, "dec": 12,
}
_MOIS_RE = re.compile(r"\b(" + "|".join(sorted(MOIS, key=len, reverse=True)) + r")\b")


# ---------------------------------------------------------------------------
# 1. Récupération des pages
# ---------------------------------------------------------------------------
def fetch_page(titre: str, refresh: bool) -> str:
    """HTML rendu d'une page Wikipédia, conservé dans .cache/ : les scrutins
    passés ne bougent plus, inutile de solliciter l'API à chaque exécution."""
    fichier = CACHE / (re.sub(r"\W+", "_", titre) + ".html")
    if fichier.exists() and not refresh:
        return fichier.read_text(encoding="utf-8")
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    for _ in range(6):
        r = s.get(API_URL, params={
            "action": "parse", "page": titre, "prop": "text", "redirects": 1,
            "format": "json", "formatversion": 2, "disablelimitreport": 1,
        }, timeout=60)
        if r.status_code != 429:      # trop de requêtes : l'API demande d'attendre
            break
        time.sleep(int(r.headers.get("retry-after") or 20))
    r.raise_for_status()
    html = r.json()["parse"]["text"]
    CACHE.mkdir(exist_ok=True)
    fichier.write_text(html, encoding="utf-8")
    time.sleep(3)                     # politesse envers l'API entre deux pages
    return html


# ---------------------------------------------------------------------------
# 2. Lecture des tableaux
# ---------------------------------------------------------------------------
def _plat(s: str) -> str:
    """Minuscules sans accents ni espaces insécables, pour comparer des libellés."""
    s = unicodedata.normalize("NFD", re.sub(r"\s+", " ", s))
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


@dataclass(eq=False)
class Cellule:
    texte: str
    entete: bool      # <th>
    c0: int           # première colonne couverte
    c1: int           # colonne suivant la dernière couverte


def _texte(cell) -> str:
    """Texte visible d'une cellule, sans appels de note ni éléments masqués."""
    cell = copy.deepcopy(cell)
    for el in cell.xpath(".//sup[contains(@class,'reference')] | .//style"
                         " | .//*[contains(@style,'display:none')]"):
        el.drop_tree()
    for br in cell.iter("br"):        # « EELV<br>AEI-RPS » -> « EELV AEI-RPS »
        br.tail = " " + (br.tail or "")
    t = re.sub(r"\[[^\]]*\]", "", cell.text_content())
    return re.sub(r"\s+", " ", t.replace("\xa0", " ")).strip()


def grille(table) -> list[list[Cellule]]:
    """Le tableau déplié en grille : grille[ligne][colonne] est la cellule qui
    couvre cette case. Une cellule étalée (colspan / rowspan) est le MÊME
    objet dans toutes les cases qu'elle couvre : c'est ce qui permet de savoir
    qu'un institut a donné un seul chiffre pour deux colonnes, là où
    `pandas.read_html` recopierait le chiffre dans chacune."""
    def span(cell, attr):
        m = re.match(r"\d+", cell.get(attr) or "1")
        return max(1, int(m.group())) if m else 1

    occupe: dict[tuple[int, int], Cellule] = {}
    lignes = table.xpath("./tr | ./thead/tr | ./tbody/tr | ./tfoot/tr")
    for r, tr in enumerate(lignes):
        c = 0
        for cell in tr.xpath("./td | ./th"):
            while (r, c) in occupe:
                c += 1
            cs, rs = span(cell, "colspan"), span(cell, "rowspan")
            obj = Cellule(_texte(cell), cell.tag == "th", c, c + cs)
            for dr in range(rs):
                for dc in range(cs):
                    occupe[(r + dr, c + dc)] = obj
            c += cs
    n_col = 1 + max((c for _, c in occupe), default=-1)
    return [[occupe.get((r, c)) for c in range(n_col)] for r in range(len(lignes))]


def trouver_tableau(html: str, section: str):
    """Premier tableau doté d'une ligne « Résultats » sous un titre dont le
    chemin (« h2 > h3 > h4 ») contient `section`."""
    tree = LH.fromstring(html)
    chemin: dict[int, str] = {}
    for el in tree.iter("h2", "h3", "h4", "h5", "table"):
        if el.tag != "table":
            niveau = int(el.tag[1])
            chemin = {k: v for k, v in chemin.items() if k < niveau}
            chemin[niveau] = el.text_content().split("[")[0].strip()
            continue
        if "wikitable" not in (el.get("class") or ""):
            continue
        if _plat(section) not in _plat(" > ".join(chemin[k] for k in sorted(chemin))):
            continue
        g = grille(el)
        if any(ligne and ligne[0] and ligne[0].texte.startswith("Résultat") for ligne in g):
            return g
    raise RuntimeError(f"aucun tableau avec résultats sous « {section} »")


def valeur(texte: str) -> float | None:
    """Pourcentage d'une cellule : « 29 % » -> 29, « 25,66 % (26,16 %) » -> 25,66
    (premier chiffre : le décompte officiel), « <0,5 % » -> 0,25, « — » -> None."""
    t = texte.replace(" ", "")
    m = re.match(r"<(\d+(?:[.,]\d+)?)", t)
    if m:
        return float(m.group(1).replace(",", ".")) / 2
    m = re.match(r"(\d+(?:[.,]\d+)?)%?", t)
    return float(m.group(1).replace(",", ".")) if m else None


def fin_terrain(texte: str, annee_par_defaut: int) -> date | None:
    """Dernier jour du terrain : « 30 mai - 1er juin » -> 1er juin,
    « 18-21 avr. » -> 21 avril, « 21 - 24 mai 2019 » -> 24 mai 2019."""
    t = _plat(texte).replace("1er", "1")
    annees = re.findall(r"\b(20\d\d)\b", t)
    t = re.sub(r"\b20\d\d\b", " ", t)
    mois = list(_MOIS_RE.finditer(t))
    if not mois:
        return None
    dernier = mois[-1]
    debut = mois[-2].end() if len(mois) > 1 else 0
    jours = re.findall(r"\d{1,2}", t[debut:dernier.start()])
    if not jours:
        return None
    annee = int(annees[-1]) if annees else annee_par_defaut
    try:
        return date(annee, MOIS[dernier.group(1)], int(jours[-1]))
    except ValueError:
        return None


def institut_de(sondeur: str) -> str | None:
    cle = re.sub(r"[^a-z0-9]", "", _plat(sondeur))
    for nom, motif in INSTITUTS:
        if motif in cle:
            return nom
    return None


# ---------------------------------------------------------------------------
# 3. Confrontation sondages / résultat
# ---------------------------------------------------------------------------
@dataclass
class Unite:
    libelle: str
    colonnes: tuple[int, ...]
    resultat: float


@dataclass
class Confrontation:
    scrutin: Scrutin
    unites: list[Unite]
    sondages: dict[str, dict]   # institut -> {"fin": date, "lignes": n, "valeurs": [..]}
    erreurs: dict[str, float]   # institut -> erreur quadratique moyenne, en points

    @property
    def erreur_moyenne(self) -> float:
        return float(np.mean(list(self.erreurs.values())))


def _somme(ligne: list[Cellule], colonnes) -> float | None:
    """Somme des cellules (distinctes) d'une ligne sur un groupe de colonnes ;
    None si aucune ne porte de chiffre."""
    vus, total, trouve = set(), 0.0, False
    for c in colonnes:
        cell = ligne[c]
        if cell is None or id(cell) in vus:
            continue
        vus.add(id(cell))
        v = valeur(cell.texte)
        if v is not None:
            total, trouve = total + v, True
    return total if trouve else None


def confronter(scrutin: Scrutin, g: list[list[Cellule]]) -> Confrontation:
    n_col = len(g[0])
    # En-tête : les lignes de tête dont la première case est un <th>. Seules
    # celles du haut : plus bas, une ligne <th> est un événement de campagne
    # étalé sur toute la largeur (« Dissolution de l'Assemblée »...).
    n_entete = next(i for i, l in enumerate(g) if not (l[0] and l[0].entete))
    entetes = g[:n_entete]
    est_entete = [i < n_entete or bool(l[0] and l[0].entete) for i, l in enumerate(g)]

    # colonnes de candidats : aucune de leurs cellules d'en-tête n'est un
    # sondeur, une date, l'abstention, « Autres »...
    def libelles(c):
        vus, out = set(), []
        for l in entetes:
            cell = l[c]
            if cell is not None and id(cell) not in vus and cell.texte:
                vus.add(id(cell))
                out.append(cell.texte)
        return out

    candidats = [c for c in range(n_col)
                 if libelles(c)
                 and not any(m in _plat(x) for x in libelles(c) for m in COLONNES_HORS_CANDIDATS)
                 and not any(_plat(x) in EN_TETES_FOURRE_TOUT for x in libelles(c))]

    donnees = [l for l, e in zip(g, est_entete) if not e and l[0] and l[0].texte]
    resultat = next(l for l in donnees if l[0].texte.startswith("Résultat"))
    col_date = next(c for c in range(n_col) if any("date" in _plat(x) for x in libelles(c)))

    # dernier sondage de chaque institut dans la fenêtre
    debut = scrutin.jour - timedelta(days=JOURS_MAX)
    par_institut: dict[str, list[tuple[date, list[Cellule]]]] = {}
    for l in donnees:
        inst = institut_de(l[0].texte)
        fin = fin_terrain(l[col_date].texte, scrutin.jour.year) if l[col_date] else None
        if inst and fin and debut <= fin <= scrutin.jour:
            par_institut.setdefault(inst, []).append((fin, l))
    derniers = {}
    for inst, lst in par_institut.items():
        fin = max(f for f, _ in lst)
        derniers[inst] = (fin, [l for f, l in lst if f == fin])

    # Unités de comparaison : on part d'une unité par colonne de candidat et
    # on fusionne les colonnes qu'une même cellule couvre — dans la ligne des
    # résultats ou dans l'un des sondages retenus (union-find).
    parent = {c: c for c in candidats}

    def racine(c):
        while parent[c] != c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c

    for ligne in [resultat] + [l for _, ls in derniers.values() for l in ls]:
        for cell in {id(x): x for x in ligne if x is not None}.values():
            couvre = [c for c in range(cell.c0, cell.c1) if c in parent]
            for c in couvre[1:]:
                parent[racine(c)] = racine(couvre[0])
    groupes: dict[int, list[int]] = {}
    for c in candidats:
        groupes.setdefault(racine(c), []).append(c)

    def libelle(cols):
        # en-tête qui coiffe exactement le groupe (« NFP », « RN et alliés »),
        # sinon les en-têtes des colonnes mis bout à bout (« DVD + LR »)
        for l in reversed(entetes):
            cell = l[cols[0]]
            if cell and cell.texte and (cell.c0, cell.c1) == (cols[0], cols[-1] + 1):
                return cell.texte
        return " + ".join(libelles(c)[-1] for c in cols)

    unites = []
    for cols in sorted(groupes.values()):
        r = _somme(resultat, cols)
        if r is not None and r >= SEUIL_VOIX:
            unites.append(Unite(libelle(cols), tuple(cols), r))

    sondages = {}
    for inst, (fin, lignes) in derniers.items():
        vals = []
        for u in unites:
            xs = [v for v in (_somme(l, u.colonnes) for l in lignes) if v is not None]
            vals.append(float(np.mean(xs)) if xs else None)
        sondages[inst] = {"fin": fin, "lignes": len(lignes), "valeurs": vals}

    # une unité absente d'un sondage sort de la comparaison pour tous
    manquantes = {i for s in sondages.values() for i, v in enumerate(s["valeurs"]) if v is None}
    if manquantes:
        qui = {u.libelle: [k for k, s in sondages.items() if s["valeurs"][i] is None]
               for i, u in enumerate(unites) if i in manquantes}
        print(f"  ⚠ {scrutin.nom} : unités retirées faute d'être publiées par "
              f"tous : {qui}", file=sys.stderr)
    garde = [i for i in range(len(unites)) if i not in manquantes]
    unites = [unites[i] for i in garde]
    for s in sondages.values():
        s["valeurs"] = [s["valeurs"][i] for i in garde]

    erreurs = {inst: math.sqrt(np.mean([(v - u.resultat) ** 2
                                        for v, u in zip(s["valeurs"], unites)]))
               for inst, s in sondages.items()}
    return Confrontation(scrutin, unites, sondages, erreurs)


def scores(confs: list[Confrontation]) -> dict[str, dict]:
    """Écart de chaque institut à l'erreur moyenne de chaque scrutin, et leur
    moyenne : le score final (négatif = moins d'erreur que la moyenne)."""
    out = {}
    for nom, _ in INSTITUTS:
        ecarts = [c.erreurs[nom] - c.erreur_moyenne for c in confs if nom in c.erreurs]
        if ecarts:
            out[nom] = {"ecarts": ecarts, "score": float(np.mean(ecarts)),
                        "n": len(ecarts)}
    return out


# ---------------------------------------------------------------------------
# 4. Graphiques
# ---------------------------------------------------------------------------
ABREGE = {"Présidentielle": "Présid.", "Législatives": "Législ.", "Européennes": "Europ."}


def _pts(v: float, unite: bool = True, signe: bool = True) -> str:
    """-0.3 -> « −0,30 pt » : virgule décimale, vrai signe moins."""
    s = f"{abs(v):.2f}".replace(".", ",")
    if signe:
        s = ("−" if v < -0.005 else "+" if v > 0.005 else "") + s
    if unite:
        s += " pt" if abs(v) < 2 else " pts"
    return s


def _style():
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 11,
        "axes.edgecolor": MUTED, "axes.labelcolor": INK_SECOND,
        "xtick.color": MUTED, "ytick.color": MUTED,
        "text.color": INK, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    })
    return plt


def graphique_classement(sc: dict, confs: list[Confrontation], fichier: Path):
    """Le score de chaque institut (gros point, relié au zéro), et l'écart de
    chacun de ses scrutins (petits cercles) : la dispersion dit combien le
    classement est fragile, et d'où vient un mauvais score."""
    plt = _style()
    import matplotlib.ticker as mticker
    from matplotlib.lines import Line2D

    ordre = sorted(sc, key=lambda k: sc[k]["score"])
    n = len(ordre)
    fig = plt.figure(figsize=(12, 6.6))
    ax = fig.add_axes([0.205, 0.16, 0.655, 0.64])

    tous = [e for k in ordre for e in sc[k]["ecarts"]]
    lim = max(1.0, math.ceil(max(abs(e) for e in tous) * 2 + 0.6) / 2)
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-0.6, n - 0.4)

    for i, nom in enumerate(ordre):
        y = n - 1 - i
        s = sc[nom]
        couleur = MIEUX if s["score"] < 0 else PIRE
        ax.axhline(y, color=GRID, lw=0.8, zorder=1)
        ax.scatter(s["ecarts"], [y] * s["n"], s=46, facecolor=SURFACE,
                   edgecolor=MUTED, linewidths=1.2, zorder=3)
        ax.plot([0, s["score"]], [y, y], color=couleur, lw=2.6,
                solid_capstyle="round", zorder=4)
        ax.scatter([s["score"]], [y], s=170, color=couleur, edgecolor=SURFACE,
                   linewidths=2, zorder=5)

        # nom à gauche, nombre de scrutins dessous ; score à droite
        ax.annotate(nom, (-0.03, y), xycoords=("axes fraction", "data"),
                    xytext=(0, 1.5), textcoords="offset points",
                    ha="right", va="bottom", fontsize=13, fontweight="bold", color=INK)
        ax.annotate(f"{s['n']} scrutins", (-0.03, y), xycoords=("axes fraction", "data"),
                    xytext=(0, -2.5), textcoords="offset points",
                    ha="right", va="top", fontsize=9.5, color=MUTED)
        ax.annotate(_pts(s["score"]), (1.03, y), xycoords=("axes fraction", "data"),
                    ha="left", va="center", fontsize=13, fontweight="bold", color=INK)

        # les scrutins qui pèsent le plus sur le score sont nommés
        scrutins_couverts = [c.scrutin for c in confs if nom in c.erreurs]
        for e, scr in zip(s["ecarts"], scrutins_couverts):
            if abs(e) >= 1.0:
                ax.annotate(f"{ABREGE[scr.titre]} {scr.detail}", (e, y),
                            xytext=(0, 8), textcoords="offset points",
                            ha="center", va="bottom", fontsize=8.5, color=INK_SECOND)

    ax.axvline(0, color=INK, lw=1, zorder=2)
    ax.annotate("moyenne des instituts", (0, 1), xycoords=("data", "axes fraction"),
                xytext=(0, 20), textcoords="offset points",
                ha="center", va="bottom", fontsize=9.5, color=INK)
    ax.annotate("←  se trompe moins", (0, 1), xycoords=("data", "axes fraction"),
                xytext=(-10, 5), textcoords="offset points",
                ha="right", va="bottom", fontsize=9.5, color=INK_SECOND)
    ax.annotate("se trompe plus  →", (0, 1), xycoords=("data", "axes fraction"),
                xytext=(10, 5), textcoords="offset points",
                ha="left", va="bottom", fontsize=9.5, color=INK_SECOND)
    ax.annotate("score", (1.03, 1), xycoords="axes fraction",
                xytext=(0, 5), textcoords="offset points",
                ha="left", va="bottom", fontsize=9.5, color=INK_SECOND)

    ax.xaxis.set_major_locator(mticker.MultipleLocator(1))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(
        lambda v, _: "0" if abs(v) < 1e-9 else _pts(v).replace(",00", "")))
    ax.grid(axis="x", color=GRID, lw=0.8, zorder=0)
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0, labelsize=10)

    fig.text(0.03, 0.935, "Quel institut se trompe le moins ?", fontsize=17,
             fontweight="bold", color=INK)
    fig.text(0.03, 0.892, "Erreur de l'institut moins l'erreur moyenne des instituts sur "
             f"le même scrutin, en points — scrutins nationaux depuis {SCRUTINS[0].jour.year}",
             fontsize=11, color=INK_SECOND)
    poignees = [
        Line2D([], [], ls="", marker="o", ms=7, mfc=SURFACE, mec=MUTED, mew=1.2,
               label="un scrutin"),
        Line2D([], [], ls="", marker="o", ms=11, mfc=INK_SECOND, mec=SURFACE, mew=1.5,
               label="moyenne des scrutins : le score (bleu : mieux que la moyenne, "
                     "orange : moins bien)"),
    ]
    fig.legend(handles=poignees, loc="lower left", bbox_to_anchor=(0.2, 0.025), ncol=2,
               frameon=False, fontsize=9.5, labelcolor=INK_SECOND, handletextpad=0.4,
               columnspacing=2.2)
    fig.text(0.97, 0.04, "Wikipédia", fontsize=8, color=MUTED, ha="right")
    fig.savefig(fichier, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"✓ {fichier.name}")


def graphique_scrutins(sc: dict, confs: list[Confrontation], fichier: Path):
    """Tableau institut x scrutin : l'erreur en points dans la case, sa
    couleur dit si elle est en dessous (bleu) ou au-dessus (orange) de
    l'erreur moyenne des instituts sur ce scrutin. Dernière ligne : cette
    erreur moyenne, qui dit la difficulté du scrutin."""
    plt = _style()
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.patches import FancyBboxPatch

    # bleu ramp 300 / orange de même clarté OKLab (L ~ 0.72) : le texte noir
    # garde un contraste > 7:1 sur les deux pôles comme sur le gris neutre.
    cmap = LinearSegmentedColormap.from_list("ecart", ["#6da7ec", NEUTRE, "#e78662"])
    SATURE = 1.0   # points : au-delà, la couleur ne fonce plus

    ordre = sorted(sc, key=lambda k: sc[k]["score"])
    n = len(ordre)
    # un peu d'air entre deux années de scrutin
    xs, annees, groupe = [], {}, 0
    for j, c in enumerate(confs):
        an = c.scrutin.jour.year
        if annees and an not in annees:
            groupe += 1
        xs.append(j + 0.3 * groupe)
        annees.setdefault(an, []).append(xs[-1])
    y_moy = -(n + 0.3)

    fig = plt.figure(figsize=(12, 6.6))
    ax = fig.add_axes([0.175, 0.15, 0.805, 0.59])
    ax.set_xlim(-0.5, xs[-1] + 0.5)
    ax.set_ylim(y_moy - 0.5, 0.5)
    ax.axis("off")

    # rayon des coins en unités de données x, corrigé de l'étirement des cases
    largeur_px = ax.get_position().width * fig.get_figwidth() * 150
    hauteur_px = ax.get_position().height * fig.get_figheight() * 150
    px_x = (xs[-1] + 1) / largeur_px                 # unités x par pixel
    px_y = (n + 1.3) / hauteur_px                    # unités y par pixel
    gx, gy = 3 * px_x, 3 * px_y                      # 3 px d'espace entre cases

    for j, c in enumerate(confs):
        x = xs[j]
        for i, nom in enumerate(ordre):
            y = -i
            if nom not in c.erreurs:
                ax.text(x, y, "—", ha="center", va="center", fontsize=11, color=MUTED)
                continue
            e = c.erreurs[nom]
            t = 0.5 + 0.5 * max(-1, min(1, (e - c.erreur_moyenne) / SATURE))
            ax.add_patch(FancyBboxPatch(
                (x - 0.5 + gx, y - 0.5 + gy), 1 - 2 * gx, 1 - 2 * gy,
                boxstyle=f"round,pad=0,rounding_size={6 * px_x}",
                mutation_aspect=px_y / px_x, facecolor=cmap(t), edgecolor="none"))
            ax.text(x, y, f"{e:.1f}".replace(".", ","), ha="center", va="center",
                    fontsize=12, color=INK)
        ax.text(x, y_moy, f"{c.erreur_moyenne:.1f}".replace(".", ","), ha="center",
                va="center", fontsize=12, color=INK_SECOND, fontweight="bold")
        # en-tête de colonne : type de scrutin, tour (l'année coiffe le groupe)
        tour = c.scrutin.detail.partition(" · ")[2]
        ax.text(x, 0.6, tour, ha="center", va="bottom", fontsize=9, color=MUTED)
        ax.text(x, 0.9, c.scrutin.titre, ha="center", va="bottom", fontsize=9.5,
                color=INK_SECOND)

    for an, cols in annees.items():
        ax.text(np.mean(cols), 1.42, str(an), ha="center", va="bottom", fontsize=12,
                fontweight="bold", color=INK)
        ax.plot([cols[0] - 0.47, cols[-1] + 0.47], [1.33, 1.33], color=GRID, lw=1,
                clip_on=False, solid_capstyle="butt")

    for i, nom in enumerate(ordre):
        ax.annotate(nom, (-0.015, -i), xycoords=("axes fraction", "data"), ha="right",
                    va="center", fontsize=12, fontweight="bold", color=INK)
    ax.annotate("Moyenne", (-0.015, y_moy), xycoords=("axes fraction", "data"),
                ha="right", va="center", fontsize=12, color=INK_SECOND)
    ax.plot([-0.5, xs[-1] + 0.5], [y_moy + 0.62] * 2, color=GRID, lw=1, clip_on=False)

    # légende de la couleur : petite échelle continue sous le tableau
    leg = fig.add_axes([0.4, 0.06, 0.3, 0.022])
    leg.imshow(np.linspace(0, 1, 256)[None, :], aspect="auto", cmap=cmap)
    leg.set_xticks([0, 127.5, 255], [f"−{SATURE:g} pt", "moyenne du scrutin",
                                     f"+{SATURE:g} pt"])
    leg.set_yticks([])
    leg.tick_params(length=0, labelsize=9, colors=INK_SECOND)
    for spine in leg.spines.values():
        spine.set_visible(False)
    fig.text(0.39, 0.071, "moins d'erreur", ha="right", va="center",
             fontsize=9.5, color=INK_SECOND)
    fig.text(0.71, 0.071, "plus d'erreur", ha="left", va="center",
             fontsize=9.5, color=INK_SECOND)

    fig.text(0.03, 0.935, "Scrutin par scrutin", fontsize=17, fontweight="bold", color=INK)
    fig.text(0.03, 0.892, "Erreur du dernier sondage de chaque institut, en points "
             "(moyenne quadratique des écarts sur les candidats au-dessus de "
             f"{SEUIL_VOIX:g} %)".replace(".", ","), fontsize=11, color=INK_SECOND)
    fig.text(0.97, 0.025, "Wikipédia", fontsize=8, color=MUTED, ha="right")
    fig.savefig(fichier, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"✓ {fichier.name}")


def ecrire_csv(confs: list[Confrontation], fichier: Path):
    """Les sondages retenus, une ligne par institut et par candidat : de quoi
    refaire le calcul à la main."""
    with open(fichier, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["scrutin", "jour_du_vote", "institut", "fin_du_terrain",
                    "candidat_ou_liste", "sondage", "resultat", "ecart"])
        for c in confs:
            for nom, _ in INSTITUTS:
                if nom not in c.sondages:
                    continue
                s = c.sondages[nom]
                for u, v in zip(c.unites, s["valeurs"]):
                    w.writerow([c.scrutin.nom, c.scrutin.jour.isoformat(), nom,
                                s["fin"].isoformat(), u.libelle, f"{v:g}",
                                f"{u.resultat:g}", f"{v - u.resultat:+.2f}"])
    print(f"✓ {fichier.name}")


# ---------------------------------------------------------------------------
# 5. Orchestration
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true",
                    help="retélécharger les pages Wikipédia au lieu de lire .cache/")
    args = ap.parse_args()
    matplotlib.use("Agg")

    confs = []
    for s in SCRUTINS:
        g = trouver_tableau(fetch_page(s.page, args.refresh), s.section)
        c = confronter(s, g)
        confs.append(c)
        print(f"{s.nom:34s} {len(c.unites):2d} candidats, erreur moyenne "
              f"{c.erreur_moyenne:.2f} pt — "
              + ", ".join(f"{k} {v:.2f}" for k, v in sorted(c.erreurs.items(),
                                                            key=lambda kv: kv[1])))
        absents = [k for k, _ in INSTITUTS if k not in c.erreurs]
        if absents:
            print(f"{'':34s} sans sondage dans les {JOURS_MAX} derniers jours : "
                  + ", ".join(absents))

    sc = scores(confs)
    print("\nScore (écart moyen à l'erreur moyenne du scrutin) :")
    for k in sorted(sc, key=lambda k: sc[k]["score"]):
        print(f"  {k:19s} {sc[k]['score']:+.3f} pt sur {sc[k]['n']} scrutins")

    graphique_classement(sc, confs, ICI / "instituts_classement.png")
    graphique_scrutins(sc, confs, ICI / "instituts_scrutins.png")
    ecrire_csv(confs, ICI / "derniers_sondages.csv")


if __name__ == "__main__":
    main()

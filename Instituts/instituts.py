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
    instituts_classement*.png  le score de chaque institut, avec le détail par scrutin
    instituts_scrutins*.png    l'erreur de chaque institut, scrutin par scrutin
                               (sans suffixe : dernier sondage ; _1mois, _3mois)
    sondages_retenus.csv       les sondages retenus, candidat par candidat
    index.html                 l'exemple de calcul, entre ses deux marqueurs

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

JOURS_MAX = 14       # le sondage retenu s'achève à 14 jours au plus de J − h
SEUIL_VOIX = 1.0     # % des suffrages en dessous duquel un candidat n'est pas comparé
SEUIL_PRINCIPAL = 5.0  # % : un sondage qui ne teste pas tous les candidats au-dessus
                       # n'est pas une hypothèse du scrutin réel (Valls sans Hamon...)
MIN_INSTITUTS = 3    # en dessous, la référence d'un scrutin ne veut rien dire

# Horizons : le sondage retenu est celui dont la fin de terrain est la plus
# proche de J − h, à JOURS_MAX jours près (pour h = 0 : le dernier avant le
# vote). Les fenêtres des trois horizons sont disjointes.
# (jours, suffixe des fichiers, titre des graphiques)
HORIZONS = [
    (0, "", "Quel institut se trompe le moins ?"),
    (30, "_1mois", "Un mois avant le vote"),
    (90, "_3mois", "Trois mois avant le vote"),
]

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
    section: str      # extrait du chemin de titres menant aux tableaux
    depuis: date | None = None   # sondages antérieurs ignorés (campagne pas encore ouverte)

    @property
    def nom(self) -> str:
        return f"{self.titre} {self.detail}"


# Un scrutin à ajouter (présidentielle 2027...) : une ligne de plus, dès que
# la page Wikipédia a sa ligne « Résultats ». `section` désigne les tableaux :
# tous ceux dont le chemin de titres (« h2 > h3 > h4 ») contient ce texte ; le
# premier doté d'une ligne « Résultats » donne le résultat, les autres (mois
# précédents, autres hypothèses) des sondages plus anciens.
SCRUTINS = [
    Scrutin("Présidentielle", "2017 · 1er tour", date(2017, 4, 23),
            "Liste de sondages sur l'élection présidentielle française de 2017",
            "concernant le premier tour"),
    Scrutin("Présidentielle", "2017 · 2d tour", date(2017, 5, 7),
            "Liste de sondages sur l'élection présidentielle française de 2017",
            "concernant le second tour"),
    Scrutin("Législatives", "2017 · 1er tour", date(2017, 6, 11),
            "Liste de sondages sur les élections législatives françaises de 2017",
            "Sondages > Premier tour"),
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
            "nationale > Premier tour"),
    Scrutin("Européennes", "2024", date(2024, 6, 9),
            "Sondages sur les élections européennes de 2024",
            "France > Sondages réalisés"),
    # législatives anticipées : convoquées le 9 juin, les sondages d'avant
    # portaient sur un scrutin hypothétique, avec d'autres alliances
    Scrutin("Législatives", "2024 · 1er tour", date(2024, 6, 30),
            "Liste de sondages sur les élections législatives françaises de 2024",
            "Sondages nationaux", depuis=date(2024, 6, 10)),
]

# Tableaux d'une section qui ne sont pas des intentions de vote
EXCLURE_TABLEAUX = ("report", "sous-echantillon", "critere", "projection")

# Colonnes qui ne sont pas un candidat : repérées par leur en-tête (à
# n'importe quel niveau), comparé sans accents ni majuscules.
COLONNES_HORS_CANDIDATS = ("sondeur", "institut", "source", "date", "echan",  # « Échan- tillon »
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
    """Texte visible d'une cellule, sans appels de note ni éléments masqués.
    Les dates écrites <time datetime="2016-12-08">8 décembre</time> récupèrent
    leur année, absente du texte visible."""
    cell = copy.deepcopy(cell)
    for el in cell.xpath(".//sup[contains(@class,'reference')] | .//style"
                         " | .//*[contains(@style,'display:none')]"):
        el.drop_tree()
    for br in cell.iter("br"):        # « EELV<br>AEI-RPS » -> « EELV AEI-RPS »
        br.tail = " " + (br.tail or "")
    for t in cell.iter("time"):
        dt = t.get("datetime") or ""
        if re.match(r"\d{4}-\d{2}", dt) and dt[:4] not in t.text_content():
            t.tail = f" {dt[:4]}" + (t.tail or "")
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


@dataclass
class Tableau:
    g: list[list[Cellule]]
    annee: int | None     # année des dates sans année (légende « Décembre 2021 »...)
    mois: int | None      # mois des dates sans mois (légende « Mars 2017 »)

    @property
    def n_entete(self) -> int:
        """Lignes d'en-tête : celles du haut dont la première case est un <th>.
        Plus bas, une ligne <th> est un événement de campagne étalé sur toute
        la largeur (« Dissolution de l'Assemblée »...)."""
        return next((i for i, l in enumerate(self.g) if not (l[0] and l[0].entete)),
                    len(self.g))

    def libelles(self, c: int) -> list[str]:
        """En-têtes de la colonne c, du plus général au plus précis."""
        vus, out = set(), []
        for l in self.g[:self.n_entete]:
            cell = l[c]
            if cell is not None and id(cell) not in vus and cell.texte:
                vus.add(id(cell))
                out.append(cell.texte)
        return out

    def candidats(self) -> list[int]:
        """Colonnes de candidats : aucun de leurs en-têtes n'est un sondeur, une
        date, l'abstention, « Autres »..."""
        return [c for c in range(len(self.g[0]))
                if (labs := self.libelles(c))
                and not any(m in _plat(x) for x in labs for m in COLONNES_HORS_CANDIDATS)
                and not any(_plat(x) in EN_TETES_FOURRE_TOUT for x in labs)]

    def col_date(self) -> int | None:
        return next((c for c in range(len(self.g[0]))
                     if any("date" in _plat(x) for x in self.libelles(c))), None)

    def donnees(self) -> list[list[Cellule]]:
        return [l for l in self.g[self.n_entete:]
                if l[0] and l[0].texte and not l[0].entete]


def tableaux(html: str, section: str) -> list[Tableau]:
    """Les tableaux de sondages dont le chemin de titres (« h2 > h3 > h4 »)
    contient `section`, dans l'ordre de la page, avec l'année et le mois que
    donnent leur légende ou l'encadré repliable qui les contient
    (« Sondages de 2016 »)."""
    tree = LH.fromstring(html)
    chemin: dict[int, str] = {}
    annee_encadre = None
    out = []
    for el in tree.iter("h2", "h3", "h4", "h5", "div", "table"):
        if el.tag == "div":
            if "NavHead" in (el.get("class") or ""):
                m = re.search(r"\b(20\d\d)\b", el.text_content())
                annee_encadre = int(m.group(1)) if m else None
            continue
        if el.tag != "table":
            niveau = int(el.tag[1])
            chemin = {k: v for k, v in chemin.items() if k < niveau}
            chemin[niveau] = el.text_content().split("[")[0].strip()
            annee_encadre = None
            continue
        if "wikitable" not in (el.get("class") or ""):
            continue
        cap = el.find(".//caption")
        legende = _plat(cap.text_content()) if cap is not None else ""
        lieu = _plat(" > ".join(chemin[k] for k in sorted(chemin)))
        if _plat(section) not in lieu or any(x in lieu + " " + legende
                                              for x in EXCLURE_TABLEAUX):
            continue
        ans = re.findall(r"\b(20\d\d)\b", legende)
        mois = {MOIS[m.group(1)] for m in _MOIS_RE.finditer(legende)}
        out.append(Tableau(grille(el), int(ans[-1]) if ans else annee_encadre,
                           mois.pop() if len(mois) == 1 else None))
    return out


def valeur(texte: str) -> float | None:
    """Pourcentage d'une cellule : « 29 % » -> 29, « 25,66 % (26,16 %) » -> 25,66
    (premier chiffre : le décompte officiel), « <0,5 % » -> 0,25, « — » -> None."""
    t = texte.replace(" ", "")
    m = re.match(r"<(\d+(?:[.,]\d+)?)", t)
    if m:
        return float(m.group(1).replace(",", ".")) / 2
    m = re.match(r"(\d+(?:[.,]\d+)?)%?", t)
    return float(m.group(1).replace(",", ".")) if m else None


def fin_terrain(texte: str, annee_par_defaut: int, mois_par_defaut: int | None = None
                ) -> date | None:
    """Dernier jour du terrain : « 30 mai - 1er juin » -> 1er juin,
    « 18-21 avr. » -> 21 avril, « 21 - 24 mai 2019 » -> 24 mai 2019 ; « 2-3 »
    dans un tableau légendé « Mars 2017 » -> 3 mars 2017."""
    t = _plat(texte).replace("1er", "1")
    annees = re.findall(r"\b(20\d\d)\b", t)
    t = re.sub(r"\b20\d\d\b", " ", t)
    mois = list(_MOIS_RE.finditer(t))
    if mois:
        m = MOIS[mois[-1].group(1)]
        debut = mois[-2].end() if len(mois) > 1 else 0
        jours = re.findall(r"\d{1,2}", t[debut:mois[-1].start()])
    elif mois_par_defaut:
        m, jours = mois_par_defaut, re.findall(r"\d{1,2}", t)
    else:
        return None
    if not jours:
        return None
    try:
        return date(int(annees[-1]) if annees else annee_par_defaut, m, int(jours[-1]))
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
# Tous les sondages d'un scrutin sont exprimés dans les colonnes du tableau des
# résultats : une cellule de sondage est (colonnes du résultat qu'elle couvre,
# valeur). Dans le tableau des résultats, c'est la colonne elle-même ; dans un
# tableau plus ancien, la colonne qui porte le même candidat (« Jean-Luc
# Mélenchon (FI) » -> « Jean-Luc Mélenchon (LFI) », « FN/RN » -> « RN »).
Cellules = list[tuple[frozenset[int], float | None]]


@dataclass
class Ligne:
    institut: str
    fin: date
    cellules: Cellules
    extras: int       # candidats chiffrés qui ne se sont pas présentés (Bayrou 2017...)

    @property
    def couverture(self) -> set[int]:
        return {c for cols, v in self.cellules if v is not None for c in cols}


@dataclass
class Lecture:
    scrutin: Scrutin
    resultats: Tableau
    candidats: list[int]          # colonnes de candidats du tableau des résultats
    resultat: Cellules
    lignes: list[Ligne]


def _cle_nom(libelle: str) -> str:
    """Nom de famille : « Jean-Luc Mélenchon (FI) » -> « melenchon »."""
    mots = _plat(libelle.split("(")[0]).split()
    return mots[-1] if mots else ""


def _jetons(libelle: str) -> set[str]:
    return {j for j in re.split(r"[\s\-/·,()]+", _plat(libelle)) if j}


def correspondance(libelle: str, cibles: dict[int, list[str]], personnes: bool) -> set[int]:
    """Colonnes du tableau des résultats qui portent le même candidat que
    `libelle`. Présidentielle : même nom de famille. Listes et blocs : même
    libellé (à n'importe quel niveau d'en-tête : « NFP » coiffe quatre
    colonnes), sinon le plus de sigles en commun, sans ex aequo."""
    if personnes:
        k = _cle_nom(libelle)
        return {c for c, labs in cibles.items() if any(_cle_nom(x) == k for x in labs)}
    exact = {c for c, labs in cibles.items() if any(_plat(x) == _plat(libelle) for x in labs)}
    if exact:
        return exact
    communs = {c: max(len(_jetons(libelle) & _jetons(x)) for x in labs)
               for c, labs in cibles.items()}
    meilleur = max(communs.values(), default=0)
    gagnants = {c for c, n in communs.items() if n == meilleur}
    return gagnants if meilleur and len(gagnants) == 1 else set()


def _cellules(ligne: list[Cellule], colonnes: dict[int, set[int]]) -> tuple[Cellules, int]:
    """Les cellules d'une ligne dans les colonnes du résultat, et le nombre de
    candidats chiffrés sans correspondance."""
    out, extras = [], 0
    for cell in {id(x): x for x in ligne if x is not None}.values():
        couvre = [c for c in range(cell.c0, cell.c1) if c in colonnes]
        if not couvre:
            continue
        cibles = frozenset(set().union(*(colonnes[c] for c in couvre)))
        v = valeur(cell.texte)
        if cibles:
            out.append((cibles, v))
        elif v is not None:
            extras += 1
    return out, extras


def lire(scrutin: Scrutin, html: str) -> Lecture:
    tabs = tableaux(html, scrutin.section)
    res = next(t for t in tabs if any(l[0].texte.startswith("Résultat") for l in t.donnees()))
    candidats = res.candidats()
    cibles = {c: res.libelles(c) for c in candidats}
    personnes = scrutin.titre == "Présidentielle"

    resultat, lignes = None, []
    for t in tabs:
        if t is res:
            colonnes = {c: {c} for c in candidats}
        else:
            colonnes = {c: correspondance(t.libelles(c)[-1], cibles, personnes)
                        for c in t.candidats()}
        col_date = t.col_date()
        if col_date is None:
            continue
        for l in t.donnees():
            if l[0].texte.startswith("Résultat"):
                if t is res and resultat is None:
                    resultat = _cellules(l, colonnes)[0]
                continue
            inst = institut_de(l[0].texte)
            fin = fin_terrain(l[col_date].texte, t.annee or scrutin.jour.year, t.mois) \
                if inst and l[col_date] else None
            if fin:
                cellules, extras = _cellules(l, colonnes)
                lignes.append(Ligne(inst, fin, cellules, extras))
    return Lecture(scrutin, res, candidats, resultat, lignes)


@dataclass
class Unite:
    libelle: str
    colonnes: frozenset[int]
    resultat: float


@dataclass
class Confrontation:
    scrutin: Scrutin
    horizon: int
    unites: list[Unite]
    sondages: dict[str, dict]   # institut -> {"fin": date, "lignes": n, "valeurs": [..]}
    erreurs: dict[str, float]   # institut -> erreur quadratique moyenne, en points

    @property
    def erreur_moyenne(self) -> float:
        return float(np.mean(list(self.erreurs.values())))


def _somme(cellules: Cellules, colonnes: frozenset[int]) -> float | None:
    """Somme des cellules tombant dans un groupe de colonnes ; None si aucune
    ne porte de chiffre."""
    vals = [v for cols, v in cellules if cols & colonnes and v is not None]
    return sum(vals) if vals else None


def confronter(lec: Lecture, horizon: int) -> Confrontation | None:
    scr = lec.scrutin
    cible = scr.jour - timedelta(days=horizon)
    fin_max = min(cible + timedelta(days=JOURS_MAX), scr.jour)
    debut = max(cible - timedelta(days=JOURS_MAX), scr.depuis or date.min)

    # Un sondage valide teste tous les candidats principaux (≥ SEUIL_PRINCIPAL %) :
    # c'est une hypothèse du scrutin réel. Pour chaque institut, le plus
    # proche de J − h (à égalité, le plus récent) ;
    # s'il donne plusieurs hypothèses, la plus proche du scrutin réel (cf.
    # `preference`) ; ex aequo moyennés.
    principaux = [cols for cols, v in lec.resultat if v is not None and v >= SEUIL_PRINCIPAL]
    alliances = [cols for cols, _ in lec.resultat if len(cols) > 1]    # NUPES, NFP...

    def preference(l: Ligne):
        # plus de candidats réels ; puis les alliances du scrutin réel (NUPES
        # unie plutôt qu'en listes séparées) ; puis le moins de candidats fictifs
        unies = sum(any(cols >= a for cols, v in l.cellules if v is not None)
                    for a in alliances)
        return (len(l.couverture), unies, -l.extras)

    valides = [l for l in lec.lignes
               if debut <= l.fin <= fin_max and all(l.couverture & p for p in principaux)]
    retenus = {}
    for inst, _ in INSTITUTS:
        siennes = [l for l in valides if l.institut == inst]
        if not siennes:
            continue
        fin = min((l.fin for l in siennes), key=lambda f: (abs((f - cible).days), -f.toordinal()))
        siennes = [l for l in siennes if l.fin == fin]
        meilleure = max(preference(l) for l in siennes)
        retenus[inst] = (fin, [l for l in siennes if preference(l) == meilleure])
    if len(retenus) < MIN_INSTITUTS:
        return None

    # Unités de comparaison : une par colonne de candidat, fusionnées quand une
    # même cellule en couvre plusieurs — dans le résultat ou dans l'un des
    # sondages retenus (« RN et alliés » chez Ifop en 2024 : union-find).
    parent = {c: c for c in lec.candidats}

    def racine(c):
        while parent[c] != c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c

    for cellules in [lec.resultat] + [l.cellules for _, ls in retenus.values() for l in ls]:
        for cols, _ in cellules:
            cols = sorted(cols)
            for c in cols[1:]:
                parent[racine(c)] = racine(cols[0])
    groupes: dict[int, list[int]] = {}
    for c in lec.candidats:
        groupes.setdefault(racine(c), []).append(c)

    def libelle(cols):
        # en-tête qui coiffe exactement le groupe (« NFP », « RN et alliés »),
        # sinon les en-têtes des colonnes mis bout à bout (« DVD + LR »)
        for l in reversed(lec.resultats.g[:lec.resultats.n_entete]):
            cell = l[cols[0]]
            if cell and cell.texte and (cell.c0, cell.c1) == (cols[0], cols[-1] + 1):
                return cell.texte
        return " + ".join(lec.resultats.libelles(c)[-1] for c in cols)

    unites = []
    for cols in sorted(groupes.values()):
        r = _somme(lec.resultat, frozenset(cols))
        if r is not None and r >= SEUIL_VOIX:
            unites.append(Unite(libelle(cols), frozenset(cols), r))

    sondages = {}
    for inst, (fin, lignes) in retenus.items():
        vals = []
        for u in unites:
            xs = [v for v in (_somme(l.cellules, u.colonnes) for l in lignes) if v is not None]
            vals.append(float(np.mean(xs)) if xs else None)
        sondages[inst] = {"fin": fin, "lignes": len(lignes), "valeurs": vals}

    # une unité absente d'un sondage sort de la comparaison pour tous
    manquantes = {i for s in sondages.values() for i, v in enumerate(s["valeurs"]) if v is None}
    if manquantes:
        qui = {u.libelle: [k for k, s in sondages.items() if s["valeurs"][i] is None]
               for i, u in enumerate(unites) if i in manquantes}
        print(f"  ⚠ {scr.nom}, J−{horizon} : unités retirées faute d'être publiées "
              f"par tous : {qui}", file=sys.stderr)
    garde = [i for i in range(len(unites)) if i not in manquantes]
    unites = [unites[i] for i in garde]
    for s in sondages.values():
        s["valeurs"] = [s["valeurs"][i] for i in garde]

    erreurs = {inst: math.sqrt(np.mean([(v - u.resultat) ** 2
                                        for v, u in zip(s["valeurs"], unites)]))
               for inst, s in sondages.items()}
    return Confrontation(scr, horizon, unites, sondages, erreurs)


def scores(confs: list[Confrontation]) -> dict[str, dict]:
    """Écart de chaque institut à la référence de chaque scrutin (l'erreur
    moyenne des instituts présents), et leur moyenne : le score (négatif =
    moins d'erreur que la référence). Le score est aussi, à l'arrondi près,
    l'erreur moyenne de l'institut moins la moyenne des références des mêmes
    scrutins : les deux sont gardées pour être affichées côte à côte."""
    out = {}
    for nom, _ in INSTITUTS:
        couverts = [c for c in confs if nom in c.erreurs]
        if couverts:
            ecarts = [c.erreurs[nom] - c.erreur_moyenne for c in couverts]
            out[nom] = {"ecarts": ecarts, "score": float(np.mean(ecarts)),
                        "n": len(ecarts),
                        "erreur": float(np.mean([c.erreurs[nom] for c in couverts])),
                        "reference": float(np.mean([c.erreur_moyenne for c in couverts])),
                        "scrutins": [c.scrutin for c in couverts]}
    return out


def ecart_affiche(a: float, b: float) -> float:
    """a − b tel qu'un lecteur le retrouve en soustrayant les valeurs
    affichées à deux décimales (« 1,67 − 1,97 = −0,30 ») : différence des
    arrondis, et non arrondi de la différence, qui peut tomber à 0,01 près."""
    return round(round(a, 2) - round(b, 2), 2)


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


def _fr(v: float, nd: int = 2, signe: bool = False) -> str:
    """-0.3 -> « −0,30 » : virgule décimale, vrai signe moins ; `signe`
    ajoute le « + » des valeurs positives."""
    s = f"{abs(v):.{nd}f}".replace(".", ",")
    if round(v, nd) < 0:
        return "−" + s
    return ("+" + s) if signe and round(v, nd) > 0 else s


def _quel_sondage(horizon: int) -> str:
    if horizon == 0:
        return "dernier sondage avant le vote"
    return f"sondage le plus proche de J − {horizon}, à {JOURS_MAX} jours près"


def graphique_classement(sc: dict, confs: list[Confrontation], fichier: Path,
                         horizon: int, titre: str, lim: float):
    """Le score de chaque institut (gros point, relié au zéro : la référence)
    et l'écart de chacun de ses scrutins (petits cercles) : la dispersion dit
    combien le classement est fragile, et d'où vient un mauvais score. À
    droite, la soustraction qui donne le score, posée en colonnes : erreur
    moyenne de l'institut − référence moyenne des mêmes scrutins = score."""
    plt = _style()
    import matplotlib.ticker as mticker
    from matplotlib.lines import Line2D

    ordre = sorted(sc, key=lambda k: sc[k]["score"])
    n = len(ordre)
    fig = plt.figure(figsize=(12, 6.8))
    ax = fig.add_axes([0.205, 0.155, 0.48, 0.63])

    ax.set_xlim(-lim, lim)       # même échelle pour les trois horizons
    ax.set_ylim(-0.6, n - 0.4)

    # colonnes de droite (bord droit, en fraction de la figure) et opérateurs
    col_err, col_ref, col_ecart = 0.775, 0.88, 0.97
    ops = [(0.783, "−"), (0.888, "=")]

    def a_droite(x, y, texte, **kw):
        ax.annotate(texte, (x, y), xycoords=("figure fraction", "data"),
                    ha=kw.pop("ha", "right"), va="center", **kw)

    for i, nom in enumerate(ordre):
        y = n - 1 - i
        s = sc[nom]
        affiche = ecart_affiche(s["erreur"], s["reference"])
        couleur = MIEUX if affiche < 0 else PIRE if affiche > 0 else INK_SECOND
        ax.axhline(y, color=GRID, lw=0.8, zorder=1)
        ax.scatter(s["ecarts"], [y] * s["n"], s=46, facecolor=SURFACE,
                   edgecolor=MUTED, linewidths=1.2, zorder=3)
        ax.plot([0, s["score"]], [y, y], color=couleur, lw=2.6,
                solid_capstyle="round", zorder=4)
        ax.scatter([s["score"]], [y], s=170, color=couleur, edgecolor=SURFACE,
                   linewidths=2, zorder=5)

        # nom à gauche, nombre de scrutins dessous
        ax.annotate(nom, (-0.03, y), xycoords=("axes fraction", "data"),
                    xytext=(0, 1.5), textcoords="offset points",
                    ha="right", va="bottom", fontsize=13, fontweight="bold", color=INK)
        ax.annotate(f"{s['n']} scrutins", (-0.03, y), xycoords=("axes fraction", "data"),
                    xytext=(0, -2.5), textcoords="offset points",
                    ha="right", va="top", fontsize=9.5, color=MUTED)

        # erreur − référence = écart (le score)
        a_droite(col_err, y, _fr(s["erreur"]), fontsize=12, color=INK_SECOND)
        a_droite(col_ref, y, _fr(s["reference"]), fontsize=12, color=INK_SECOND)
        a_droite(col_ecart, y, _fr(affiche, signe=True),
                 fontsize=13, fontweight="bold", color=INK)
        for x, op in ops:
            a_droite(x, y, op, ha="left", fontsize=12, color=MUTED)

        # les scrutins qui pèsent le plus sur le score sont nommés ; deux
        # étiquettes trop proches se partagent le dessus et le dessous du point
        px_par_pt = ax.get_position().width * fig.get_figwidth() * 150 / (2 * lim)
        dernier = {1: -1e9, -1: -1e9}            # bord droit de la dernière étiquette
        for e, scr in sorted(zip(s["ecarts"], s["scrutins"]), key=lambda es: es[0]):
            if abs(e) < 1.0:
                continue
            texte = f"{ABREGE[scr.titre]} {scr.detail}"
            demi = len(texte) * 5.2               # demi-largeur approximative, en pixels
            x = e * px_par_pt
            cote = 1 if x - demi > dernier[1] else -1 if x - demi > dernier[-1] else 0
            if not cote:
                continue
            dernier[cote] = x + demi
            ax.annotate(texte, (e, y), xytext=(0, 8 * cote), textcoords="offset points",
                        ha="center", va="bottom" if cote > 0 else "top",
                        fontsize=8.5, color=INK_SECOND)

    # en-têtes : le zéro est la référence ; colonnes de droite
    ax.axvline(0, color=INK, lw=1, zorder=2)
    ax.annotate("référence", (0, 1), xycoords=("data", "axes fraction"),
                xytext=(0, 21), textcoords="offset points",
                ha="center", va="bottom", fontsize=10, fontweight="bold", color=INK)
    ax.annotate("←  moins d'erreur", (0, 1), xycoords=("data", "axes fraction"),
                xytext=(-10, 5), textcoords="offset points",
                ha="right", va="bottom", fontsize=9.5, color=INK_SECOND)
    ax.annotate("plus d'erreur  →", (0, 1), xycoords=("data", "axes fraction"),
                xytext=(10, 5), textcoords="offset points",
                ha="left", va="bottom", fontsize=9.5, color=INK_SECOND)
    for x, haut, bas in [(col_err, "erreur", "de l'institut"),
                         (col_ref, "référence", "des mêmes scrutins"),
                         (col_ecart, "écart", "= le score")]:
        ax.annotate(haut, (x, 1), xycoords=("figure fraction", "axes fraction"),
                    xytext=(0, 21), textcoords="offset points",
                    ha="right", va="bottom", fontsize=10,
                    fontweight="bold" if haut == "écart" else "normal",
                    color=INK if haut == "écart" else INK_SECOND)
        ax.annotate(bas, (x, 1), xycoords=("figure fraction", "axes fraction"),
                    xytext=(0, 6), textcoords="offset points",
                    ha="right", va="bottom", fontsize=8.5, color=MUTED)

    ax.xaxis.set_major_locator(mticker.MultipleLocator(1))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(
        lambda v, _: "0" if abs(v) < 1e-9 else _pts(v).replace(",00", "")))
    ax.grid(axis="x", color=GRID, lw=0.8, zorder=0)
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0, labelsize=10)

    fig.text(0.03, 0.94, titre, fontsize=17, fontweight="bold", color=INK)
    fig.text(0.03, 0.898, "Erreur de l'institut moins la référence du scrutin, en points — "
             f"{_quel_sondage(horizon)}", fontsize=11, color=INK_SECOND)
    poignees = [
        Line2D([], [], ls="", marker="o", ms=7, mfc=SURFACE, mec=MUTED, mew=1.2,
               label="un scrutin : son erreur − la référence du scrutin"),
        Line2D([], [], ls="", marker="o", ms=11, mfc=INK_SECOND, mec=SURFACE, mew=1.5,
               label="moyenne de ces écarts : le score"),
    ]
    fig.legend(handles=poignees, loc="lower left", bbox_to_anchor=(0.2, 0.025), ncol=2,
               frameon=False, fontsize=9.5, labelcolor=INK_SECOND, handletextpad=0.4,
               columnspacing=2.2)
    fig.text(0.97, 0.04, "Wikipédia", fontsize=8, color=MUTED, ha="right")
    fig.savefig(fichier, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"✓ {fichier.name}")


def graphique_scrutins(sc: dict, confs: list[Confrontation], fichier: Path,
                       horizon: int):
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
    ax.annotate("Référence", (-0.015, y_moy), xycoords=("axes fraction", "data"),
                xytext=(0, 1), textcoords="offset points",
                ha="right", va="bottom", fontsize=12, fontweight="bold", color=INK_SECOND)
    ax.annotate("moyenne des instituts", (-0.015, y_moy), xycoords=("axes fraction", "data"),
                xytext=(0, -2), textcoords="offset points",
                ha="right", va="top", fontsize=9, color=MUTED)
    ax.plot([-0.5, xs[-1] + 0.5], [y_moy + 0.62] * 2, color=GRID, lw=1, clip_on=False)

    # légende de la couleur : petite échelle continue sous le tableau
    leg = fig.add_axes([0.4, 0.06, 0.3, 0.022])
    leg.imshow(np.linspace(0, 1, 256)[None, :], aspect="auto", cmap=cmap)
    leg.set_xticks([0, 127.5, 255], [f"−{SATURE:g} pt", "référence du scrutin",
                                     f"+{SATURE:g} pt"])
    leg.set_yticks([])
    leg.tick_params(length=0, labelsize=9, colors=INK_SECOND)
    for spine in leg.spines.values():
        spine.set_visible(False)
    fig.text(0.39, 0.071, "moins d'erreur", ha="right", va="center",
             fontsize=9.5, color=INK_SECOND)
    fig.text(0.71, 0.071, "plus d'erreur", ha="left", va="center",
             fontsize=9.5, color=INK_SECOND)

    fig.text(0.03, 0.935, "Scrutin par scrutin" + {0: "", 30: ", un mois avant",
             90: ", trois mois avant"}.get(horizon, f", J − {horizon}"),
             fontsize=17, fontweight="bold", color=INK)
    fig.text(0.03, 0.892, f"Erreur quadratique moyenne (RMSE) en points — "
             f"{_quel_sondage(horizon)} ; la couleur la compare à la référence du scrutin",
             fontsize=11, color=INK_SECOND)
    fig.text(0.97, 0.025, "Wikipédia", fontsize=8, color=MUTED, ha="right")
    fig.savefig(fichier, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"✓ {fichier.name}")


def ecrire_csv(confs: list[Confrontation], fichier: Path):
    """Les sondages retenus, une ligne par institut et par candidat : de quoi
    refaire le calcul à la main."""
    with open(fichier, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["horizon_jours", "scrutin", "jour_du_vote", "institut",
                    "fin_du_terrain", "candidat_ou_liste", "sondage", "resultat", "ecart"])
        for c in confs:
            for nom, _ in INSTITUTS:
                if nom not in c.sondages:
                    continue
                s = c.sondages[nom]
                for u, v in zip(c.unites, s["valeurs"]):
                    w.writerow([c.horizon, c.scrutin.nom, c.scrutin.jour.isoformat(), nom,
                                s["fin"].isoformat(), u.libelle, f"{v:g}",
                                f"{u.resultat:g}", f"{v - u.resultat:+.2f}"])
    print(f"✓ {fichier.name}")


# ---------------------------------------------------------------------------
# 5. Exemple de calcul, écrit dans la page
# ---------------------------------------------------------------------------
# Le calcul complet d'un institut sur un scrutin, posé en tableau dans
# index.html. Ses chiffres dépendent des données et de la méthode : le script
# les réécrit à chaque exécution entre ces deux marqueurs, pour qu'ils ne
# deviennent jamais faux en silence (cf. README).
EXEMPLE = ("Présidentielle 2022 · 1er tour", "Ipsos")   # (scrutin, institut)
EXEMPLE_DEBUT = "<!-- exemple : écrit par instituts.py, ne pas modifier à la main -->"
EXEMPLE_FIN = "<!-- /exemple -->"
MOIS_NOMS = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
             "août", "septembre", "octobre", "novembre", "décembre"]


def _date_fr(d: date) -> str:
    return f"{'1er' if d.day == 1 else d.day} {MOIS_NOMS[d.month]} {d.year}"


def html_exemple(confs: list[Confrontation], sc: dict) -> str:
    nom_scrutin, inst = EXEMPLE
    c = next(c for c in confs if c.scrutin.nom == nom_scrutin)
    s = c.sondages[inst]
    lignes = sorted(zip(c.unites, s["valeurs"]), key=lambda uv: -uv[0].resultat)
    carres = [(v - u.resultat) ** 2 for u, v in lignes]
    moy_carres = float(np.mean(carres))
    err, ref = c.erreurs[inst], c.erreur_moyenne
    total = sc[inst]
    de = "d'" if _plat(inst)[0] in "aeiouy" else "de "

    def g(v):      # valeur de sondage telle que publiée : 17,5 ; 3 ; 56,75
        return f"{v:g}".replace(".", ",")

    def pts(v):    # « 0,73 point », « 2,04 points »
        return f"{_fr(v)} point{'s' if abs(v) >= 2 else ''}"

    def eq(texte):
        return f'<span class="eq">{texte}</span>'

    annee, _, tour = c.scrutin.detail.partition(" · ")
    tour = tour.replace("1er", "1<sup>er</sup>").replace("2d", "2<sup>d</sup>")
    titre = f"{c.scrutin.titre.lower()} {annee}" + (f", {tour}" if tour else "")

    rangs = [f'<tr><th scope="row">{u.libelle}</th><td>{g(v)}</td>'
             f"<td>{_fr(u.resultat)}</td><td>{_fr(v - u.resultat, signe=True)}</td>"
             f"<td>{_fr(e2)}</td></tr>" for (u, v), e2 in zip(lignes, carres)]
    html = "\n".join([
        '<div class="calc">',
        f"  <h3>Exemple : {inst}, {titre}</h3>",
        f'  <p class="calc-lead">Son dernier sondage s\'achève le {_date_fr(s["fin"])}, '
        f"{(c.scrutin.jour - s['fin']).days} jours avant le vote. Écart = sondage − "
        "résultat, en points.</p>",
        '  <div class="calc-table">',
        "    <table>",
        '      <thead><tr><th scope="col">Candidat</th><th scope="col">Sondage</th>'
        '<th scope="col">Résultat</th><th scope="col">Écart</th>'
        '<th scope="col">Écart²</th></tr></thead>',
        "      <tbody>",
        *[f"        {r}" for r in rangs],
        "      </tbody>",
        f'      <tfoot><tr><th scope="row" colspan="4">Moyenne des {len(carres)} carrés'
        f"</th><td>{_fr(moy_carres)}</td></tr></tfoot>",
        "    </table>",
        "  </div>",
        '  <ol class="calc-steps">',
        f"    <li><strong>Erreur.</strong> {eq(f'E = √{_fr(moy_carres)} = {_fr(err)}')} "
        f"point{'s' if err >= 2 else ''}.</li>",
        f"    <li><strong>Référence.</strong> Les {len(c.erreurs)} instituts présents se "
        f"trompent en moyenne de {pts(ref)} sur ce scrutin. Écart {de}{inst} : "
        f"{eq(f'{_fr(err)} − {_fr(ref)} = {_fr(ecart_affiche(err, ref), signe=True)}')}.</li>",
        f"    <li><strong>Score.</strong> Même calcul sur ses {total['n']} scrutins, puis "
        f"moyenne : erreur {eq(_fr(total['erreur']))}, référence "
        f"{eq(_fr(total['reference']))}, score "
        f"{eq(_fr(ecart_affiche(total['erreur'], total['reference']), signe=True))}.</li>",
        "  </ol>",
        "</div>",
    ])
    # pas de « : » ni de « % » rejeté seul en début de ligne
    return html.replace(" :", "&nbsp;:").replace(" %", "&nbsp;%")


def ecrire_exemple(confs: list[Confrontation], sc: dict, fichier: Path):
    page = fichier.read_text(encoding="utf-8")
    debut, fin = page.find(EXEMPLE_DEBUT), page.find(EXEMPLE_FIN)
    if debut < 0 or fin < debut:
        print(f"⚠ marqueurs de l'exemple absents de {fichier.name} : page non modifiée",
              file=sys.stderr)
        return
    retrait = page[page.rfind("\n", 0, debut) + 1:debut]     # indentation des marqueurs
    bloc = "\n".join(retrait + l if l else l for l in html_exemple(confs, sc).split("\n"))
    neuve = page[:debut + len(EXEMPLE_DEBUT)] + "\n" + bloc + "\n" + retrait + page[fin:]
    if neuve != page:
        fichier.write_text(neuve, encoding="utf-8")
    print(f"✓ {fichier.name} (exemple de calcul)")


# ---------------------------------------------------------------------------
# 6. Orchestration
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true",
                    help="retélécharger les pages Wikipédia au lieu de lire .cache/")
    args = ap.parse_args()
    matplotlib.use("Agg")

    lectures = [lire(s, fetch_page(s.page, args.refresh)) for s in SCRUTINS]
    horizons = []
    for h, suffixe, titre in HORIZONS:
        print(f"\n— J − {h} " + "—" * 60)
        confs = []
        for lec in lectures:
            c = confronter(lec, h)
            if c is None:
                print(f"{lec.scrutin.nom:34s} moins de {MIN_INSTITUTS} instituts : écarté")
                continue
            confs.append(c)
            print(f"{c.scrutin.nom:34s} {len(c.unites):2d} candidats, référence "
                  f"{c.erreur_moyenne:.2f} pt — "
                  + ", ".join(f"{k} {v:.2f}" for k, v in sorted(c.erreurs.items(),
                                                                key=lambda kv: kv[1])))
        sc = scores(confs)
        print("Score :", ", ".join(f"{k} {sc[k]['score']:+.3f} ({sc[k]['n']})"
                                   for k in sorted(sc, key=lambda k: sc[k]["score"])))
        horizons.append((h, suffixe, titre, confs, sc))

    # une seule échelle pour les trois classements, qu'on puisse les comparer
    tous = [abs(e) for *_, sc in horizons for v in sc.values() for e in v["ecarts"]]
    lim = max(1.0, math.ceil(max(tous) * 2 + 0.6) / 2)
    print()
    for h, suffixe, titre, confs, sc in horizons:
        graphique_classement(sc, confs, ICI / f"instituts_classement{suffixe}.png", h, titre, lim)
        graphique_scrutins(sc, confs, ICI / f"instituts_scrutins{suffixe}.png", h)
    ecrire_csv([c for *_, confs, _ in horizons for c in confs], ICI / "sondages_retenus.csv")
    _, _, _, confs, sc = horizons[0]
    ecrire_exemple(confs, sc, ICI / "index.html")


if __name__ == "__main__":
    main()

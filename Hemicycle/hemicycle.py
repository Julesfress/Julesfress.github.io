#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Carte des députés de l'Assemblée nationale, dessinée à partir de leurs votes
réels — pas de leur étiquette de groupe.

Chaque scrutin public de la législature en cours est un axe de comparaison :
deux députés se ressemblent s'ils votent pareil. En comparant les 577 députés
deux à deux, puis en projetant le nuage obtenu sur ses deux directions
principales, on obtient une carte où la position de chacun n'est postulée par
personne : elle sort des votes.

Ce que la carte montre en général :
  - l'axe 1 reconstitue le clivage gauche-droite sans qu'on le lui demande ;
  - l'axe 2 sépare les oppositions du socle gouvernemental, ce qui range les
    deux extrémités de l'axe 1 du même côté — un clivage que l'étiquette de
    groupe, à elle seule, ne laisse pas voir ;
  - la dispersion d'un groupe se lit directement : un groupe compact vote d'un
    bloc, un groupe étalé (LIOT, non-inscrits) ne vote pas comme un groupe.

Méthode — pourquoi une similarité par paires plutôt qu'une ACP directe.
La matrice députés × scrutins est vide à ~75 % : la participation médiane est
d'environ 130 votants sur 577, l'absence est la règle et non l'exception. Une
ACP classique obligerait à inventer une valeur pour chaque case vide (souvent
la moyenne du scrutin), donc à faire voter des députés absents. On calcule
plutôt, pour chaque paire de députés, leur accord moyen sur les seuls scrutins
qu'ils ont votés TOUS LES DEUX ; les absences ne sont jamais comblées, elles
sont ignorées. La carte est ensuite obtenue par positionnement multidimensionnel
classique (double centrage de la matrice d'accord, puis diagonalisation), qui
place les députés de façon que les distances à l'écran reflètent au mieux ces
accords deux à deux.

Conventions de vote : pour = +1, contre = -1, abstention = 0. Un non-votant
(président de séance, absence) n'est pas un vote et ne compte pour aucune paire.

Usage :
    python hemicycle.py                 # les deux PNG
    python hemicycle.py --cache .cache  # réutilise les archives déjà téléchargées
    python hemicycle.py --min-votes 100 # resserre le filtre des députés
    python hemicycle.py --show          # fenêtres interactives

Dépendances : requests, numpy, matplotlib   (voir requirements.txt)
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import zipfile
from collections import Counter, defaultdict
from datetime import date

import matplotlib
import numpy as np
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
LEGISLATURE = 17
BASE = "https://data.assemblee-nationale.fr/static/openData/repository"
SCRUTINS_URL = f"{BASE}/{LEGISLATURE}/loi/scrutins/Scrutins.json.zip"
ACTEURS_URL = (f"{BASE}/{LEGISLATURE}/amo/deputes_actifs_mandats_actifs_organes"
               f"/AMO10_deputes_actifs_mandats_actifs_organes.json.zip")
USER_AGENT = "carte-hemicycle/1.0 (script pédagogique)"

# Un député qui a voté trois fois ne peut pas être situé sérieusement : sa
# position dépendrait d'une poignée de scrutins. Le seuil écarte surtout les
# arrivées très récentes (remplacements) et les ministres.
MIN_VOTES = 50

# Valeur numérique d'une position de vote. L'abstention vaut 0 : elle est
# à mi-chemin entre le pour et le contre, ce qui est exactement ce qu'on veut
# lui faire dire ici. Les non-votants n'apparaissent pas — c'est une absence,
# pas une opinion.
POSITIONS = {"pours": 1.0, "contres": -1.0, "abstentions": 0.0}

# Jeu de couleurs « encre » du système de design (identique à
# Sondpres/sondages_2027.py et à assets/css/style.css).
INK = "#0b0b0b"
INK_SECOND = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
SURFACE = "#fcfcfb"

# Une seule couleur d'accent, et elle ne porte jamais l'identité d'un groupe :
# sur un nuage de points, aucune palette catégorielle ne reste distinguable
# au-delà de trois séries (a fortiori douze groupes). L'identité passe donc
# par le texte — étiquettes posées sur la carte, titres des facettes — et
# l'accent ne sert qu'à désigner « le groupe de CE panneau ».
ACCENT = "#2a78d6"
BACKDROP = "#dcdbd4"

FR_MONTHS = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
             "août", "septembre", "octobre", "novembre", "décembre"]


def fr_date(iso: str) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{d} {FR_MONTHS[m]} {y}"


def pct(x: float) -> str:
    return f"{x:.1f}".replace(".", ",") + " %"


def milliers(n: int) -> str:
    """8434 -> « 8 434 ». Le remplacement est appliqué au seul nombre : passé
    sur la phrase entière, il effacerait aussi les virgules du texte."""
    return f"{n:,}".replace(",", " ")


# ---------------------------------------------------------------------------
# 1. Récupération des archives
# ---------------------------------------------------------------------------
def fetch_zip(url: str, cache_dir: str | None, tentatives: int = 4) -> zipfile.ZipFile:
    """Archive ZIP de l'open data de l'Assemblée, gardée en mémoire.

    Les archives sont volumineuses (~26 Mo pour les scrutins, 170 Mo une fois
    décompressées) : on ne les écrit jamais sur le disque décompressées, on lit
    chaque entrée à la demande. `cache_dir` évite de retélécharger à chaque
    essai en local ; en intégration continue il n'y a pas de cache, donc les
    données sont fraîches à chaque exécution.

    Le serveur coupe assez souvent la connexion en cours de transfert (le
    téléchargement se termine alors sur un IncompleteRead, ou sur une archive
    tronquée que zipfile refuse) : comme personne ne surveille l'exécution
    quotidienne, on réessaie au lieu d'échouer. L'archive n'est mise en cache
    qu'une fois relue avec succès, pour ne jamais garder un fichier tronqué.
    """
    name = url.rsplit("/", 1)[-1]
    path = os.path.join(cache_dir, name) if cache_dir else None
    if path and os.path.exists(path):
        with open(path, "rb") as fh:
            return zipfile.ZipFile(io.BytesIO(fh.read()))

    for essai in range(1, tentatives + 1):
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=300)
            r.raise_for_status()
            zf = zipfile.ZipFile(io.BytesIO(r.content))
        except (requests.RequestException, zipfile.BadZipFile) as err:
            if essai == tentatives:
                raise
            attente = 5 * essai
            print(f"  {name} : échec ({type(err).__name__}), "
                  f"nouvelle tentative dans {attente} s", file=sys.stderr)
            time.sleep(attente)
            continue
        if path:
            os.makedirs(cache_dir, exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(r.content)
        return zf
    raise AssertionError("unreachable")


# ---------------------------------------------------------------------------
# 2. Députés et groupes
# ---------------------------------------------------------------------------
def _is_nil(value) -> bool:
    """Vrai pour un champ absent. L'export sérialise « pas de valeur » tantôt
    par `null`, tantôt par le `{"@xsi:nil": "true"}` hérité du XML d'origine."""
    return value is None or (isinstance(value, dict) and value.get("@xsi:nil") == "true")


def _as_list(value) -> list:
    """Les champs répétés de l'export sont une liste s'il y a plusieurs
    éléments, mais l'objet nu s'il n'y en a qu'un."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def load_deputes(zf: zipfile.ZipFile) -> dict[str, dict]:
    """{identifiant acteur -> {nom, groupe, libelle_groupe}} pour les députés en exercice."""
    groupes: dict[str, tuple[str, str]] = {}
    for entry in zf.namelist():
        if "/organe/" not in entry:
            continue
        organe = json.loads(zf.read(entry))["organe"]
        if organe["codeType"] == "GP":  # GP = groupe politique
            groupes[organe["uid"]] = (organe["libelleAbrev"], organe["libelle"])

    deputes: dict[str, dict] = {}
    for entry in zf.namelist():
        if "/acteur/" not in entry:
            continue
        acteur = json.loads(zf.read(entry))["acteur"]
        ident = acteur["etatCivil"]["ident"]

        # Un député peut avoir changé de groupe en cours de législature : on
        # retient le mandat en cours (sans date de fin) et, à défaut, le plus
        # récent — c'est le groupe sous lequel il siège aujourd'hui.
        ref, latest = None, ""
        for mandat in _as_list(acteur["mandats"]["mandat"]):
            if mandat.get("typeOrgane") != "GP":
                continue
            organe_ref = _as_list(mandat["organes"]["organeRef"])
            organe_ref = organe_ref[0] if organe_ref else None
            if organe_ref not in groupes:
                continue
            if _is_nil(mandat.get("dateFin")):
                ref = organe_ref
                break
            if (mandat.get("dateDebut") or "") > latest:
                latest, ref = mandat.get("dateDebut") or "", organe_ref
        if ref is None:
            continue

        abbrev, libelle = groupes[ref]
        deputes[acteur["uid"]["#text"]] = {
            "nom": f"{ident['prenom']} {ident['nom']}",
            "groupe": abbrev,
            "libelle": libelle,
        }
    return deputes


# ---------------------------------------------------------------------------
# 3. Matrice des votes
# ---------------------------------------------------------------------------
def load_votes(zf: zipfile.ZipFile, deputes: dict[str, dict]):
    """Matrices (votes, exprimés) de taille députés × scrutins.

    `votes[i, j]` vaut +1/0/-1 et `exprime[i, j]` vaut 1 si et seulement si le
    député i s'est prononcé sur le scrutin j. Ce masque est le cœur de la
    méthode : c'est lui qui permet plus loin de ne comparer deux députés que
    sur les scrutins qu'ils ont votés tous les deux.
    """
    entries = sorted(e for e in zf.namelist() if e.endswith(".json"))
    uids = sorted(deputes)
    index = {uid: i for i, uid in enumerate(uids)}

    votes = np.zeros((len(uids), len(entries)), dtype=np.float32)
    exprime = np.zeros((len(uids), len(entries)), dtype=np.float32)
    dates: list[str] = []

    for j, entry in enumerate(entries):
        scrutin = json.loads(zf.read(entry))["scrutin"]
        dates.append(scrutin["dateScrutin"])
        for organe in _as_list(scrutin["ventilationVotes"]["organe"]):
            for groupe in _as_list(organe["groupes"]["groupe"]):
                nominatif = groupe["vote"].get("decompteNominatif") or {}
                for cle, valeur in POSITIONS.items():
                    bloc = nominatif.get(cle)
                    if _is_nil(bloc):
                        continue
                    for votant in _as_list(bloc.get("votant")):
                        i = index.get(votant["acteurRef"])
                        if i is not None:   # les députés partis en cours de route
                            votes[i, j] = valeur
                            exprime[i, j] = 1.0
    return uids, votes, exprime, dates


# ---------------------------------------------------------------------------
# 4. Carte
# ---------------------------------------------------------------------------
def build_map(votes: np.ndarray, exprime: np.ndarray):
    """Coordonnées des députés + part de variance de chaque axe.

    1. `commun = exprime @ exprime.T` compte, pour chaque paire, les scrutins
       votés des deux côtés ; `accord = votes @ votes.T` en somme les produits
       (+1 quand les deux votent pareil, -1 quand ils s'opposent, 0 dès qu'une
       abstention entre en jeu). Leur rapport est l'accord moyen de la paire,
       dans [-1, 1], calculé sans jamais combler une absence.
    2. Le double centrage (`J S J`) transforme cette matrice d'accord en une
       matrice de produits scalaires centrée sur le barycentre de l'Assemblée :
       sa diagonalisation donne alors directement les coordonnées cherchées
       (positionnement multidimensionnel classique).
    """
    commun = exprime @ exprime.T
    accord = votes @ votes.T
    # Une paire qui n'a jamais voté ensemble n'apporte aucune information : on
    # lui donne un accord nul (« ni proche ni opposé ») plutôt qu'une division
    # par zéro. Le seuil de MIN_VOTES rend le cas marginal.
    similarite = accord / np.maximum(commun, 1.0)

    n = len(similarite)
    centrage = np.eye(n) - 1.0 / n
    noyau = centrage @ similarite @ centrage
    valeurs, vecteurs = np.linalg.eigh(noyau)
    ordre = np.argsort(valeurs)[::-1]
    valeurs, vecteurs = valeurs[ordre], vecteurs[:, ordre]

    coords = vecteurs[:, :2] * np.sqrt(np.abs(valeurs[:2]))
    positives = np.clip(valeurs, 0.0, None)
    parts = 100.0 * positives[:2] / positives.sum()
    return coords, parts


def orient(coords: np.ndarray, groupes: list[str]) -> np.ndarray:
    """Fixe le sens des deux axes.

    Une diagonalisation ne définit chaque axe qu'au signe près : sans cette
    étape, la carte pourrait se retrouver retournée d'une exécution à l'autre
    et le graphique serait re-committé pour rien. On ancre donc les deux axes
    sur des repères stables — la gauche à gauche, le socle gouvernemental en
    bas — en se rabattant sur un critère purement numérique si le groupe de
    référence a disparu (changement de législature).
    """
    def barycentre(abbrev: str, axe: int) -> float | None:
        vals = [c[axe] for c, g in zip(coords, groupes) if g == abbrev]
        return float(np.mean(vals)) if vals else None

    coords = coords.copy()
    for axe, (ancre, attendu) in enumerate([("LFI-NFP", -1.0), ("EPR", -1.0)]):
        ref = barycentre(ancre, axe)
        if ref is None or ref == 0.0:
            # Repli : on ne connaît plus l'ancre, on impose que la queue la
            # plus longue de la distribution pointe vers les positifs.
            ref = -float(np.mean(coords[:, axe] ** 3)) or 1.0
        if np.sign(ref) != attendu:
            coords[:, axe] *= -1.0
    return coords


# ---------------------------------------------------------------------------
# 5. Graphiques
# ---------------------------------------------------------------------------
def setup_style():
    matplotlib.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 11,
        "axes.edgecolor": MUTED, "axes.labelcolor": INK_SECOND,
        "xtick.color": MUTED, "ytick.color": MUTED,
        "text.color": INK, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    })


def ellipse_groupe(points: np.ndarray):
    """Demi-axes et angle de l'ellipse à un écart-type d'un nuage de points.
    Sa taille est la cohésion du groupe : serrée, le groupe vote d'un bloc."""
    if len(points) < 3:
        return None
    cov = np.cov(points.T)
    valeurs, vecteurs = np.linalg.eigh(cov)
    valeurs = np.clip(valeurs, 0.0, None)
    ordre = np.argsort(valeurs)[::-1]
    valeurs, vecteurs = valeurs[ordre], vecteurs[:, ordre]
    angle = np.degrees(np.arctan2(vecteurs[1, 0], vecteurs[0, 0]))
    return 2 * np.sqrt(valeurs[0]), 2 * np.sqrt(valeurs[1]), angle


def spread_labels(points: np.ndarray, gap: np.ndarray, iters: int = 600) -> np.ndarray:
    """Écarte des étiquettes qui se recouvrent, en les gardant près de leur
    point d'ancrage. Les groupes de gauche se superposent largement sur la
    carte : sans cela, quatre noms s'empileraient au même endroit."""
    pos = points.copy()
    for _ in range(iters):
        bouge = False
        for i in range(len(pos)):
            for j in range(i + 1, len(pos)):
                delta = pos[j] - pos[i]
                chevauchement = gap - np.abs(delta)
                if np.all(chevauchement > 0):
                    # On sépare selon l'axe où le recouvrement est le plus
                    # faible : c'est le déplacement le plus court qui résout.
                    axe = int(np.argmin(chevauchement / gap))
                    pousse = np.zeros(2)
                    sens = np.sign(delta[axe]) or 1.0
                    pousse[axe] = sens * chevauchement[axe] * 0.28
                    pos[i] -= pousse
                    pos[j] += pousse
                    bouge = True
        if not bouge:
            break
    return pos


def chart_carte(coords, groupes, parts, meta, outfile, show):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Ellipse

    fig, ax = plt.subplots(figsize=(12, 8.4))
    fig.subplots_adjust(left=0.055, right=0.975, top=0.855, bottom=0.105)

    ax.axhline(0, color=GRID, lw=1, zorder=0)
    ax.axvline(0, color=GRID, lw=1, zorder=0)
    ax.scatter(coords[:, 0], coords[:, 1], s=25, color=INK, alpha=0.42,
               linewidths=0, zorder=2)

    par_groupe = defaultdict(list)
    for c, g in zip(coords, groupes):
        par_groupe[g].append(c)
    ordre = sorted(par_groupe, key=lambda g: np.mean([c[0] for c in par_groupe[g]]))

    centres = np.array([np.mean(par_groupe[g], axis=0) for g in ordre])
    for g, centre in zip(ordre, centres):
        forme = ellipse_groupe(np.array(par_groupe[g]))
        if forme:
            largeur, hauteur, angle = forme
            ax.add_patch(Ellipse(centre, largeur, hauteur, angle=angle,
                                 facecolor="none", edgecolor=INK_SECOND,
                                 lw=0.9, alpha=0.45, zorder=3))

    etendue = coords.max(0) - coords.min(0)
    gap = np.array([etendue[0] * 0.115, etendue[1] * 0.062])
    labels = spread_labels(centres, gap)
    for g, centre, pos in zip(ordre, centres, labels):
        if np.linalg.norm(pos - centre) > gap[1] * 0.25:
            ax.plot([centre[0], pos[0]], [centre[1], pos[1]], color=MUTED,
                    lw=0.8, alpha=0.8, zorder=4)
        ax.text(*pos, f"{g}  {len(par_groupe[g])}", ha="center", va="center",
                fontsize=10.5, fontweight="bold", color=INK, zorder=5,
                bbox=dict(boxstyle="round,pad=0.28", facecolor=SURFACE,
                          edgecolor="none", alpha=0.9))

    # Les pôles ne sont pas nommés d'avance : chaque extrémité d'axe est
    # étiquetée par le groupe qui s'y trouve réellement, pour que la lecture
    # de la carte ne dépende d'aucune grille de lecture imposée au départ.
    ax.set_xlabel(f"Axe 1 — {pct(parts[0])} de la variance", labelpad=10)
    ax.set_ylabel(f"Axe 2 — {pct(parts[1])} de la variance", labelpad=10)
    hauteurs = {g: centres[k][1] for k, g in enumerate(ordre)}
    bas, haut = min(hauteurs, key=hauteurs.get), max(hauteurs, key=hauteurs.get)
    # Les pôles sont posés le long des axes, hors du nuage : placés à
    # l'intérieur, ils se liraient comme l'étiquette d'un point voisin.
    for texte, (x, y), ha, va, rot in [
        (f"← {ordre[0]}", (0.0, -0.055), "left", "top", 0),
        (f"{ordre[-1]} →", (1.0, -0.055), "right", "top", 0),
        (f"← {bas}", (-0.020, 0.0), "left", "bottom", 90),
        (f"{haut} →", (-0.020, 1.0), "right", "top", 90),
    ]:
        ax.annotate(texte, (x, y), xycoords="axes fraction", ha=ha, va=va,
                    fontsize=9.5, color=MUTED, rotation=rot, rotation_mode="anchor")

    for spine in ("top", "right", "left", "bottom"):
        ax.spines[spine].set_visible(False)
    ax.set_xticks([]), ax.set_yticks([])

    fig.text(0.055, 0.955, "Où siègent vraiment les députés", fontsize=19,
             fontweight="bold", color=INK, va="top")
    fig.text(0.055, 0.902, f"Un point par député. Deux députés sont proches s'ils ont "
                           f"voté de la même façon sur les {milliers(meta['n_scrutins'])} "
                           f"scrutins publics de la {LEGISLATURE}ᵉ législature.",
             fontsize=11.5, color=INK_SECOND, va="top")
    fig.text(0.055, 0.030, f"Assemblée nationale, données ouvertes · scrutins du "
                           f"{fr_date(meta['debut'])} au {fr_date(meta['fin'])} · "
                           f"{meta['n_deputes']} députés ayant pris part à au moins "
                           f"{meta['min_votes']} scrutins · l'ellipse couvre un écart-type "
                           f"du groupe",
             fontsize=8.5, color=MUTED, va="top")

    fig.savefig(outfile, dpi=150, facecolor=SURFACE, metadata={"Software": None})
    print(f"✓ Carte écrite : {outfile}")
    if show:
        plt.show()
    plt.close(fig)


def chart_facettes(coords, groupes, meta, outfile, show):
    """Un panneau par groupe : le groupe en couleur, l'Assemblée entière en
    fond. C'est la lecture que douze couleurs sur un même nuage ne permettent
    pas — on voit d'un coup qui est compact, qui est éclaté, qui déborde sur
    le camp d'en face."""
    import matplotlib.pyplot as plt

    par_groupe = defaultdict(list)
    for c, g in zip(coords, groupes):
        par_groupe[g].append(c)
    ordre = sorted(par_groupe, key=lambda g: np.mean([c[0] for c in par_groupe[g]]))

    cols = 4
    rows = -(-len(ordre) // cols)
    fig, axes = plt.subplots(rows, cols, figsize=(12, 3.05 * rows + 1.0))
    fig.subplots_adjust(left=0.02, right=0.98, top=0.845, bottom=0.055,
                        wspace=0.08, hspace=0.24)

    marge = (coords.max(0) - coords.min(0)) * 0.07
    lims = (coords.min(0) - marge, coords.max(0) + marge)

    for ax, g in zip(axes.ravel(), ordre):
        pts = np.array(par_groupe[g])
        ax.scatter(coords[:, 0], coords[:, 1], s=7, color=BACKDROP,
                   linewidths=0, zorder=1)
        ax.scatter(pts[:, 0], pts[:, 1], s=15, color=ACCENT, alpha=0.9,
                   linewidths=0, zorder=2)
        ax.set_title(f"{g} · {len(pts)}", fontsize=12, fontweight="bold",
                     loc="left", color=INK, pad=7)
        ax.set_xlim(lims[0][0], lims[1][0])
        ax.set_ylim(lims[0][1], lims[1][1])
        ax.set_xticks([]), ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)

    for ax in axes.ravel()[len(ordre):]:
        ax.set_visible(False)

    fig.suptitle("Chaque groupe sur la carte", x=0.02, y=0.965, ha="left",
                 fontsize=19, fontweight="bold", color=INK)
    fig.text(0.02, 0.902, "Même carte que ci-dessus, groupe par groupe : en couleur, "
                          "les députés du groupe ; en gris, toute l'Assemblée.",
             fontsize=11.5, color=INK_SECOND, va="bottom")
    fig.text(0.02, 0.016, f"Assemblée nationale, données ouvertes · "
                          f"{milliers(meta['n_scrutins'])} scrutins publics de la "
                          f"{LEGISLATURE}ᵉ législature",
             fontsize=8.5, color=MUTED, va="bottom")

    fig.savefig(outfile, dpi=150, facecolor=SURFACE, metadata={"Software": None})
    print(f"✓ Facettes écrites : {outfile}")
    if show:
        plt.show()
    plt.close(fig)


# ---------------------------------------------------------------------------
# 6. Orchestration
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", metavar="DIR", default=None,
                    help="dossier où garder les archives téléchargées (local uniquement)")
    ap.add_argument("--min-votes", type=int, default=MIN_VOTES, metavar="N",
                    help=f"scrutins minimum pour qu'un député soit placé (défaut : {MIN_VOTES})")
    ap.add_argument("--carte", default="hemicycle_carte.png", help="fichier de la carte")
    ap.add_argument("--facettes", default="hemicycle_groupes.png", help="fichier des facettes")
    ap.add_argument("--show", action="store_true", help="afficher les fenêtres")
    args = ap.parse_args()

    if not args.show:
        matplotlib.use("Agg")

    print("Téléchargement des données de l'Assemblée…")
    deputes = load_deputes(fetch_zip(ACTEURS_URL, args.cache))
    uids, votes, exprime, dates = load_votes(fetch_zip(SCRUTINS_URL, args.cache), deputes)
    print(f"  {len(uids)} députés · {votes.shape[1]} scrutins · "
          f"{100 * exprime.mean():.1f} % de la matrice remplie")

    garde = exprime.sum(1) >= args.min_votes
    if garde.sum() < 3:
        sys.exit(f"Trop peu de députés atteignent {args.min_votes} scrutins : "
                 f"la structure des données a peut-être changé.")
    if (~garde).any():
        print(f"  {(~garde).sum()} député(s) écarté(s), moins de {args.min_votes} votes")

    votes, exprime = votes[garde], exprime[garde]
    retenus = [deputes[u] for u, k in zip(uids, garde) if k]
    groupes = [d["groupe"] for d in retenus]

    coords, parts = build_map(votes, exprime)
    coords = orient(coords, groupes)
    print(f"  variance expliquée : axe 1 {pct(parts[0])} · axe 2 {pct(parts[1])}")
    # La légende des sigles est écrite à la main dans index.html : cette ligne
    # sert à repérer l'apparition ou la disparition d'un groupe.
    print("  groupes : " + ", ".join(f"{g} {n}" for g, n in Counter(groupes).most_common()))

    meta = {
        "n_scrutins": votes.shape[1],
        "n_deputes": len(retenus),
        "min_votes": args.min_votes,
        "debut": min(dates),
        "fin": max(dates),
    }
    setup_style()
    chart_carte(coords, groupes, parts, meta, args.carte, args.show)
    chart_facettes(coords, groupes, meta, args.facettes, args.show)


if __name__ == "__main__":
    main()

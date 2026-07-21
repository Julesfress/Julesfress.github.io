#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Courbes lissées des intentions de vote au 1er tour de la présidentielle 2027.

Les données sont lues EN DIRECT sur Wikipédia à chaque exécution :
    « Liste de sondages sur l'élection présidentielle française de 2027 »
Le graphique reflète donc toujours l'état courant de la page. Si de nouvelles
lignes (nouveaux sondages) y sont ajoutées, il suffit de relancer le script
(ou d'utiliser --watch) pour que le graphique se mette à jour automatiquement.

Le lissage se fait en distance de sondage (pas en distance calendaire) :
chaque sondage d'un candidat est numéroté dans l'ordre (0, 1, 2, ...), et la
courbe est une moyenne à noyau gaussien centrée sur ce rang, pas sur le jour.
Deux sondages publiés à trois jours d'écart pèsent donc l'un sur l'autre
autant que deux sondages publiés à trois semaines d'écart. La courbe reste
néanmoins continue et sans à-coups (elle est évaluée sur une grille fine de
jours, comme un lissage classique), car le noyau gaussien fait entrer et
sortir chaque sondage progressivement plutôt que par à-coups.

Candidats suivis : Le Pen, Mélenchon, Philippe, Attal, Glucksmann,
Retailleau, Tondelier. Période : à partir du 1er janvier 2026.

Usage :
    python sondages_2027.py                 # génère sondages_2027.png
    python sondages_2027.py -o chart.png    # fichier de sortie
    python sondages_2027.py --watch 60      # régénère toutes les 60 min si la page a changé
    python sondages_2027.py --show          # ouvre une fenêtre interactive

Dépendances : requests, pandas, matplotlib, lxml   (voir requirements.txt)
"""
from __future__ import annotations

import argparse
import hashlib
import io
import re
import sys
import time
import unicodedata
from datetime import date, datetime

import matplotlib
import pandas as pd
import requests
import lxml.html as LH

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PAGE_TITLE = "Liste de sondages sur l'élection présidentielle française de 2027"
API_URL = "https://fr.wikipedia.org/w/api.php"
USER_AGENT = "sondages-2027-chart/1.0 (script pédagogique)"
START = date(2026, 1, 1)          # on ne garde que les sondages à partir de cette date
RANK_BANDWIDTH = 1.5               # écart-type du noyau de lissage, en RANGS de sondage (pas en jours)

# Candidats retenus.  Chaque entrée : nom de colonne Wikipédia (préfixe du
# nom de famille), couleur, marqueur.  La palette a été validée pour être
# lisible et sûre pour le daltonisme (plancher vision normale ΔE 15.6 ;
# séparation CVD dans la bande 6–8, compensée par les libellés directs + les
# marqueurs distincts). L'ordre fixe la superposition et la légende.
CANDIDATES = [
    ("Le Pen",     "#0072B2", "o"),   # RN  – bleu
    ("Mélenchon",  "#D55E00", "s"),   # LFI – vermillon
    ("Philippe",   "#C9AE00", "^"),   # HOR – or
    ("Attal",      "#56B4E9", "D"),   # RE  – bleu ciel
    ("Glucksmann", "#CC79A7", "v"),   # PP  – rose
    ("Retailleau", "#6A3D9A", "P"),   # LR  – violet
    ("Tondelier",  "#009E73", "X"),   # LE  – vert
]

# En-têtes alternatifs. Au 1er semestre 2026, la colonne du RN était intitulée
# « Candidat RN » (candidature Le Pen / Bardella encore indécise) : on la
# rattache à la ligne Le Pen pour une courbe continue depuis début 2026.
ALIASES = {
    "Le Pen": ["Le Pen", "Candidat RN"],
}

# Jeu de couleurs "encre" du système de design (fond clair).
INK          = "#0b0b0b"
INK_SECOND   = "#52514e"
MUTED        = "#898781"
GRID         = "#e1e0d9"
SURFACE      = "#fcfcfb"

FR_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
    "juin": 6, "juillet": 7, "août": 8, "aout": 8, "septembre": 9,
    "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}


# ---------------------------------------------------------------------------
# 1. Récupération de la page
# ---------------------------------------------------------------------------
def fetch_page() -> tuple[str, str]:
    """Retourne (html_rendu, horodatage_derniere_revision) depuis l'API Wikipédia."""
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})

    # HTML rendu de la page
    r = s.get(API_URL, params={
        "action": "parse", "page": PAGE_TITLE, "prop": "text",
        "format": "json", "formatversion": 2, "disablelimitreport": 1,
    }, timeout=60)
    r.raise_for_status()
    html = r.json()["parse"]["text"]

    # Date de dernière modification (pour l'afficher sur le graphique)
    rev = ""
    try:
        rq = s.get(API_URL, params={
            "action": "query", "prop": "revisions", "titles": PAGE_TITLE,
            "rvprop": "timestamp", "rvlimit": 1, "format": "json",
            "formatversion": 2,
        }, timeout=60)
        rev = rq.json()["query"]["pages"][0]["revisions"][0]["timestamp"]
    except Exception:
        pass
    return html, rev


# ---------------------------------------------------------------------------
# 2. Analyse des tableaux
# ---------------------------------------------------------------------------
def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def parse_value(cell, accept_name=None) -> float | None:
    """Convertit une cellule en pourcentage.

    - nombre « pur » (« 16 », « 2,5 ») -> valeur : c'est le candidat de la colonne.
    - cellule avec un nom (« 9 Hollande », « 36 Bardella ») -> None, SAUF si le nom
      du candidat suivi y figure (« 32 Le Pen », « 34 Bardella / Le Pen »), ce qui
      permet d'isoler Le Pen dans la colonne ambiguë « Candidat RN » du 1er sem. 2026.
    - « — », vide, etc. -> None.
    """
    if cell is None or (isinstance(cell, float) and pd.isna(cell)):
        return None
    txt = str(cell).replace("\xa0", " ").strip()
    txt = re.sub(r"\[[^\]]*\]", "", txt).strip()      # retire les appels de note [a]
    if txt in ("", "—", "-", "–", "?", "nd", "n.d."):
        return None
    if re.fullmatch(r"\d+(?:[.,]\d+)?", txt):          # nombre pur
        return float(txt.replace(",", "."))
    if accept_name:                                    # nombre + nom : seulement si c'est le bon candidat
        m = re.match(r"(\d+(?:[.,]\d+)?)", txt)
        if m and _strip_accents(accept_name).lower() in _strip_accents(txt).lower():
            return float(m.group(1).replace(",", "."))
    return None                                        # autre chose (un autre nom) -> ignoré


def parse_period(text: str, year: int) -> date | None:
    """Milieu de la période d'enquête. Ex : « 28 mai-2 juin », « 9-10 juillet »."""
    t = str(text).lower().replace("\xa0", " ")
    t = re.sub(r"\[[^\]]*\]", "", t)
    t = t.replace("1er", "1").replace("1ᵉʳ", "1")
    months = [(m.start(), FR_MONTHS[m.group()])
              for m in re.finditer("|".join(FR_MONTHS), t)]
    days = [int(m.group()) for m in re.finditer(r"\d{1,2}", t)]
    if not months or not days:
        return None
    start_m = months[0][1]
    end_m = months[-1][1]
    start_d, end_d = days[0], days[-1]
    start_y = end_y = year
    if start_m > end_m:            # période à cheval sur le nouvel an (déc -> janv)
        start_y -= 1
    try:
        d0 = date(start_y, start_m, start_d)
        d1 = date(end_y, end_m, end_d)
    except ValueError:
        return None
    return date.fromordinal(round((d0.toordinal() + d1.toordinal()) / 2))


def parse_table(table_el, year: int) -> list[dict]:
    """Extrait un tableau de sondages : moyenne des hypothèses par sondage."""
    df = pd.read_html(io.StringIO(LH.tostring(table_el, encoding="unicode")),
                      thousands=None)[0]
    # aplatit l'en-tête multi-niveaux vers les noms (niveau le plus bas)
    df.columns = [c[-1] if isinstance(c, tuple) else c for c in df.columns]
    cols = list(df.columns)

    def find(*prefixes):
        for prefix in prefixes:
            for c in cols:
                if isinstance(c, str) and _strip_accents(c).lower().startswith(
                        _strip_accents(prefix).lower()):
                    return c
        return None

    sondeur_c, date_c = find("Sondeur"), find("Date")
    cand_cols = {name: find(*ALIASES.get(name, [name])) for name, _, _ in CANDIDATES}
    cand_cols = {k: v for k, v in cand_cols.items() if v is not None}
    if sondeur_c is None or date_c is None or len(cand_cols) < 3:
        return []  # pas un tableau de sondages de 1er tour

    work = pd.DataFrame({"sondeur": df[sondeur_c], "date_raw": df[date_c]})
    for name, col in cand_cols.items():
        work[name] = df[col].map(lambda c, n=name: parse_value(c, n))

    # lignes parasites (barre de couleurs, répétition d'en-tête, lignes vides)
    work = work[work["sondeur"].notna()]
    work = work[work["sondeur"].astype(str).str.strip().str.lower() != "sondeur"]
    num_cols = list(cand_cols)
    work = work[work[num_cols].notna().any(axis=1)]
    if work.empty:
        return []

    # une valeur par candidat et par sondage = moyenne des hypothèses testées
    grouped = work.groupby(["sondeur", "date_raw"], sort=False)[num_cols].mean()

    records = []
    for (sondeur, date_raw), row in grouped.iterrows():
        d = parse_period(date_raw, year)
        if d is None or d < START:
            continue
        for name in num_cols:
            v = row[name]
            if pd.notna(v):
                records.append({"date": d, "candidat": name,
                                "valeur": float(v), "sondeur": sondeur})
    return records


def extract_polls(html: str) -> pd.DataFrame:
    """Parcourt la page, ne garde que les tableaux « 1er tour » d'année >= 2026."""
    tree = LH.fromstring(html)
    cur_h2, cur_year = "", None
    records: list[dict] = []
    for el in tree.iter("h2", "h3", "h4", "h5", "table"):
        if el.tag in ("h2", "h3", "h4", "h5"):
            txt = el.text_content().split("[")[0].strip()
            if el.tag == "h2":
                cur_h2 = txt
            ym = re.search(r"\b(20\d\d)\b", txt)
            if ym:
                cur_year = int(ym.group(1))
        else:  # table
            classes = el.get("class") or ""
            if "wikitable" not in classes:
                continue
            if "premier tour" not in _strip_accents(cur_h2).lower():
                continue
            if not cur_year or cur_year < START.year:
                continue
            records.extend(parse_table(el, cur_year))

    df = pd.DataFrame(records)
    if df.empty:
        raise RuntimeError("Aucun sondage 1er tour >= 2026 trouvé : la structure "
                           "de la page a peut-être changé.")
    return df.sort_values("date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# 3. Lissage (noyau gaussien en rang de sondage, pas en jours)
# ---------------------------------------------------------------------------
def poll_rank_smooth(dates_num, values, grid, bw: float = RANK_BANDWIDTH):
    """Lissage à noyau gaussien où la distance utilisée est un RANG de
    sondage (0, 1, 2, ... dans l'ordre chronologique du candidat), pas un
    nombre de jours : deux sondages à trois jours d'écart pèsent l'un sur
    l'autre exactement comme deux sondages à trois semaines d'écart.

    `grid` (mêmes unités que `dates_num`, typiquement une grille de jours)
    est d'abord converti en un rang continu par interpolation linéaire entre
    les rangs entiers des sondages voisins, ce qui permet d'évaluer une
    courbe lisse jour par jour tout en gardant un poids basé sur le nombre
    de sondages. Le noyau (contrairement à une fenêtre dure comme une
    moyenne mobile) fait entrer/sortir chaque sondage progressivement : un
    nouveau sondage ne peut donc pas faire sauter la courbe d'un coup, il la
    déplace en douceur, proportionnellement à sa proximité de rang.
    """
    import numpy as np
    dates_num = np.asarray(dates_num, float)
    values = np.asarray(values, float)
    grid = np.asarray(grid, float)
    ranks = np.arange(len(dates_num), dtype=float)
    grid_ranks = np.interp(grid, dates_num, ranks)
    out = np.empty(grid.shape)
    for i, r in enumerate(grid_ranks):
        w = np.exp(-0.5 * ((r - ranks) / bw) ** 2)
        out[i] = np.dot(w, values) / w.sum()
    return out


# ---------------------------------------------------------------------------
# 4. Graphique
# ---------------------------------------------------------------------------
def make_chart(df: pd.DataFrame, revision: str, outfile: str, show: bool):
    import numpy as np
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import matplotlib.ticker as mticker

    num = mdates.date2num  # date -> nombre de jours (référentiel matplotlib)
    FR_ABBR = ["", "janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.",
               "août", "sept.", "oct.", "nov.", "déc."]

    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 11,
        "axes.edgecolor": MUTED, "axes.labelcolor": INK_SECOND,
        "xtick.color": MUTED, "ytick.color": MUTED,
        "text.color": INK, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    })

    fig, ax = plt.subplots(figsize=(12, 7))
    fig.subplots_adjust(left=0.06, right=0.80, top=0.9, bottom=0.11)

    xmin = num(START)
    xmax = df["date"].map(num).max()
    grid = np.arange(xmin, xmax + 1)

    end_labels = []  # (y, couleur, texte) pour placement anti-collision
    for name, color, marker in CANDIDATES:
        sub = df[df["candidat"] == name].sort_values("date")
        if sub.empty:
            continue
        d = sub["date"].map(num).to_numpy()
        y = sub["valeur"].to_numpy()

        # nuage de points bruts (léger)
        ax.scatter(d, y, s=16, color=color, alpha=0.28, marker=marker,
                   linewidths=0, zorder=2)

        # courbe lissée, continue, restreinte à l'étendue des données du
        # candidat : noyau gaussien en rang de sondage (cf. poll_rank_smooth)
        gmask = (grid >= d.min()) & (grid <= d.max())
        gg = grid[gmask]
        if gg.size == 0:
            continue
        sm = poll_rank_smooth(d, y, gg)
        ax.plot(gg, sm, color=color, lw=2.2, zorder=3, solid_capstyle="round")
        end_labels.append([sm[-1], color, marker, f"{name} {sm[-1]:.1f}"])

    # --- libellés directs à droite, sans chevauchement -----------------------
    end_labels.sort(key=lambda e: e[0])
    min_gap = 1.15  # points de %
    for i in range(1, len(end_labels)):
        if end_labels[i][0] - end_labels[i - 1][0] < min_gap:
            end_labels[i][0] = end_labels[i - 1][0] + min_gap
    x_lab = xmax + (xmax - xmin) * 0.02
    for yv, color, marker, txt in end_labels:
        ax.plot(x_lab, yv, marker=marker, color=color, ms=8, clip_on=False,
                markeredgecolor=SURFACE, markeredgewidth=1.0, zorder=5)
        ax.annotate(txt, (x_lab, yv), xytext=(11, 0), textcoords="offset points",
                    va="center", ha="left", fontsize=10, color=INK,
                    annotation_clip=False)

    # --- axes / habillage -----------------------------------------------------
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(0, max(40, df["valeur"].max() + 4))
    ax.xaxis.set_major_locator(mdates.MonthLocator())

    def _fr_month(x, _):
        dt = mdates.num2date(x)
        lab = FR_ABBR[dt.month]
        return f"{lab}\n{dt.year}" if dt.month == 1 else lab

    ax.xaxis.set_major_formatter(mticker.FuncFormatter(_fr_month))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.grid(axis="y", color=GRID, lw=1, zorder=0)
    ax.grid(axis="x", color=GRID, lw=0.6, alpha=0.6, zorder=0)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(length=0)

    ax.set_title("Présidentielle 2027 — intentions de vote au 1er tour",
                 fontsize=17, fontweight="bold", loc="left", color=INK, pad=16)
    ax.text(0, 1.015,
            "Courbes lissées (noyau gaussien en rang de sondage, pas en jours) "
            "· moyenne des hypothèses par sondage",
            transform=ax.transAxes, fontsize=9.5, color=INK_SECOND)
    ax.text(0, -0.115,
            "Données : Wikipédia, « Liste de sondages sur l'élection présidentielle "
            "française de 2027 ».",
            transform=ax.transAxes, fontsize=8, color=MUTED)

    fig.savefig(outfile, dpi=150, facecolor=SURFACE)
    print(f"✓ Graphique écrit : {outfile}  ({len(df)} points, "
          f"{df['sondeur'].nunique()} sondages, "
          f"{df['date'].min()} → {df['date'].max()})")
    if show:
        plt.show()
    plt.close(fig)


# ---------------------------------------------------------------------------
# 5. Orchestration
# ---------------------------------------------------------------------------
def build_once(outfile: str, show: bool) -> str:
    html, rev = fetch_page()
    df = extract_polls(html)
    make_chart(df, rev, outfile, show)
    return hashlib.md5(html.encode("utf-8")).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output", default="sondages_2027.png",
                    help="fichier image de sortie (défaut : sondages_2027.png)")
    ap.add_argument("--show", action="store_true", help="afficher la fenêtre du graphique")
    ap.add_argument("--watch", type=float, metavar="MIN", default=None,
                    help="régénère toutes les MIN minutes si la page Wikipédia a changé")
    args = ap.parse_args()

    if not args.show:
        matplotlib.use("Agg")

    if args.watch is None:
        build_once(args.output, args.show)
        return

    print(f"Mode veille : vérification toutes les {args.watch} min "
          f"(Ctrl+C pour arrêter).")
    last = None
    while True:
        try:
            html, rev = fetch_page()
            h = hashlib.md5(html.encode("utf-8")).hexdigest()
            if h != last:
                df = extract_polls(html)
                make_chart(df, rev, args.output, show=False)
                last = h
            else:
                print(f"[{datetime.now():%H:%M}] aucune modification.")
        except Exception as e:  # ne pas casser la boucle sur une erreur réseau
            print(f"[{datetime.now():%H:%M}] erreur : {e}", file=sys.stderr)
        time.sleep(args.watch * 60)


if __name__ == "__main__":
    main()

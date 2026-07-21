#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Courbes lissées des intentions de vote au 1er tour d'une présidentielle
française, lues EN DIRECT sur Wikipédia (« Liste de sondages sur l'élection
présidentielle française de XXXX »).

Trois élections sont suivies :
  - 2027 (à venir) : Le Pen, Mélenchon, Philippe, Attal, Glucksmann,
    Retailleau, Tondelier — la page bouge, le graphique se met à jour à
    chaque exécution (ou en continu avec --watch).
  - 2022 et 2017 (passées) : seuls les 5 candidats arrivés en tête au 1er
    tour sont tracés, et le graphique se termine sur le résultat réel du
    scrutin (extrait de la page elle-même), pas sur une estimation.
Un même parti garde la même couleur/le même marqueur d'une élection à
l'autre (ex. Macron 2017/2022 et Attal 2027 partagent la couleur « RE »).

Le lissage se fait en distance de sondage (pas en distance calendaire) :
chaque sondage d'un candidat est numéroté dans l'ordre (0, 1, 2, ...), et la
courbe est une moyenne à noyau gaussien centrée sur ce rang, pas sur le jour.
Deux sondages publiés à trois jours d'écart pèsent donc l'un sur l'autre
autant que deux sondages publiés à trois semaines d'écart. La courbe reste
néanmoins continue et sans à-coups (elle est évaluée sur une grille fine de
jours, comme un lissage classique), car le noyau gaussien fait entrer et
sortir chaque sondage progressivement plutôt que par à-coups.

Tous les graphiques démarrent en avril de l'année précédant l'élection.

Usage :
    python sondages_2027.py                    # génère sondages_2027.png
    python sondages_2027.py --election 2017     # génère sondages_2017.png
    python sondages_2027.py --election all      # les trois graphiques
    python sondages_2027.py -o chart.png        # fichier de sortie
    python sondages_2027.py --watch 60          # régénère toutes les 60 min si la page a changé
    python sondages_2027.py --show              # ouvre une fenêtre interactive

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
from dataclasses import dataclass, field
from datetime import date, datetime

import matplotlib
import pandas as pd
import requests
import lxml.html as LH

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_URL = "https://fr.wikipedia.org/w/api.php"
USER_AGENT = "sondages-2027-chart/1.0 (script pédagogique)"
RANK_BANDWIDTH = 2.2   # écart-type du noyau de lissage, en RANGS de sondage (pas en jours)

# Couleur + marqueur par PARTI (pas par candidat) : un même parti garde le
# même code visuel d'une élection à l'autre — ex. Macron 2017/2022 et Attal
# 2027 partagent « RE », Fillon/Pécresse/Retailleau partagent « LR ». Palette
# Okabe-Ito + extensions, validée daltonisme (skill dataviz : toutes les
# combinaisons utilisées ci-dessous passent CVD ΔE >= 8, plancher normal >= 15).
PARTIES = {
    "RN":  ("#0072B2", "o"),   # Le Pen
    "LFI": ("#D55E00", "s"),   # Mélenchon
    "RE":  ("#56B4E9", "D"),   # Macron / Attal (En Marche -> LREM -> Renaissance)
    "LR":  ("#6A3D9A", "P"),   # Fillon / Pécresse / Retailleau
    "HOR": ("#C9AE00", "^"),   # Philippe
    "PP":  ("#CC79A7", "v"),   # Glucksmann
    "LE":  ("#009E73", "X"),   # Tondelier
    "PS":  ("#E69F00", "*"),   # Hamon
    "REC": ("#8B3A1A", "h"),   # Zemmour
}


def _c(name: str, party: str) -> tuple[str, str, str]:
    color, marker = PARTIES[party]
    return (name, color, marker)


@dataclass
class ElectionConfig:
    year: int
    page_title: str
    start: date                        # 1er avril de l'année précédant l'élection
    candidates: list[tuple[str, str, str]]   # (nom, couleur, marqueur)
    outfile: str
    aliases: dict[str, list[str]] = field(default_factory=dict)
    election_date: date | None = None  # jour du 1er tour, si l'élection a déjà eu lieu


CONFIGS: dict[int, ElectionConfig] = {
    2027: ElectionConfig(
        year=2027,
        page_title="Liste de sondages sur l'élection présidentielle française de 2027",
        start=date(2026, 4, 1),
        candidates=[
            _c("Le Pen", "RN"), _c("Mélenchon", "LFI"), _c("Philippe", "HOR"),
            _c("Attal", "RE"), _c("Glucksmann", "PP"), _c("Retailleau", "LR"),
            _c("Tondelier", "LE"),
        ],
        # En-tête alternatif : au 1er semestre 2026, la colonne du RN était
        # intitulée « Candidat RN » (candidature Le Pen / Bardella encore
        # indécise) : rattachée à Le Pen pour une courbe continue.
        aliases={"Le Pen": ["Le Pen", "Candidat RN"]},
        outfile="sondages_2027.png",
    ),
    2022: ElectionConfig(
        year=2022,
        page_title="Liste de sondages sur l'élection présidentielle française de 2022",
        start=date(2021, 4, 1),
        candidates=[
            _c("Macron", "RE"), _c("Le Pen", "RN"), _c("Mélenchon", "LFI"),
            _c("Zemmour", "REC"), _c("Pécresse", "LR"),
        ],
        outfile="sondages_2022.png",
        election_date=date(2022, 4, 10),
    ),
    2017: ElectionConfig(
        year=2017,
        page_title="Liste de sondages sur l'élection présidentielle française de 2017",
        start=date(2016, 4, 1),
        candidates=[
            _c("Macron", "RE"), _c("Le Pen", "RN"), _c("Fillon", "LR"),
            _c("Mélenchon", "LFI"), _c("Hamon", "PS"),
        ],
        outfile="sondages_2017.png",
        election_date=date(2017, 4, 23),
    ),
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
    # formes abrégées (« 21 avr. », courantes sur les tableaux de sondages
    # les plus récents d'une élection, par opposition aux lignes-résumé qui
    # écrivent le mois en toutes lettres)
    "janv": 1, "févr": 2, "fevr": 2, "avr": 4, "juil": 7,
    "sept": 9, "oct": 10, "nov": 11, "déc": 12, "dec": 12,
}


# ---------------------------------------------------------------------------
# 1. Récupération de la page
# ---------------------------------------------------------------------------
def fetch_page(page_title: str) -> tuple[str, str]:
    """Retourne (html_rendu, horodatage_derniere_revision) depuis l'API Wikipédia."""
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})

    # HTML rendu de la page
    r = s.get(API_URL, params={
        "action": "parse", "page": page_title, "prop": "text",
        "format": "json", "formatversion": 2, "disablelimitreport": 1,
    }, timeout=60)
    r.raise_for_status()
    html = r.json()["parse"]["text"]

    # Date de dernière modification (pour l'afficher sur le graphique)
    rev = ""
    try:
        rq = s.get(API_URL, params={
            "action": "query", "prop": "revisions", "titles": page_title,
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


_TIME_TAG_RE = re.compile(r'<time[^>]*datetime="([^"]+)"[^>]*>([^<]*)</time>')


def inject_explicit_years(html: str) -> str:
    """Les pages d'élections passées truffent leurs cellules de date de
    balises <time datetime="2017-04-21">21 avril</time> : l'année, absente
    du texte visible, est ré-injectée à partir de l'attribut « datetime »
    pour que parse_period() la retrouve sans avoir à la deviner.
    """
    def repl(m: re.Match) -> str:
        dt, text = m.group(1), m.group(2)
        if re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", dt) and dt[:4] not in text:
            return f"{text} {dt[:4]}"
        return text
    return _TIME_TAG_RE.sub(repl, html)


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
    txt = re.sub(r"\s*%\s*$", "", txt)                # pages archivées : suffixe "%" (la page en cours n'en a pas)
    if txt in ("", "—", "-", "–", "?", "nd", "n.d."):
        return None
    if re.fullmatch(r"\d+(?:[.,]\d+)?", txt):          # nombre pur
        return float(txt.replace(",", "."))
    if accept_name:                                    # nombre + nom : seulement si c'est le bon candidat
        m = re.match(r"(\d+(?:[.,]\d+)?)", txt)
        if m and _strip_accents(accept_name).lower() in _strip_accents(txt).lower():
            return float(m.group(1).replace(",", "."))
    return None                                        # autre chose (un autre nom) -> ignoré


def parse_period(text: str, election_year: int, year_hint: int | None = None,
                  month_hint: int | None = None) -> date | None:
    """Milieu de la période d'enquête. Ex : « 28 mai-2 juin », « 9-10 juillet ».

    Sur les pages archivées, beaucoup de tableaux mensuels n'écrivent que le
    jour dans chaque ligne (« du 17 au 19 ») : le mois n'apparaît que dans la
    légende du tableau (`<caption><time datetime="2017-03">Mars 2017</time>`).
    `month_hint`/`year_hint` (dérivés de cette légende ou, à défaut, du
    contexte de section) comblent ce trou quand le texte de la cellule ne
    contient lui-même aucun mot de mois.

    L'année de la borne de fin est résolue dans cet ordre de priorité :
      1. une année explicite à 4 chiffres présente dans le texte lui-même
         (les pages archivées 2017/2022 en ont une grâce à
         `inject_explicit_years` ; la page 2027, encore en édition, n'en a
         quasiment jamais) ;
      2. l'année indiquée par le contexte (légende du tableau, titre de
         section du type « Année 2026 », ou étiquette « Sondages de 2016 »
         d'un encadré repliable) ;
      3. à défaut, un mois de janvier à avril est supposé appartenir à
         l'année de l'élection, un mois de mai à décembre à l'année
         précédente — cohérent avec la fenêtre étudiée (avril année-1 à
         avril de l'année de l'élection).
    La borne de début hérite de la même année, sauf si elle tombe après la
    borne de fin dans le calendrier (période à cheval sur le nouvel an).
    """
    t = str(text).lower().replace("\xa0", " ")
    t = re.sub(r"\[[^\]]*\]", "", t)
    t = t.replace("1er", "1").replace("1ᵉʳ", "1")

    years_found = [int(y) for y in re.findall(r"\b20\d\d\b", t)]
    t_no_year = re.sub(r"\b20\d\d\b", " ", t)           # évite que "2017" ne soit lu comme des jours
    months = [(m.start(), FR_MONTHS[m.group()])
              for m in re.finditer("|".join(FR_MONTHS), t_no_year)]
    days = [int(m.group()) for m in re.finditer(r"\d{1,2}", t_no_year)]
    if not days:
        return None
    if months:
        start_m, end_m = months[0][1], months[-1][1]
    elif month_hint is not None:
        start_m = end_m = month_hint
    else:
        return None
    start_d, end_d = days[0], days[-1]

    if years_found:
        end_y = years_found[-1]
    elif year_hint is not None:
        end_y = year_hint
    else:
        end_y = election_year if end_m <= 4 else election_year - 1
    start_y = end_y - 1 if start_m > end_m else end_y   # période à cheval sur le nouvel an (déc -> janv)

    try:
        d0 = date(start_y, start_m, start_d)
        d1 = date(end_y, end_m, end_d)
    except ValueError:
        return None
    return date.fromordinal(round((d0.toordinal() + d1.toordinal()) / 2))


def table_month_hint(table_el) -> tuple[int, int] | None:
    """(année, mois) si le tableau porte une légende du type
    « Mars 2017 » (rendue par <caption><time datetime="2017-03">...) — les
    tableaux mensuels des pages archivées n'écrivent le mois que là. On lit
    le texte visible plutôt que l'attribut datetime : `inject_explicit_years`
    a déjà déballé la balise <time> à ce stade (elle agit sur toute la page)."""
    cap = table_el.find(".//caption")
    if cap is None:
        return None
    txt = cap.text_content().lower()
    ym = re.search(r"\b(20\d\d)\b", txt)
    mm = re.search("|".join(FR_MONTHS), txt)
    return (int(ym.group(1)), FR_MONTHS[mm.group()]) if (ym and mm) else None


def parse_table(table_el, config: ElectionConfig,
                 year_hint: int | None) -> tuple[list[dict], dict | None]:
    """Extrait un tableau de sondages : moyenne des hypothèses par sondage.

    Renvoie (records, resultat_reel). `resultat_reel` est un dict
    {candidat: valeur} tiré d'une éventuelle ligne « Résultats » — le
    résultat officiel du 1er tour, présent tel quel sur les pages
    d'élections passées — ou None si la ligne est absente (élection à venir).
    """
    df = pd.read_html(io.StringIO(LH.tostring(table_el, encoding="unicode")),
                      thousands=None)[0]
    # aplatit l'en-tête multi-niveaux vers les noms (niveau le plus bas)
    df.columns = [c[-1] if isinstance(c, tuple) else c for c in df.columns]
    cols = list(df.columns)

    def find(*needles):
        for needle in needles:
            for c in cols:
                if isinstance(c, str) and _strip_accents(needle).lower() in _strip_accents(c).lower():
                    return c
        return None

    sondeur_c, date_c = find("Sondeur"), find("Date")
    cand_cols = {name: find(*config.aliases.get(name, [name])) for name, _, _ in config.candidates}
    cand_cols = {k: v for k, v in cand_cols.items() if v is not None}
    if sondeur_c is None or date_c is None or len(cand_cols) < 3:
        return [], None  # pas un tableau de sondages de 1er tour

    work = pd.DataFrame({"sondeur": df[sondeur_c], "date_raw": df[date_c]})
    for name, col in cand_cols.items():
        work[name] = df[col].map(lambda c, n=name: parse_value(c, n))

    # lignes parasites (barre de couleurs, répétition d'en-tête, lignes vides)
    work = work[work["sondeur"].notna()]
    work["sondeur"] = work["sondeur"].astype(str).str.strip()
    work = work[work["sondeur"].str.lower() != "sondeur"]
    num_cols = list(cand_cols)
    work = work[work[num_cols].notna().any(axis=1)]
    if work.empty:
        return [], None

    # la ligne "Résultats" est le résultat officiel, pas un sondage : isolée.
    is_result = work["sondeur"].str.lower() == "résultats"
    final_result = None
    if is_result.any():
        row = work[is_result].iloc[0]
        final_result = {name: float(row[name]) for name in num_cols if pd.notna(row[name])}
    work = work[~is_result]
    if work.empty:
        return [], final_result

    # une valeur par candidat et par sondage = moyenne des hypothèses testées
    grouped = work.groupby(["sondeur", "date_raw"], sort=False)[num_cols].mean()

    # la légende du tableau (si elle existe) est plus précise que le hint de
    # section : elle prime pour l'année ET fournit le mois quand les lignes
    # elles-mêmes n'en donnent aucun (cf. table_month_hint).
    cap_hint = table_month_hint(table_el)
    eff_year_hint = cap_hint[0] if cap_hint else year_hint
    month_hint = cap_hint[1] if cap_hint else None

    records = []
    for (sondeur, date_raw), row in grouped.iterrows():
        d = parse_period(date_raw, config.year, eff_year_hint, month_hint)
        if d is None or d < config.start:
            continue
        for name in num_cols:
            v = row[name]
            if pd.notna(v):
                records.append({"date": d, "candidat": name,
                                "valeur": float(v), "sondeur": sondeur})
    return records, final_result


def extract_polls(html: str, config: ElectionConfig) -> tuple[pd.DataFrame, dict]:
    """Parcourt la page, ne garde que les tableaux « 1er tour » depuis START."""
    html = inject_explicit_years(html)
    tree = LH.fromstring(html)
    cur_h2, year_hint = "", None
    records: list[dict] = []
    final_results: dict = {}
    for el in tree.iter("h2", "h3", "h4", "h5", "div", "table"):
        if el.tag in ("h2", "h3", "h4", "h5"):
            if el.tag in ("h2", "h3"):
                # une nouvelle section h2/h3 (ex. « second tour » -> « premier
                # tour ») ne doit pas hériter de l'année d'une section
                # précédente sans rapport ; h4/h5 ne réinitialisent pas, car
                # ils héritent légitimement de l'année de leur h3 parent
                # (ex. « Année 2026 » (h3) > « Second semestre 2026 » (h4)).
                year_hint = None
            txt = el.text_content().split("[")[0].strip()
            if el.tag == "h2":
                cur_h2 = txt
            ym = re.search(r"\b(20\d\d)\b", txt)
            if ym:
                year_hint = int(ym.group(1))
        elif el.tag == "div":
            # encadrés repliables des pages archivées : « Sondages de 2016 »
            if "NavHead" in (el.get("class") or ""):
                ym = re.search(r"\b(20\d\d)\b", el.text_content())
                if ym:
                    year_hint = int(ym.group(1))
        else:  # table
            classes = el.get("class") or ""
            if "wikitable" not in classes:
                continue
            if "premier tour" not in _strip_accents(cur_h2).lower():
                continue
            recs, final_result = parse_table(el, config, year_hint)
            records.extend(recs)
            if final_result:
                final_results.update(final_result)

    df = pd.DataFrame(records)
    if df.empty:
        raise RuntimeError(f"Aucun sondage 1er tour depuis {config.start} trouvé pour "
                           f"{config.year} : la structure de la page a peut-être changé.")
    return df.sort_values("date").reset_index(drop=True), final_results


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
def make_chart(df: pd.DataFrame, config: ElectionConfig, final_results: dict,
               revision: str, outfile: str, show: bool):
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

    xmin = num(config.start)
    last_poll_x = df["date"].map(num).max()
    has_result = bool(final_results) and config.election_date is not None
    xmax = num(config.election_date) if has_result else last_poll_x
    grid = np.arange(xmin, last_poll_x + 1)

    end_labels = []  # (y, couleur, marqueur, texte) pour placement anti-collision
    for name, color, marker in config.candidates:
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

        final_v = final_results.get(name)
        if final_v is not None:
            # pont en pointillés entre le dernier sondage et le résultat réel,
            # marqué distinctement (cerclage foncé) pour qu'il ne soit jamais
            # confondu avec une simple estimation de sondage.
            ax.plot([gg[-1], xmax], [sm[-1], final_v], color=color, lw=1.3,
                    ls=(0, (1, 1.6)), zorder=3)
            ax.plot(xmax, final_v, marker=marker, color=color, ms=11,
                    markeredgecolor=INK, markeredgewidth=1.1, zorder=4, clip_on=False)
            end_labels.append([final_v, color, marker, f"{name} {final_v:.1f}"])
        else:
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
    data_max = max(df["valeur"].max(), max(final_results.values(), default=0))
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(0, max(40, data_max + 4))
    ax.xaxis.set_major_locator(mdates.MonthLocator())

    def _fr_month(x, _):
        dt = mdates.num2date(x)
        lab = FR_ABBR[dt.month]
        return f"{lab}\n{dt.year}" if dt.month == config.start.month else lab

    ax.xaxis.set_major_formatter(mticker.FuncFormatter(_fr_month))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.grid(axis="y", color=GRID, lw=1, zorder=0)
    ax.grid(axis="x", color=GRID, lw=0.6, alpha=0.6, zorder=0)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(length=0)

    ax.set_title(f"Présidentielle {config.year} — intentions de vote au 1er tour",
                 fontsize=17, fontweight="bold", loc="left", color=INK, pad=16)
    ax.text(0, -0.115, "Wikipédia", transform=ax.transAxes, fontsize=8, color=MUTED)

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
def build_once(config: ElectionConfig, outfile: str, show: bool) -> str:
    html, rev = fetch_page(config.page_title)
    df, final_results = extract_polls(html, config)
    make_chart(df, config, final_results, rev, outfile, show)
    return hashlib.md5(html.encode("utf-8")).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output", default=None,
                    help="fichier image de sortie (défaut : celui de l'élection choisie)")
    ap.add_argument("--election", choices=["2017", "2022", "2027", "all"], default="2027",
                    help="élection à tracer (défaut : 2027, la seule suivie en direct)")
    ap.add_argument("--show", action="store_true", help="afficher la fenêtre du graphique")
    ap.add_argument("--watch", type=float, metavar="MIN", default=None,
                    help="régénère toutes les MIN minutes si la page Wikipédia a changé "
                         "(une seule élection à la fois)")
    args = ap.parse_args()

    if not args.show:
        matplotlib.use("Agg")

    years = [2017, 2022, 2027] if args.election == "all" else [int(args.election)]
    if args.output and len(years) != 1:
        ap.error("-o/--output ne fonctionne qu'avec une seule élection à la fois "
                 "(sinon les trois graphiques s'écraseraient dans le même fichier)")

    if args.watch is None:
        for year in years:
            config = CONFIGS[year]
            build_once(config, args.output or config.outfile, args.show)
        return

    if len(years) != 1:
        ap.error("--watch ne fonctionne qu'avec une seule élection à la fois")
    config = CONFIGS[years[0]]
    outfile = args.output or config.outfile
    print(f"Mode veille : vérification toutes les {args.watch} min "
          f"(Ctrl+C pour arrêter).")
    last = None
    while True:
        try:
            html, rev = fetch_page(config.page_title)
            h = hashlib.md5(html.encode("utf-8")).hexdigest()
            if h != last:
                df, final_results = extract_polls(html, config)
                make_chart(df, config, final_results, rev, outfile, show=False)
                last = h
            else:
                print(f"[{datetime.now():%H:%M}] aucune modification.")
        except Exception as e:  # ne pas casser la boucle sur une erreur réseau
            print(f"[{datetime.now():%H:%M}] erreur : {e}", file=sys.stderr)
        time.sleep(args.watch * 60)


if __name__ == "__main__":
    main()

import html as html_module
import os
import re
import json
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import streamlit as st
import streamlit.components.v1 as components
import feedparser
import requests
from bs4 import BeautifulSoup
from mistralai import Mistral

# ==============================
# CONFIGURATION MISTRAL
# ==============================

client = Mistral(api_key="yLoE1iD8DZpbusRDpVQ44wmyc2uIqaTx")

MAX_CHARS = 12000  # Limite envoyée à Mistral

# Performance : chargement par lots (12 articles / flux), cache et parallélisme
MAX_ARTICLES_PAR_FLUX = 12
MAX_FEED_WORKERS = 6
MAX_IMAGE_WORKERS = 4
FEED_CACHE_TTL = 900

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


# ==============================
# FONCTION : EXTRACTION ARTICLE COMPLET
# ==============================

@st.cache_data(show_spinner=False)
def extraire_texte_article(url):
    paras = _extraire_paragraphes_web(url)
    return "\n\n".join(paras)


# ==============================
# FONCTION : NETTOYAGE HTML SIMPLE (fallback)
# ==============================

def nettoyer_html(raw_html):
    """Texte lisible sans balises ni entités HTML (&#8217; → ', etc.)."""
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    texte = html_module.unescape(soup.get_text(" ", strip=True))
    return re.sub(r"\s+", " ", texte).strip()


# ==============================
# FONCTION : GENERATION RESUME
# ==============================

def generer_resume(texte, n_points=5):
    try:
        n_points = max(1, min(5, int(n_points)))
        texte_utilise = texte[:MAX_CHARS]
        point_label = "point majeur" if n_points == 1 else "points majeurs"

        response = client.chat.complete(
            model="mistral-small-latest",
            messages=[
                {
                    "role": "user",
                    "content": f"""
Fais un résumé de l'article suivant en {n_points} {point_label}.

Le résumé commencera par une problématique générale formulée sous forme de question paradoxale.

Ensuite :
- {n_points} points différenciés par des emojis adaptés
- Chaque point contient, au format bullet point :
    • un titre court
    • une explication synthétique
    • si présent dans l'article : un chiffre ou exemple précis

Article :
{texte_utilise}
"""
                }
            ]
        )

        return response.choices[0].message.content

    except Exception as e:
        return f"Erreur Mistral : {str(e)}"


# ==============================
# FLUX RSS (inchangés)
# ==============================

# ==============================
# CATALOGUE FLUX RSS PAR PACKAGES
# ==============================

FEED_PACKAGES = {
    "geopolitique": {
        "name": "Géopolitique",
        "emoji": "🌍",
        "feeds": [
            {"name": "Institut Montaigne", "emoji": "🏛️", "url": "https://www.institutmontaigne.org/rss.xml"},
            {"name": "Telos", "emoji": "📚", "url": "https://www.telos-eu.com/fr/rss.xml"},
            {"name": "Diploweb", "emoji": "🗺️", "url": "https://www.diploweb.com/spip.php?page=backend"},
            {"name": "Les Yeux du Monde", "emoji": "👁", "url": "https://les-yeux-du-monde.fr/feed"},
            {"name": "IRIS vidéos", "emoji": "🎬", "url": "https://www.iris-france.org/feed/"},
            {"name": "Orient XXI", "emoji": "🕌", "url": "https://orientxxi.info/?page=backend&lang=fr"},
            {"name": "Regards sur l'Est", "emoji": "🧭", "url": "https://regard-est.com/feed"},
        ],
    },
    "litterature_philosophie": {
        "name": "Littérature et Philosophie",
        "emoji": "📖",
        "feeds": [
            {"name": "La Vie des idées", "emoji": "🧠", "url": "https://laviedesidees.fr/spip.php?page=backend"},
            {"name": "Philomédia", "emoji": "💭", "url": "https://www.philomedia.be/feed/"},
            {"name": "Acta Fabula", "emoji": "📜", "url": "https://www.fabula.org/lodel/acta/backend.php?format=rss092documents"},
            {"name": "Actu-Philosophia", "emoji": "🤔", "url": "https://www.actu-philosophia.com/feed/"},
        ],
    },
    "economie": {
        "name": "Économie",
        "emoji": "💶",
        "feeds": [
            {"name": "Institut Choiseul", "emoji": "💼", "url": "https://www.choiseul.info/feed"},
            {"name": "CEPII / OFCE", "emoji": "📊", "url": "https://www.cepii.fr/CEPII/rss/RSSLettre.asp"},
            {"name": "Observatoire des inégalités", "emoji": "⚖️", "url": "https://www.inegalites.fr/spip.php?page=backend"},
            {"name": "Alternatives Économiques", "emoji": "📰", "url": "https://blogs.alternatives-economiques.fr/rss.xml"},
        ],
    },
    "environnement_societe": {
        "name": "Environnement et société",
        "emoji": "🌱",
        "feeds": [
            {"name": "Terra Nova", "emoji": "🌍", "url": "https://tnova.fr/feed"},
            {"name": "Institut Jean Jaurès", "emoji": "✊", "url": "https://www.jean-jaures.org/publication/feed/"},
            {"name": "Baptiste Coulmont", "emoji": "📊", "url": "https://coulmont.com/feed/"},
            {"name": "OpenEdition Lectures", "emoji": "📚", "url": "https://journals.openedition.org/lectures/backend?format=rssdocuments&idcontainer=171"},
        ],
    },
    "custom_feed": {
        "name": "Créer mon propre feed",
        "emoji": "✨",
        "feeds": [],
        "custom": True,
    },
}

DEFAULT_ACTIVE_PACKAGES = ["geopolitique"]
MAX_ACTIVE_PACKAGES = 1

NOTEBOOKLM_URL = "https://notebooklm.google.com"

MOIS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre"
]


# ==============================
# HELPERS : METADONNEES ARTICLE
# ==============================

def _split_emoji(label):
    """Sépare l'emoji du nom de la source ('🧠 La Vie des Idées')."""
    parts = label.split(" ", 1)
    if len(parts) == 2 and not parts[0][0].isalnum():
        return parts[0], parts[1]
    return "📰", label


def _url_image_absolue(src, base_url=""):
    """Normalise une URL d'image (relative → absolue) et filtre les pixels de tracking."""
    if not src or not str(src).strip():
        return None
    src = str(src).strip()
    if src.startswith("data:"):
        return None
    abs_url = urljoin(base_url or "", src)
    if not abs_url.startswith("http"):
        return None
    low = abs_url.lower()
    if any(x in low for x in ("pixel", "spacer", "1x1", "blank.gif", "tracking", "beacon")):
        return None
    return abs_url


def _images_depuis_html(html_blob, base_url=""):
    """Liste d'images trouvées dans un bloc HTML (corps d'article RSS ou page)."""
    if not html_blob:
        return []
    soup = BeautifulSoup(html_blob, "html.parser")
    out = []
    seen = set()
    for img in soup.find_all("img"):
        for attr in ("src", "data-src", "data-lazy-src", "data-original", "data-srcset"):
            val = img.get(attr)
            if not val:
                continue
            if attr == "data-srcset":
                val = val.split(",")[0].strip().split()[0]
            url = _url_image_absolue(val, base_url)
            if url and url not in seen:
                seen.add(url)
                out.append(url)
            break
    return out


def _extraire_logo_site(soup, base_url):
    """Logo ou icône du site en dernier recours."""
    for sel, attr in (
        ('link[rel="apple-touch-icon"]', "href"),
        ('link[rel="icon"][sizes="192x192"]', "href"),
        ('link[rel="icon"][sizes="512x512"]', "href"),
        ('link[rel="icon"]', "href"),
        ('link[rel="shortcut icon"]', "href"),
        ('meta[property="og:logo"]', "content"),
        ('meta[name="msapplication-TileImage"]', "content"),
    ):
        el = soup.select_one(sel)
        if el and el.get(attr):
            url = _url_image_absolue(el.get(attr), base_url)
            if url:
                return url
    for img in soup.select(
        "header img.logo, header .logo img, .site-logo img, #logo img, "
        "a.logo img, .custom-logo, .navbar-brand img"
    ):
        url = _url_image_absolue(img.get("src") or img.get("data-src"), base_url)
        if url:
            return url
    return None


def _extraire_image(entry):
    """Trouve la meilleure image disponible pour un article de flux."""
    base = entry.get("link", "") or ""
    candidats = []

    for enc in entry.get("enclosures", []) or []:
        href = enc.get("href") or enc.get("url")
        if href and ("image" in (enc.get("type", "") or "")
                     or re.search(r"\.(jpe?g|png|webp|gif|svg)", href, re.I)):
            candidats.append(href)

    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure":
            href = link.get("href")
            if href and ("image" in (link.get("type", "") or "")
                         or re.search(r"\.(jpe?g|png|webp|gif|svg)", href, re.I)):
                candidats.append(href)

    for media in (entry.get("media_content") or []):
        if media.get("url"):
            candidats.append(media["url"])
    for thumb in (entry.get("media_thumbnail") or []):
        if thumb.get("url"):
            candidats.append(thumb["url"])

    html_parts = []
    if entry.get("content"):
        for block in entry["content"]:
            html_parts.append(block.get("value", "") or "")
    html_parts.append(entry.get("summary", "") or "")
    for html_blob in html_parts:
        candidats.extend(_images_depuis_html(html_blob, base))

    for src in candidats:
        url = _url_image_absolue(src, base) or (src if str(src).startswith("http") else None)
        if url:
            return url
    return None


def _date_lisible(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return "", 0
    try:
        dt = datetime(*parsed[:6])
        return f"{dt.day} {MOIS_FR[dt.month - 1]} {dt.year}", time.mktime(parsed)
    except Exception:
        return "", 0


def _excerpt(summary_html, limite=240):
    texte = nettoyer_html(summary_html or "")
    texte = re.sub(r"\s+", " ", texte).strip()
    if len(texte) <= limite:
        return texte
    coupe = texte[:limite].rsplit(" ", 1)[0]
    return coupe + "…"


def _titre_incomplet(titre):
    t = re.sub(r"\s+", " ", (titre or "").strip())
    if not t:
        return True
    return t.lower().rstrip(".") in ("sans titre", "untitled", "(no title)", "no title")


@st.cache_data(show_spinner=False, ttl=3600)
def _metadonnees_page(url):
    """Complète titre / image depuis la page web (articles RSS incomplets)."""
    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=12)
        response.raise_for_status()
        if _page_est_bloquee(response.text):
            return {"title": "", "image": None}
        soup = BeautifulSoup(response.text, "html.parser")

        titre = None
        for sel, attr in (
            ('meta[property="og:title"]', "content"),
            ('meta[name="twitter:title"]', "content"),
            ("h1", None),
            ("title", None),
        ):
            el = soup.select_one(sel)
            if not el:
                continue
            val = (el.get(attr) if attr else el.get_text(" ", strip=True)) or ""
            val = re.sub(r"\s+", " ", val).strip()
            if val and not _titre_incomplet(val):
                titre = val
                break

        image = None
        for sel in (
            'meta[property="og:image"]',
            'meta[property="og:image:url"]',
            'meta[name="twitter:image"]',
            'meta[name="twitter:image:src"]',
        ):
            el = soup.select_one(sel)
            if el and el.get("content"):
                image = _url_image_absolue(el["content"], url)
                if image:
                    break

        if not image:
            for sel in (
                "article img",
                "main img",
                ".texte img",
                "#text-content img",
                ".entry-content img",
                ".post-content img",
                ".article-body img",
                "[itemprop=articleBody] img",
            ):
                for img in soup.select(sel):
                    image = _url_image_absolue(img.get("src") or img.get("data-src"), url)
                    if image:
                        break
                if image:
                    break

        if not image:
            image = _extraire_logo_site(soup, url)

        return {"title": titre or "", "image": image}
    except Exception:
        return {"title": "", "image": None}


def _enrichir_metadonnees(article):
    """Scraping léger si le flux RSS n'a pas de titre ou d'image."""
    if not article.get("link"):
        return article
    needs_title = _titre_incomplet(article.get("title"))
    needs_image = not article.get("image")
    if not needs_title and not needs_image:
        return article

    meta = _metadonnees_page(article["link"])
    if needs_title and meta.get("title"):
        article["title"] = meta["title"]
    if needs_image and meta.get("image"):
        article["image"] = meta["image"]
    return article


def _article_depuis_entry(entry, nom, emoji, i):
    lien = entry.get("link", "")
    if not lien:
        return None
    summary_html = entry.get("summary", "")
    body_html = _best_feed_html(entry)
    date_txt, ts = _date_lisible(entry)
    image = _extraire_image(entry)
    if not image and body_html:
        imgs = _images_depuis_html(body_html, lien)
        if imgs:
            image = imgs[0]
    art = {
        "id": hashlib.md5(lien.encode("utf-8")).hexdigest()[:12],
        "source": nom,
        "emoji": emoji,
        "title": (entry.get("title") or "Sans titre").strip(),
        "author": entry.get("author", "") or "",
        "link": lien,
        "date": date_txt,
        "timestamp": ts,
        "excerpt": _excerpt(summary_html or body_html),
        "summary_html": summary_html,
        "body_html": body_html,
        "image": image,
        "featured": i == 0 and bool(image),
    }
    art = _enrichir_metadonnees(art)
    if i == 0 and art.get("image"):
        art["featured"] = True
    return art


def _packages_payload():
    """Catalogue complet pour le composant front."""
    out = []
    for pkg_id, pkg in FEED_PACKAGES.items():
        out.append({
            "id": pkg_id,
            "name": pkg["name"],
            "emoji": pkg["emoji"],
            "feeds": pkg["feeds"],
            "custom": bool(pkg.get("custom")),
        })
    return out


def _normalize_active_packages(active_packages):
    valid = [p for p in (active_packages or []) if p in FEED_PACKAGES]
    if not valid:
        valid = DEFAULT_ACTIVE_PACKAGES.copy()
    return valid[:MAX_ACTIVE_PACKAGES]


def _feeds_from_packages(active_packages, disabled=None):
    """Flux issus des packages actifs, hors flux désactivés individuellement."""
    disabled = set(disabled or [])
    feeds = []
    seen = set()
    for pkg_id in _normalize_active_packages(active_packages):
        for f in FEED_PACKAGES[pkg_id]["feeds"]:
            url = f["url"]
            if url in disabled or url in seen:
                continue
            seen.add(url)
            feeds.append(f)
    return feeds


def _lister_sources(active_packages, custom_feeds, disabled=None):
    """Médias visibles dans Filtrer (packages actifs + flux perso uniquement)."""
    disabled = set(disabled or [])
    sources = []
    seen = set()
    for f in _feeds_from_packages(active_packages, disabled):
        emoji = f.get("emoji") or "📰"
        nom = f.get("name") or "Source"
        key = f"{emoji}|{nom}"
        if key not in seen:
            seen.add(key)
            sources.append({"key": key, "name": nom, "emoji": emoji})
    for f in custom_feeds or []:
        url = (f.get("url") or "").strip()
        if not url or url in disabled:
            continue
        emoji = (f.get("emoji") or "📰").strip() or "📰"
        nom = (f.get("name") or "Source").strip()
        key = f"{emoji}|{nom}"
        if key not in seen:
            seen.add(key)
            sources.append({"key": key, "name": nom, "emoji": emoji})
    return sources


def _jobs_from_feeds_key(feeds_key):
    """Liste ordonnée des flux actifs (url, nom, emoji)."""
    disabled = set()
    custom_feeds = []
    active_packages = DEFAULT_ACTIVE_PACKAGES.copy()

    try:
        parsed = json.loads(feeds_key or "{}")
        if isinstance(parsed, list):
            custom_feeds = parsed
        else:
            custom_feeds = parsed.get("custom", []) or []
            disabled = set(parsed.get("disabled", []) or [])
            active_packages = _normalize_active_packages(
                parsed.get("active_packages") or DEFAULT_ACTIVE_PACKAGES
            )
    except (json.JSONDecodeError, TypeError):
        custom_feeds = []

    package_urls = set()
    jobs = []
    for f in _feeds_from_packages(active_packages, disabled):
        url = f["url"]
        package_urls.add(url)
        jobs.append((url, f.get("name") or "Source", f.get("emoji") or "📰"))

    for f in custom_feeds:
        url = (f.get("url") or "").strip()
        if not url or url in disabled or url in package_urls:
            continue
        jobs.append((
            url,
            (f.get("name") or "Source").strip(),
            (f.get("emoji") or "📰").strip() or "📰",
        ))
    return jobs


def _round_robin_merge(batches):
    if not batches:
        return []
    merged = []
    max_len = max(len(b) for b in batches)
    for i in range(max_len):
        for batch in batches:
            if i < len(batch):
                merged.append(batch[i])
    return merged


@st.cache_data(show_spinner=False, ttl=FEED_CACHE_TTL)
def _articles_depuis_flux(url, name, emoji, offset, limit):
    """Lot d'articles d'un flux (cache par URL + offset). Images enrichies via scraping."""
    try:
        flux = feedparser.parse(url, request_headers=REQUEST_HEADERS)
    except Exception:
        return [], False
    entries = list(flux.entries[offset:offset + limit])
    has_more = len(flux.entries) > offset + limit
    if not entries:
        return [], has_more

    articles = []
    workers = min(MAX_IMAGE_WORKERS, len(entries))

    def _build(entry_i):
        entry, idx = entry_i
        return _article_depuis_entry(entry, name, emoji, idx)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for art in pool.map(_build, [(e, offset + i) for i, e in enumerate(entries)]):
            if art:
                articles.append(art)
    return articles, has_more


def _collecter_lot(jobs, feed_offsets=None, limit=MAX_ARTICLES_PAR_FLUX):
    """Charge un lot d'articles en parallèle et fusionne en round-robin."""
    if not jobs:
        return [], False, {}

    offsets = dict(feed_offsets or {})
    batches_by_url = {}
    has_more = False
    workers = min(MAX_FEED_WORKERS, len(jobs))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {}
        for url, name, emoji in jobs:
            off = offsets.get(url, 0)
            fut = pool.submit(_articles_depuis_flux, url, name, emoji, off, limit)
            future_map[fut] = (url, off)

        for fut in as_completed(future_map):
            url, off = future_map[fut]
            try:
                batch, feed_has_more = fut.result()
            except Exception:
                batch, feed_has_more = [], False
            batches_by_url[url] = batch
            offsets[url] = off + len(batch)
            if feed_has_more:
                has_more = True

    batches = [batches_by_url.get(url, []) for url, _, _ in jobs]
    return _round_robin_merge(batches), has_more, offsets


def _reinitialiser_pool_articles(feeds_key):
    jobs = _jobs_from_feeds_key(feeds_key)
    batch, has_more, offsets = _collecter_lot(jobs, {})
    st.session_state.articles_feeds_key = feeds_key
    st.session_state.feed_offsets = offsets
    st.session_state.articles_pool = batch
    st.session_state.feeds_has_more = has_more


def _feeds_cache_key(custom_feeds, disabled=None, active_packages=None):
    normalized = []
    for f in custom_feeds or []:
        normalized.append({
            "url": f.get("url", ""),
            "name": f.get("name", ""),
            "emoji": f.get("emoji", ""),
        })
    payload = {
        "custom": sorted(normalized, key=lambda f: f.get("url", "")),
        "disabled": sorted(set(disabled or [])),
        "active_packages": sorted(_normalize_active_packages(active_packages)),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _best_feed_html(entry):
    """Meilleur HTML disponible dans le flux (content:encoded ou summary)."""
    summary = entry.get("summary", "") or ""
    content = ""
    if entry.get("content"):
        content = entry["content"][0].get("value", "") or ""
    if len(nettoyer_html(content)) >= len(nettoyer_html(summary)):
        return content or summary
    return summary or content


def html_en_paragraphes(html):
    """Convertit un bloc HTML (flux RSS ou page) en paragraphes lisibles."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()
    paras = []
    for el in soup.find_all(["p", "li", "h2", "h3", "blockquote"]):
        txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if len(txt) > 40:
            paras.append(txt)
    if not paras:
        txt = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
        if len(txt) > 80:
            paras = [txt]
    plain = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
    if len(plain) > sum(len(p) for p in paras) + 150 and len(plain) > 400:
        morceaux = re.split(r'(?<=[.!?…])\s+(?=[A-ZÀ-ÖØ-Þ«""(])', plain)
        morceaux = [m.strip() for m in morceaux if len(m.strip()) > 40]
        if morceaux:
            return morceaux
    return paras


def _paragraphes_depuis_root(root):
    """Extrait les paragraphes d'un bloc HTML racine."""
    paras = []
    for el in root.find_all(["p", "li", "h2", "h3", "blockquote"]):
        txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if len(txt) > 40:
            paras.append(txt)
    return paras


def _page_est_bloquee(html_text):
    """Détecte les pages anti-bot (Anubis, Cloudflare…) sans contenu article."""
    blob = html_text or ""
    low = blob.lower()
    if len(blob) < 8000 and any(
        marker in low
        for marker in (
            "anubis",
            "challenge__logo",
            "filtrer les robots",
            "checking your browser",
            "cf-browser-verification",
            "just a moment",
        )
    ):
        return True
    return False


def _extraire_paragraphes_web(url):
    """Extraction full-text depuis la page web de l'article."""
    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
        response.raise_for_status()
        if _page_est_bloquee(response.text):
            return []
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "iframe"]):
            tag.decompose()

        selectors = [
            "#text-content",          # OpenEdition (Lodel)
            ".entry-content.texte",   # SPIP (La Vie des Idées, Diploweb…)
            ".texte",
            "article .field--name-body",
            "article .node__content",
            ".article-body",
            ".article-content",
            ".post-content",
            "article .entry-content",
            "[itemprop=articleBody]",
            "article",
            "main",
            "[role=main]",
            ".entry-content",
        ]
        best = []
        seen_roots = set()
        for sel in selectors:
            for root in soup.select(sel):
                if id(root) in seen_roots:
                    continue
                if "chapo" in (root.get("class") or []):
                    continue
                seen_roots.add(id(root))
                paras = _paragraphes_depuis_root(root)
                if sum(len(p) for p in paras) > sum(len(p) for p in best):
                    best = paras

        if best:
            return best

        root = soup.body or soup
        for tag in root.find_all(["header", "footer", "nav", "aside", "form"]):
            tag.decompose()
        return _paragraphes_depuis_root(root)
    except Exception:
        return []


def obtenir_paragraphes(article):
    """Texte intégral : page web et flux RSS — on garde la source la plus riche."""
    url = article.get("link", "")
    fallback_html = article.get("body_html") or article.get("summary_html") or ""
    web_paras = _extraire_paragraphes_web(url) if url else []
    html_paras = html_en_paragraphes(fallback_html)
    web_len = sum(len(p) for p in web_paras)
    html_len = sum(len(p) for p in html_paras)
    # Ne pas laisser un court résumé RSS l'emporter sur un texte web substantiel
    if web_len >= 600 and html_len < web_len * 1.25:
        return web_paras
    if html_len > web_len:
        return html_paras
    if web_len >= 400:
        return web_paras
    return html_paras or web_paras


@st.cache_data(show_spinner=False)
def resume_pour_url(url, fallback_html, n_points=5):
    texte = extraire_texte_article(url)
    if len(texte) < 800:
        texte = nettoyer_html(fallback_html)
    return generer_resume(texte, n_points)


# ==============================
# COMPOSANT FLIPBOARD
# ==============================

_DIR = os.path.dirname(os.path.abspath(__file__))
# Nom versionné pour invalider le cache navigateur après déploiement
# (Streamlit sert le composant sous app.<name>/index.html).
_flipboard = components.declare_component(
    "flipboard_magazine_v11",
    path=os.path.join(_DIR, "flipboard_component"),
)


def flipboard(articles, enrichments):
    return _flipboard(
        articles=articles,
        enrichments=enrichments,
        default=None,
        key="flipboard_magazine_v11",
    )


# ==============================
# INTERFACE
# ==============================

st.set_page_config(page_title="Verto", layout="wide")

st.markdown(
    """
    <style>
    #MainMenu, header, footer {visibility: hidden;}
    [data-testid="stToolbar"] {display: none;}
    [data-testid="stDecoration"] {display: none;}
    .stApp {background: #0a1440;}
    .block-container {padding: 0.3rem 0.5rem 0 0.5rem; max-width: 100%;}
    [data-testid="stAppViewBlockContainer"] {padding: 0.3rem 0.5rem 0 0.5rem;}
    iframe {border: none !important;}
    </style>
    """,
    unsafe_allow_html=True,
)

# Etat des enrichissements (texte intégral + résumé IA), conservé en session
if "enrich_text" not in st.session_state:
    st.session_state.enrich_text = {}
if "enrich_summary" not in st.session_state:
    st.session_state.enrich_summary = {}
if "last_nonce" not in st.session_state:
    st.session_state.last_nonce = None
if "custom_feeds" not in st.session_state:
    st.session_state.custom_feeds = []
if "disabled_feeds" not in st.session_state:
    st.session_state.disabled_feeds = []
if "active_packages" not in st.session_state:
    st.session_state.active_packages = DEFAULT_ACTIVE_PACKAGES.copy()
if "articles_feeds_key" not in st.session_state:
    st.session_state.articles_feeds_key = None
if "articles_pool" not in st.session_state:
    st.session_state.articles_pool = []
if "feed_offsets" not in st.session_state:
    st.session_state.feed_offsets = {}
if "feeds_has_more" not in st.session_state:
    st.session_state.feeds_has_more = False

feeds_key = _feeds_cache_key(
    st.session_state.custom_feeds,
    st.session_state.disabled_feeds,
    st.session_state.active_packages,
)

if st.session_state.articles_feeds_key != feeds_key:
    _reinitialiser_pool_articles(feeds_key)

articles = st.session_state.articles_pool
index = {a["id"]: a for a in articles}

enrichments = {
    "text": st.session_state.enrich_text,
    "summary": st.session_state.enrich_summary,
    "notebooklm": NOTEBOOKLM_URL,
    "custom_feeds": st.session_state.custom_feeds,
    "feed_packages": _packages_payload(),
    "active_packages": st.session_state.active_packages,
    "disabled_feeds": st.session_state.disabled_feeds,
    "has_more": st.session_state.feeds_has_more,
    "sources": _lister_sources(
        st.session_state.active_packages,
        st.session_state.custom_feeds,
        st.session_state.disabled_feeds,
    ),
}

valeur = flipboard(articles, enrichments)

# Traitement des actions renvoyées par le composant
if isinstance(valeur, dict) and valeur.get("nonce") != st.session_state.last_nonce:
    st.session_state.last_nonce = valeur.get("nonce")

    if valeur.get("action") == "feeds":
        new_feeds = valeur.get("feeds") or []
        new_disabled = valeur.get("disabled_feeds")
        if new_disabled is None:
            new_disabled = valeur.get("disabled_defaults") or []
        new_packages = _normalize_active_packages(
            valeur.get("active_packages") or DEFAULT_ACTIVE_PACKAGES
        )
        changed = False
        if new_feeds != st.session_state.custom_feeds:
            st.session_state.custom_feeds = new_feeds
            changed = True
        if new_disabled != st.session_state.disabled_feeds:
            st.session_state.disabled_feeds = new_disabled
            changed = True
        if new_packages != st.session_state.active_packages:
            st.session_state.active_packages = new_packages
            changed = True
        if changed:
            _articles_depuis_flux.clear()
            st.session_state.articles_feeds_key = None
            st.rerun()
    elif valeur.get("action") == "load_more":
        jobs = _jobs_from_feeds_key(feeds_key)
        batch, has_more, offsets = _collecter_lot(
            jobs, st.session_state.feed_offsets
        )
        if batch:
            seen = {a["id"] for a in st.session_state.articles_pool}
            nouveaux = [a for a in batch if a["id"] not in seen]
            st.session_state.articles_pool.extend(nouveaux)
        st.session_state.feed_offsets = offsets
        st.session_state.feeds_has_more = has_more
        st.rerun()
    elif valeur.get("id"):
        aid = valeur.get("id")
        want = valeur.get("want")
        art = index.get(aid)

        if art:
            if want in ("text", "both") and aid not in st.session_state.enrich_text:
                st.session_state.enrich_text[aid] = obtenir_paragraphes(art)

            if want in ("summary", "both") and aid not in st.session_state.enrich_summary:
                n_points = valeur.get("summary_points", 5)
                try:
                    n_points = max(1, min(5, int(n_points)))
                except (TypeError, ValueError):
                    n_points = 5
                st.session_state.enrich_summary[aid] = {
                    "text": resume_pour_url(
                        art["link"], art["summary_html"], n_points
                    ),
                    "points": n_points,
                }

        st.rerun()

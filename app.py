"""Streamlit entrypoint for Verto (plan B). FastAPI lives in main.py (plan A)."""

import os

import streamlit as st
import streamlit.components.v1 as components

from verto.service import VertoSession

_DIR = os.path.dirname(os.path.abspath(__file__))
_flipboard = components.declare_component(
    "flipboard_magazine_v15",
    path=os.path.join(_DIR, "flipboard_component"),
)


def flipboard(articles, enrichments):
    return _flipboard(
        articles=articles,
        enrichments=enrichments,
        default=None,
        key="flipboard_magazine_v15",
    )


def _session_from_streamlit() -> VertoSession:
    defaults = {
        "enrich_text": {},
        "enrich_summary": {},
        "custom_feeds": [],
        "disabled_feeds": [],
        "active_packages": None,
        "articles_feeds_key": None,
        "articles_pool": [],
        "feed_offsets": {},
        "feeds_has_more": False,
        "gemini_api_key": "",
    }
    data = {}
    for key, default in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default if default is not None else []
        data[key] = st.session_state[key]
    if data["active_packages"] is None:
        from verto.core import DEFAULT_ACTIVE_PACKAGES

        data["active_packages"] = DEFAULT_ACTIVE_PACKAGES.copy()
        st.session_state.active_packages = data["active_packages"]
    return VertoSession.from_dict(data)


def _persist_session(session: VertoSession) -> None:
    for key, value in session.to_dict().items():
        st.session_state[key] = value


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

if "last_nonce" not in st.session_state:
    st.session_state.last_nonce = None

session = _session_from_streamlit()
session.ensure_articles()
_persist_session(session)

payload = session.bootstrap_payload()
valeur = flipboard(payload["articles"], payload["enrichments"])

if isinstance(valeur, dict) and valeur.get("nonce") != st.session_state.last_nonce:
    st.session_state.last_nonce = valeur.get("nonce")

    if valeur.get("action") == "settings":
        session.update_settings(valeur.get("gemini_api_key"))
    elif valeur.get("action") == "feeds":
        new_disabled = valeur.get("disabled_feeds")
        if new_disabled is None:
            new_disabled = valeur.get("disabled_defaults") or []
        if session.update_feeds(
            valeur.get("feeds") or [],
            new_disabled,
            valeur.get("active_packages"),
        ):
            _persist_session(session)
            st.rerun()
    elif valeur.get("action") == "load_more":
        session.load_more()
        _persist_session(session)
        st.rerun()
    elif valeur.get("id"):
        aid = valeur.get("id")
        want = valeur.get("want")
        art = session.article_index().get(aid)
        if art:
            if want in ("text", "both"):
                session.enrich_text_for(aid, art)
            if want in ("summary", "both"):
                try:
                    n_points = max(1, min(5, int(valeur.get("summary_points", 5))))
                except (TypeError, ValueError):
                    n_points = 5
                session.enrich_summary_for(
                    aid,
                    art,
                    n_points=n_points,
                    api_key=valeur.get("gemini_api_key"),
                )
        _persist_session(session)
        st.rerun()

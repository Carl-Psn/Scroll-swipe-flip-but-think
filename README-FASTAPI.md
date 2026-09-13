# Verto — FastAPI (plan A)

Cette branche ajoute une version **FastAPI** du magazine Verto. La version **Streamlit** reste disponible via `app.py` (plan B, branche `main`).

## Lancer FastAPI

```bash
pip install -r requirements-fastapi.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Ouvrir http://localhost:8000

## Lancer Streamlit (plan B)

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Architecture

```
flipboard_component/   # Front HTML/JS (inchangé visuellement)
verto/                 # Logique métier partagée (RSS, scraping, Gemini)
main.py                # API REST FastAPI
app.py                 # Shell Streamlit (plan B)
```

## Endpoints API

| Méthode | Route | Rôle |
|---------|-------|------|
| `GET` | `/api/bootstrap` | Articles + enrichissements initiaux |
| `PUT` | `/api/settings` | Clé API Gemini |
| `PUT` | `/api/feeds` | Configuration des flux |
| `POST` | `/api/articles/load-more` | Pagination du fil |
| `POST` | `/api/articles/{id}/text` | Texte intégral |
| `POST` | `/api/articles/{id}/summary` | Résumé Gemini |

La session est portée par un cookie `verto_session`.

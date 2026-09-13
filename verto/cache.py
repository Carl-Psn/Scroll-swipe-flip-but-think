"""TTL caches replacing Streamlit @st.cache_data."""

from __future__ import annotations

from functools import wraps

from cachetools import TTLCache

_caches: dict[str, TTLCache] = {}


def cached(*, ttl: int | None = None, maxsize: int = 2048):
    """Decorator with optional TTL and a .clear() method on the wrapper."""

    def decorator(fn):
        store = TTLCache(maxsize=maxsize, ttl=ttl or 604800)
        _caches[fn.__name__] = store

        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            try:
                return store[key]
            except KeyError:
                value = fn(*args, **kwargs)
                store[key] = value
                return value

        def clear():
            store.clear()

        wrapper.clear = clear
        wrapper.cache = store
        return wrapper

    return decorator


def clear_feed_cache():
    from verto.core import articles_depuis_flux

    articles_depuis_flux.clear()

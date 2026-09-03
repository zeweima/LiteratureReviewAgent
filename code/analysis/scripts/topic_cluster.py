"""
Deterministic topic-clustering core for Tier-4 narrative synthesis (Layer 1 of it).

Embeds short text units (summary_bullets, future_directions, limitations,
keywords, ...) and clusters them so each cluster is a candidate *theme*. This is
the "discover" half of the hybrid strategy: the clusters + their characteristic
terms and representative bullets are what a human (or, next step, a Claude Code
SDK call) turns into labelled themes.

Backends
--------
* ``tfidf`` (default) -- fully offline, deterministic, no model download. Good
  enough to eyeball cluster structure and draft a codebook.
* ``sbert`` -- sentence-transformers embeddings (semantically stronger). Enable
  once the model can be downloaded; the rest of the pipeline is identical.

Everything is deterministic (no random seeds needed for agglomerative; tfidf is
fixed) so re-runs are stable and auditable.
"""

from __future__ import annotations

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def embed_tfidf(texts: list[str]):
    """Offline TF-IDF embedding (1-2 grams, english stopwords)."""
    vec = TfidfVectorizer(
        stop_words="english",
        ngram_range=(1, 2),
        min_df=2,
        max_df=0.6,
        sublinear_tf=True,
    )
    X = vec.fit_transform(texts)
    return X.toarray(), vec


def _patch_windows_ssl() -> None:
    """Use certifi's CA bundle instead of the (often malformed) Windows cert store.

    Some Windows setups raise ``SSLError: NOT_ENOUGH_DATA`` when sentence_transformers
    (via aiohttp/huggingface_hub) builds an SSL context. Must run BEFORE the import.
    """
    import os
    import ssl
    try:
        import certifi
    except ImportError:
        return
    cafile = certifi.where()
    _orig = ssl.create_default_context

    def _ctx(purpose=ssl.Purpose.SERVER_AUTH, *, cafile=None, capath=None, cadata=None):
        if cafile is None and capath is None and cadata is None:
            cafile = certifi.where()
        return _orig(purpose, cafile=cafile, capath=capath, cadata=cadata)

    ssl.create_default_context = _ctx
    os.environ.setdefault("SSL_CERT_FILE", cafile)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", cafile)


def embed_sbert(texts: list[str], model_name: str = "all-MiniLM-L6-v2"):
    """Sentence-transformers embedding (downloads the model on first use)."""
    _patch_windows_ssl()
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(model_name)
    return np.asarray(model.encode(texts, normalize_embeddings=True)), None


def cluster_texts(X, n_clusters: int):
    """Agglomerative Ward clustering (deterministic, balanced clusters).

    Ward + Euclidean on L2-normalized vectors avoids the one-mega-cluster failure
    of average/cosine linkage on dense sentence embeddings. Both backends are
    L2-normalized (sbert via normalize_embeddings, TF-IDF via norm='l2'), so
    Euclidean distance is monotonic with cosine here.
    """
    from sklearn.preprocessing import normalize
    n_clusters = max(2, min(n_clusters, X.shape[0] - 1))
    Xn = normalize(X)  # ensure unit norm (no-op if already normalized)
    model = AgglomerativeClustering(n_clusters=n_clusters, linkage="ward")
    return model.fit_predict(Xn)


def top_terms(X, labels, vec, topn: int = 8) -> dict[int, list[str]]:
    """Characteristic terms per cluster from the mean TF-IDF vector."""
    if vec is None:
        return {}
    terms = np.array(vec.get_feature_names_out())
    out = {}
    for c in sorted(set(labels)):
        mask = labels == c
        centroid = X[mask].mean(axis=0)
        out[c] = terms[np.argsort(centroid)[::-1][:topn]].tolist()
    return out


def representatives(X, labels, texts, n: int = 3) -> dict[int, list[str]]:
    """The n texts closest to each cluster centroid (medoid-style)."""
    out = {}
    for c in sorted(set(labels)):
        idx = np.where(labels == c)[0]
        centroid = X[idx].mean(axis=0, keepdims=True)
        sims = cosine_similarity(X[idx], centroid).ravel()
        order = idx[np.argsort(sims)[::-1][:n]]
        out[c] = [texts[i] for i in order]
    return out

"""In-eigenvector centrality via power iteration (paper §4.2 fingerprint 1)."""
from __future__ import annotations

import numpy as np


def power_iteration_in_eigen_centrality(
    W: np.ndarray,
    iters: int = 100,
    tol: float = 1e-6,
) -> np.ndarray:
    """Approximate the dominant left-eigenvector of W (i.e. eigenvector of W.T).

    "In-eigenvector" centrality weights each node by the centrality of its
    in-neighbors, so we iterate on the transpose of the weight matrix. The
    iterate is L1-normalised every step; for a totally disconnected graph the
    function returns a uniform vector instead of dividing by zero.

    Args:
        W: dense (V, V) non-negative weight matrix. ``W[i, j]`` is the flow
            from population i to j (edge i -> j).
        iters: hard cap on iterations.
        tol: L1 convergence tolerance.

    Returns:
        (V,) float32 centrality vector summing to 1.
    """
    V = W.shape[0]
    if V == 0:
        return np.zeros(0, dtype=np.float32)

    A = W.T.astype(np.float32)
    if float(np.abs(A).sum()) <= 0.0:
        return np.full(V, 1.0 / V, dtype=np.float32)

    # DTDG snapshots can be acyclic or sink-heavy. A small self-loop keeps
    # centrality mass on receiver hubs instead of collapsing to a zero vector.
    A = A + np.eye(V, dtype=np.float32)
    c = np.full(V, 1.0 / V, dtype=np.float32)
    for _ in range(iters):
        c_next = A @ c
        norm = float(np.abs(c_next).sum())
        if norm <= 0.0:
            return np.full(V, 1.0 / V, dtype=np.float32)
        c_next = c_next / norm
        if float(np.abs(c_next - c).sum()) < tol:
            c = c_next
            break
        c = c_next
    return c.astype(np.float32)

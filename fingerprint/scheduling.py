"""Small offline prediction profiles, distinct from the traces replayed by the NoC."""
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Sequence

import numpy as np

from .workload import (
    Workload,
    _integer_vector,
    _metadata_copy,
    _nonnegative_array,
    _readonly,
)


SCHEMA_VERSION = 1
KIND = "scheduling_fingerprint"
UNITS = {
    "traffic": "expected_flits_per_logical_tick",
    "compute": "total_sops_per_logical_tick",
    "memory": "MB",
}
ARRAY_FIELDS = (
    "pop_size", "edge_src", "edge_dst", "expected_edge_flits", "compute_total_sops",
)
VALUE_FIELDS = (*ARRAY_FIELDS, "state_size_mb", "source", "metadata")
DOCUMENT_FIELDS = {*VALUE_FIELDS, "schema_version", "kind", "units"}


@dataclass(frozen=True)
class SchedulingFingerprint:
    """Predicted logical-step traffic and card-wide SOP, without a T x N SOP copy.

    A profile is supplied explicitly by offline calibration. It is not derived
    from a task's future replay trace during scheduling. Self edges are retained
    to preserve graph identity and excluded from the NoC endpoint load.
    """

    pop_size: np.ndarray
    edge_src: np.ndarray
    edge_dst: np.ndarray
    expected_edge_flits: np.ndarray
    compute_total_sops: np.ndarray
    state_size_mb: float
    source: str
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        populations = _integer_vector(self.pop_size, "pop_size")
        src = _integer_vector(self.edge_src, "edge_src")
        dst = _integer_vector(self.edge_dst, "edge_dst")
        traffic = _nonnegative_array(self.expected_edge_flits, "expected_edge_flits", 2)
        compute = _nonnegative_array(self.compute_total_sops, "compute_total_sops", 1)
        n = len(populations)
        if n == 0 or (populations == 0).any():
            raise ValueError("pop_size must contain at least one positive MicroPopulation")
        if src.shape != dst.shape:
            raise ValueError("edge_src and edge_dst must have matching shapes")
        if (src >= n).any() or (dst >= n).any():
            raise ValueError("edge endpoints must index pop_size")
        if len(set(zip(src.tolist(), dst.tolist()))) != len(src):
            raise ValueError("duplicate src/dst edges: sum them before export")
        if traffic.shape[0] == 0 or traffic.shape[1] != len(src):
            raise ValueError("expected_edge_flits must have shape T x E with T > 0")
        if compute.shape != (traffic.shape[0],):
            raise ValueError("compute_total_sops must have shape T")
        with np.errstate(over="ignore"):
            endpoint_total = traffic[:, src != dst].sum() * 2
        if not np.isfinite(endpoint_total):
            raise ValueError("NoC endpoint total exceeds finite float64 range")
        state = np.asarray(self.state_size_mb)
        if state.ndim != 0 or state.dtype.kind not in "iuf":
            raise ValueError("state_size_mb must be a finite nonnegative number")
        state_mb = float(state)
        if not np.isfinite(state_mb) or state_mb < 0:
            raise ValueError("state_size_mb must be a finite nonnegative number")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be a nonempty provenance string")
        for name, value in zip(ARRAY_FIELDS, (populations, src, dst, traffic, compute)):
            object.__setattr__(self, name, _readonly(value))
        object.__setattr__(self, "state_size_mb", state_mb)
        object.__setattr__(self, "metadata", _metadata_copy(self.metadata))

    @property
    def T(self):
        return self.compute_total_sops.shape[0]

    @property
    def population_count(self):
        return len(self.pop_size)

    @property
    def total_compute_sops(self):
        return float(self.compute_total_sops.sum())

    @property
    def mean_compute_sops(self):
        return self.total_compute_sops / self.T

    @property
    def total_noc_endpoint(self):
        return 2 * float(self.expected_edge_flits[:, self.edge_src != self.edge_dst].sum())

    @property
    def mean_noc_endpoint(self):
        return self.total_noc_endpoint / self.T


def build_fingerprint(samples: Sequence[Workload], source: str, metadata=None):
    """Average separate calibration samples after each sample's flit quantization.

    Sample order and directed edge order are preserved. The static graph, trace
    length, population sizes and memory requirement must match exactly. Only
    the mean total SOP for each logical step is retained.
    """
    try:
        samples = tuple(samples)
    except TypeError as exc:
        raise ValueError("samples must be a nonempty sequence of Workload objects") from exc
    if not samples or any(not isinstance(sample, Workload) for sample in samples):
        raise ValueError("samples must be a nonempty sequence of Workload objects")
    reference = samples[0]
    traffic = np.zeros(reference.flits.shape, dtype=np.float64)
    compute = np.zeros(reference.T, dtype=np.float64)
    for count, sample in enumerate(samples, 1):
        if (sample.T != reference.T or sample.state_size_mb != reference.state_size_mb
                or any(not np.array_equal(getattr(sample, name), getattr(reference, name))
                       for name in ("pop_size", "edge_src", "edge_dst"))):
            raise ValueError("calibration samples must share static graph, T, pop_size and state_size_mb")
        # An incremental mean avoids allocating S x T x E / S x T x N arrays.
        traffic += (sample.flits.astype(np.float64) - traffic) / count
        compute += (sample.compute_sops.sum(axis=1) - compute) / count
    details = _metadata_copy({} if metadata is None else metadata)
    details.update(
        calibration_sample_sources=[sample.source for sample in samples],
        sample_count=len(samples),
        quantization="mean_of_per_sample_cumulative_floor_flits",
    )
    return SchedulingFingerprint(
        reference.pop_size, reference.edge_src, reference.edge_dst, traffic, compute,
        reference.state_size_mb, source, details,
    )


def save_fingerprint(path, profile: SchedulingFingerprint):
    """Write the prediction-only JSON schema, which cannot be loaded as Workload."""
    if not isinstance(profile, SchedulingFingerprint):
        raise ValueError("profile must be a SchedulingFingerprint")
    path = Path(path)
    if path.suffix.lower() != ".json":
        raise ValueError("scheduling fingerprint path must end in .json")
    document = {
        "schema_version": SCHEMA_VERSION, "kind": KIND, "units": dict(UNITS),
        "state_size_mb": profile.state_size_mb, "source": profile.source,
        "metadata": _metadata_copy(profile.metadata),
        **{name: getattr(profile, name).tolist() for name in ARRAY_FIELDS},
    }
    serialized = json.dumps(document, allow_nan=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialized, encoding="utf-8")
    return path


def load_fingerprint(path):
    """Load an explicit offline prediction; replay Workload files are rejected."""
    path = Path(path)
    if path.suffix.lower() != ".json":
        raise ValueError("scheduling fingerprint path must end in .json")
    with path.open(encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError("scheduling fingerprint document must be an object")
    if document.get("kind") != KIND:
        raise ValueError(f"scheduling fingerprint kind must equal {KIND!r}")
    missing = DOCUMENT_FIELDS - document.keys()
    unknown = document.keys() - DOCUMENT_FIELDS
    if missing:
        raise ValueError(f"missing scheduling fingerprint fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"unknown scheduling fingerprint fields: {', '.join(sorted(unknown))}")
    if type(document["schema_version"]) is not int or document["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported scheduling fingerprint schema_version; expected integer 1")
    if document["units"] != UNITS:
        raise ValueError(f"scheduling fingerprint units must equal {UNITS}")
    return SchedulingFingerprint(**{name: document[name] for name in VALUE_FIELDS})

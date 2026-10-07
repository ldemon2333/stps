"""Explicit graph traffic and independent SOP traces for single-card replay."""
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
import json
from pathlib import Path

import numpy as np

SCHEMA_VERSION = 1
UNITS = {"traffic": "flits_per_logical_tick", "compute": "sops_per_logical_tick"}
ARRAY_FIELDS = ("pop_size", "edge_src", "edge_dst", "edge_expected_flits", "compute_sops")
REQUIRED_FIELDS = {*ARRAY_FIELDS, "schema_version", "units", "state_size_mb", "source", "metadata"}
INT64_MAX = np.iinfo(np.int64).max


def _readonly(array):
    return np.frombuffer(array.tobytes(), dtype=array.dtype).reshape(array.shape)


def _integer_vector(value, name):
    raw = np.asarray(value, dtype=object)
    if raw.ndim != 1:
        raise ValueError(f"{name} must be a one-dimensional integer array")
    for item in raw:
        if isinstance(item, (bool, np.bool_)) or not isinstance(item, (int, np.integer)):
            raise ValueError(f"{name} requires integers, excluding booleans")
        if not 0 <= int(item) <= INT64_MAX:
            raise ValueError(f"{name} values must be within nonnegative int64 range")
    return np.asarray(raw, dtype=np.int64)


def _nonnegative_array(value, name, ndim):
    raw = np.asarray(value)
    if raw.ndim != ndim or raw.dtype.kind not in "iuf":
        raise ValueError(f"{name} must be a numeric {ndim}-dimensional array")
    array = np.asarray(raw, dtype=np.float64)
    if not np.isfinite(array).all() or (array < 0).any():
        raise ValueError(f"{name} must contain finite nonnegative values")
    with np.errstate(over="ignore"):
        total = array.sum()
    if not np.isfinite(total):
        raise ValueError(f"{name} total exceeds finite float64 range")
    return array


def _metadata_copy(value):
    if not isinstance(value, dict):
        raise ValueError("metadata must be a JSON object")
    try:
        copied = json.loads(json.dumps(value, allow_nan=False, sort_keys=True))
        if copied != value:
            raise ValueError("metadata must use JSON-compatible keys and values")
        return copied
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must be a finite JSON object") from exc


def _quantize(expected):
    """Cumulative floor per edge using float64's shortest decimal representation."""
    result = np.zeros(expected.shape, dtype=np.int64)
    with localcontext() as context:
        context.prec = 400
        for edge in range(expected.shape[1]):
            carry, emitted = Decimal(0), 0
            for tick in range(expected.shape[0]):
                carry += Decimal(str(float(expected[tick, edge])))
                count = int(carry)
                if count > INT64_MAX - emitted:
                    raise ValueError("quantized cumulative edge traffic exceeds int64")
                result[tick, edge] = count
                emitted += count
                carry -= count
    return _readonly(result)


@dataclass(frozen=True)
class Workload:
    pop_size: np.ndarray
    edge_src: np.ndarray
    edge_dst: np.ndarray
    edge_expected_flits: np.ndarray
    compute_sops: np.ndarray
    state_size_mb: float
    source: str
    metadata: dict = field(default_factory=dict)
    quantized_flits: np.ndarray = field(init=False, repr=False)

    def __post_init__(self):
        populations = _integer_vector(self.pop_size, "pop_size")
        src = _integer_vector(self.edge_src, "edge_src")
        dst = _integer_vector(self.edge_dst, "edge_dst")
        traffic = _nonnegative_array(self.edge_expected_flits, "edge_expected_flits", 2)
        compute = _nonnegative_array(self.compute_sops, "compute_sops", 2)
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
            raise ValueError("edge_expected_flits must have shape T x E with T > 0")
        if compute.shape != (traffic.shape[0], n):
            raise ValueError("compute_sops must have shape T x population_count")
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
        object.__setattr__(self, "quantized_flits", _quantize(traffic))

    @property
    def T(self):
        return self.compute_sops.shape[0]

    @property
    def population_count(self):
        return len(self.pop_size)

    @property
    def flits(self):
        return self.quantized_flits

    @property
    def totals(self):
        return {"expected_flits": float(self.edge_expected_flits.sum()),
                "quantized_flits": sum(int(value) for value in self.flits.flat),
                "compute_sops": float(self.compute_sops.sum())}


def _from_document(document):
    if not isinstance(document, dict):
        raise ValueError("workload document must be an object")
    if any(key in document for key in ("E", "mean_injection_trace", "traffic_sequence")):
        raise ValueError("legacy aggregate fingerprint unsupported: explicit src/dst and SOP required")
    missing = REQUIRED_FIELDS - document.keys()
    if missing:
        raise ValueError(f"missing workload fields: {', '.join(sorted(missing))}")
    unknown = document.keys() - REQUIRED_FIELDS
    if unknown:
        raise ValueError(f"unknown workload fields: {', '.join(sorted(unknown))}")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("unsupported workload schema_version; expected integer 1")
    if document["units"] != UNITS:
        raise ValueError(f"workload units must equal {UNITS}")
    return Workload(**{name: document[name] for name in (*ARRAY_FIELDS, "state_size_mb", "source", "metadata")})


def load_workload(path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        with path.open(encoding="utf-8") as handle:
            return _from_document(json.load(handle))
    if path.suffix.lower() == ".npz":
        with np.load(path, allow_pickle=False) as archive:
            if any(key in archive.files for key in ("E", "mean_injection_trace", "traffic_sequence")):
                raise ValueError("legacy aggregate fingerprint unsupported; src/dst and SOP required")
            if set(archive.files) != {*ARRAY_FIELDS, "manifest"}:
                raise ValueError("NPZ requires manifest and the five workload arrays")
            manifest = archive["manifest"]
            if manifest.ndim != 0 or manifest.dtype.kind != "U":
                raise ValueError("NPZ manifest must be scalar Unicode JSON")
            document = json.loads(str(manifest.item()))
            if not isinstance(document, dict):
                raise ValueError("NPZ manifest must contain a JSON object")
            document.update({name: archive[name] for name in ARRAY_FIELDS})
            return _from_document(document)
    raise ValueError("workload path must end in .json or .npz")


def save_workload(path, workload):
    path = Path(path)
    if path.suffix.lower() not in {".json", ".npz"}:
        raise ValueError("workload path must end in .json or .npz")
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"schema_version": SCHEMA_VERSION, "units": dict(UNITS),
                "state_size_mb": workload.state_size_mb, "source": workload.source,
                "metadata": _metadata_copy(workload.metadata)}
    if path.suffix.lower() == ".json":
        document.update({name: getattr(workload, name).tolist() for name in ARRAY_FIELDS})
        path.write_text(json.dumps(document, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    else:
        manifest = np.array(json.dumps(document, allow_nan=False, sort_keys=True))
        with path.open("wb") as handle:
            np.savez_compressed(handle, manifest=manifest,
                                **{name: getattr(workload, name) for name in ARRAY_FIELDS})
    return path


def from_edge_tensor(W, pop_size, state_size_mb, source, metadata=None):
    """Convert T x N x N x 2 [expected flits, target SOPs], including self work."""
    tensor = _nonnegative_array(W, "W", 4)
    if tensor.shape[-1] != 2 or tensor.shape[1] != tensor.shape[2]:
        raise ValueError("W must have shape T x N x N x 2 with explicit compute")
    populations = _integer_vector(pop_size, "pop_size")
    if tensor.shape[1] != len(populations):
        raise ValueError("W population dimensions must match pop_size")
    src, dst = np.nonzero(np.any(tensor[..., 0] > 0, axis=0))
    details = _metadata_copy({} if metadata is None else metadata)
    details.setdefault("input_format", "TNN2_edge_tensor")
    return Workload(populations, src, dst, tensor[:, src, dst, 0], tensor[..., 1].sum(axis=1),
                    state_size_mb, source, details)


def make_sparse_workload(seed=0, T=8, populations=3, population_size=64,
                         activity_probability=0.12, burst_flits=12):
    """Repeatable test stimulus, not a simulated biological neuron model."""
    for name, value in (("T", T), ("populations", populations),
                        ("population_size", population_size), ("burst_flits", burst_flits)):
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if not np.isfinite(activity_probability) or not 0 <= activity_probability <= 1:
        raise ValueError("activity_probability must be within [0,1]")
    rng = np.random.default_rng(seed)
    src = np.arange(populations, dtype=np.int64)
    dst = src.copy()
    if populations > 1:
        src = np.concatenate((src, src))
        dst = np.concatenate((dst, (dst + 1) % populations))
    activity = rng.random((T, populations)) < activity_probability
    activity[0] = False
    if T > 1:
        activity[1, 0] = True
    if T > 3:
        activity[T // 2, :] = True
        activity[-1, :] = False
    spikes = activity * rng.integers(1, burst_flits + 1, size=activity.shape)
    compute_activity = rng.random((T, populations)) < activity_probability
    compute_activity[0] = False
    if T > 1:
        compute_activity[1, 0] = True
    compute = compute_activity * rng.integers(10, 101, size=compute_activity.shape)
    return Workload(np.full(populations, population_size, dtype=np.int64), src, dst,
                    spikes[:, src].astype(np.float64), compute.astype(np.float64),
                    0.0, "synthetic:sparse-bursts",
                    {"seed": int(seed), "compute_model": "independent synthetic SOP trace"})

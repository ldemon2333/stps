"""Deterministic, finite-buffer, single-card NoC simulation.

All decisions in ``advance_cycle(c)`` read the state at boundary ``c`` and
commit together at boundary ``c + 1``. A link transfer occupies that whole
cycle; its flit is only available to the next router in the following cycle.
Thus no separate link inventory is needed at the sampled integer boundaries.
An uncontended flit offered at boundary zero reaches its destination NI at
boundary ``Manhattan distance + 2`` (source injection, hops, destination Rx).

The network knows neither SNN tick deadlines nor task lifetimes. In particular,
Rx is delivery, while a later consume event frees receive-buffer capacity.
Every queued flit retains its original task identity after delivery.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from numbers import Integral
from typing import Any


PORTS = ("N", "E", "S", "W", "Local")
_OPPOSITE = {"N": "S", "E": "W", "S": "N", "W": "E"}


def _integer(name: str, value: Any, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(value)


@dataclass(frozen=True)
class NoCConfig:
    mesh_x: int
    mesh_y: int
    router_buffer_depth: int = 4
    source_buffer_depth: int = 4
    sink_buffer_depth: int = 4
    sink_service_period: int = 1

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, _integer(name, getattr(self, name), 1))

    @property
    def core_count(self) -> int:
        return self.mesh_x * self.mesh_y


@dataclass(frozen=True)
class _Flit:
    task_id: str
    logical_tick: int
    edge_id: int
    src_core: int
    dst_core: int
    generated_cycle: int


@dataclass
class _Pending:
    flit: _Flit
    count: int


class NoCNetwork:
    """A mesh with XY routing, FIFO inputs and round-robin output arbitration.

    ``core_id = y * mesh_x + x``. Each source, sink and router input is bounded;
    unadmitted requests are compressed FIFO count batches in source_pending.
    Each input and output moves at most one flit per cycle. Downstream capacity
    is conservatively checked before any same-cycle departures or consumption.
    The round-robin order is N, E, S, W, Local and advances only on a transfer.
    """

    def __init__(self, config: NoCConfig):
        if not isinstance(config, NoCConfig):
            raise TypeError("config must be a NoCConfig")
        self.config = config
        self._next_cycle = 0
        self._pending: list[deque[_Pending]] = [deque() for _ in range(config.core_count)]
        self._source: list[deque[_Flit]] = [deque() for _ in range(config.core_count)]
        self._sink: list[deque[_Flit]] = [deque() for _ in range(config.core_count)]
        self._router: list[dict[str, deque[_Flit]]] = [
            {port: deque() for port in PORTS} for _ in range(config.core_count)
        ]
        self._rr = [{port: 0 for port in PORTS} for _ in range(config.core_count)]
        self._enqueue_events: list[dict[str, Any]] = []

    def offer(
        self, task_id: str, logical_tick: int, edge_id: int, src: int, dst: int,
        count: int, cycle: int,
    ) -> None:
        """Offer a count batch at the next cycle boundary. Local edges bypass NoC.

        Existing pending requests retain FIFO priority. Available source NI
        slots are filled immediately, allowing injection during this cycle.
        Their source_enqueue events are returned by advance_cycle, timestamped
        at this offer boundary. A refill after injection uses its ending boundary.
        """
        cycle = _integer("cycle", cycle)
        if cycle != self._next_cycle:
            raise ValueError(f"offer cycle must be the next boundary {self._next_cycle}")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task_id must be a nonempty string")
        logical_tick = _integer("logical_tick", logical_tick)
        edge_id = _integer("edge_id", edge_id)
        src = _integer("src", src)
        dst = _integer("dst", dst)
        count = _integer("count", count)
        if src >= self.config.core_count or dst >= self.config.core_count:
            raise ValueError("source and destination must be cores in this mesh")
        if src == dst:
            raise ValueError("local/self edges must bypass the NoC")
        if count == 0:
            return
        flit = _Flit(task_id, logical_tick, edge_id, src, dst, cycle)
        pending = self._pending[src]
        if pending and pending[-1].flit == flit:
            pending[-1].count += count
        else:
            pending.append(_Pending(flit, count))
        self._fill_source(src, cycle, self._enqueue_events)

    def _fill_source(self, core: int, boundary: int, events: list[dict[str, Any]]) -> None:
        queue, pending = self._source[core], self._pending[core]
        free = self.config.source_buffer_depth - len(queue)
        while pending and free:
            batch = pending[0]
            admitted = min(free, batch.count)
            queue.extend([batch.flit] * admitted)
            events.append(self._event(
                "source_enqueue", boundary, batch.flit, count=admitted,
                router=core, port="Local",
            ))
            batch.count -= admitted
            free -= admitted
            if batch.count == 0:
                pending.popleft()

    @staticmethod
    def _event(kind: str, cycle: int, flit: _Flit, count: int = 1, **extra: Any) -> dict[str, Any]:
        return {
            "kind": kind, "cycle": cycle, "task_id": flit.task_id,
            "logical_tick": flit.logical_tick, "edge_id": flit.edge_id,
            "src_core": flit.src_core, "dst_core": flit.dst_core,
            "count": count, "generated_cycle": flit.generated_cycle, **extra,
        }

    def _route(self, router: int, destination: int) -> str:
        x, y = router % self.config.mesh_x, router // self.config.mesh_x
        dx, dy = destination % self.config.mesh_x, destination // self.config.mesh_x
        if x != dx:
            return "E" if x < dx else "W"
        if y != dy:
            return "S" if y < dy else "N"
        return "Local"

    def _neighbor(self, router: int, port: str) -> int:
        return router + {"N": -self.config.mesh_x, "E": 1,
                         "S": self.config.mesh_x, "W": -1}[port]

    def advance_cycle(self, cycle: int) -> list[dict[str, Any]]:
        """Commit one synchronous cycle and return its transition events.

        Transfer events carry ending-boundary times c+1. Deferred offer-time
        source_enqueue events retain boundary c. tx.wait_cycles is source waiting
        time from generation to injection; rx.wait_cycles is end-to-end delay.
        Sink service occurs at boundaries P, 2P, ... for service period P, and
        only consumes flits that were already present at the start of the cycle.
        rx_blocked is counted once per destination per cycle, attributed to the
        head request selected by that output's round-robin pointer.
        router_wait counts each old input head that cannot move once per cycle;
        downstream_full takes precedence when the shared output has no capacity,
        otherwise arbitration identifies a head that lost the output grant.
        """
        cycle = _integer("cycle", cycle)
        if cycle != self._next_cycle:
            raise ValueError(f"expected cycle {self._next_cycle}, got {cycle}")
        boundary = cycle + 1
        events = self._enqueue_events
        self._enqueue_events = []

        # Decide from the old state. Every router input head asks one output,
        # so independently arbitrating outputs cannot reuse an input.
        consumes = [
            core for core, queue in enumerate(self._sink)
            if queue and boundary % self.config.sink_service_period == 0
        ]
        moves: list[tuple[int, str, str, int, _Flit]] = []
        for router, inputs in enumerate(self._router):
            requests: dict[str, list[int]] = {port: [] for port in PORTS}
            for index, input_port in enumerate(PORTS):
                if inputs[input_port]:
                    output = self._route(router, inputs[input_port][0].dst_core)
                    requests[output].append(index)
            for output in PORTS:
                if not requests[output]:
                    continue
                start = self._rr[router][output]
                winner = min(requests[output], key=lambda i: (i - start) % len(PORTS))
                input_port = PORTS[winner]
                flit = inputs[input_port][0]
                if output == "Local":
                    neighbor = router
                    downstream_full = len(self._sink[router]) >= self.config.sink_buffer_depth
                    if downstream_full:
                        events.append(self._event(
                            "rx_blocked", boundary, flit, router=router, port=output,
                        ))
                else:
                    neighbor = self._neighbor(router, output)
                    downstream = self._router[neighbor][_OPPOSITE[output]]
                    downstream_full = len(downstream) >= self.config.router_buffer_depth
                for index in requests[output]:
                    if index == winner and not downstream_full:
                        continue
                    waiting_port = PORTS[index]
                    events.append(self._event(
                        "router_wait", boundary, inputs[waiting_port][0],
                        router=router, input_port=waiting_port, port=waiting_port,
                        requested_output=output,
                        reason="downstream_full" if downstream_full else "arbitration",
                    ))
                if not downstream_full:
                    moves.append((router, input_port, output, neighbor, flit))

        injections: list[tuple[int, _Flit]] = []
        for core, queue in enumerate(self._source):
            if not queue:
                continue
            flit = queue[0]
            if len(self._router[core]["Local"]) < self.config.router_buffer_depth:
                injections.append((core, flit))
            else:
                events.append(self._event(
                    "tx_stall", boundary, flit, router=core, port="Local",
                ))

        # Remove all old-state departures before adding arrivals. Arrivals can
        # never be selected by any decision in this cycle.
        for core in consumes:
            flit = self._sink[core].popleft()
            events.append(self._event("consume", boundary, flit, router=core, port="Local"))
        for router, input_port, output, neighbor, flit in moves:
            self._router[router][input_port].popleft()
            self._rr[router][output] = (PORTS.index(input_port) + 1) % len(PORTS)
        for core, flit in injections:
            self._source[core].popleft()

        for router, input_port, output, neighbor, flit in moves:
            if output == "Local":
                self._sink[router].append(flit)
                events.append(self._event(
                    "rx", boundary, flit, router=router, port=output,
                    wait_cycles=boundary - flit.generated_cycle,
                ))
            else:
                self._router[neighbor][_OPPOSITE[output]].append(flit)
                events.append(self._event(
                    "link", boundary, flit, router=router, port=output, next_router=neighbor,
                ))
        for core, flit in injections:
            self._router[core]["Local"].append(flit)
            events.append(self._event(
                "tx", boundary, flit, router=core, port="Local",
                wait_cycles=boundary - flit.generated_cycle,
            ))
        for core in range(self.config.core_count):
            self._fill_source(core, boundary, events)
        self._next_cycle = boundary
        return events

    def outstanding(self) -> list[dict[str, Any]]:
        """Return grouped inventory, including delivered but unconsumed flits.

        Grouping only affects this diagnostic view; router and NI queue order
        remains exact. Source pending counts are never materialized as flits.
        """
        rows: list[dict[str, Any]] = []

        def append_groups(kind: str, core: int, port: str, pairs: Any) -> None:
            groups: dict[_Flit, int] = {}
            for flit, count in pairs:
                groups[flit] = groups.get(flit, 0) + count
            for flit, count in groups.items():
                row = self._event(kind, self._next_cycle, flit, count, router=core, port=port)
                rows.append(row)

        for core in range(self.config.core_count):
            append_groups("source_pending", core, "Local",
                          ((batch.flit, batch.count) for batch in self._pending[core]))
            append_groups("source_ni", core, "Local", ((flit, 1) for flit in self._source[core]))
            for port in PORTS:
                append_groups("router", core, port, ((flit, 1) for flit in self._router[core][port]))
            append_groups("sink_ni", core, "Local", ((flit, 1) for flit in self._sink[core]))
        return rows

    def pending_delivery(self) -> int:
        """Count flits that have not reached a destination NI (Rx)."""
        return sum(
            sum(batch.count for batch in self._pending[core]) + len(self._source[core])
            + sum(len(queue) for queue in self._router[core].values())
            for core in range(self.config.core_count)
        )

    def occupancy(self) -> list[dict[str, Any]]:
        """Report all queues at the current boundary, including empty queues."""
        rows = []
        for core in range(self.config.core_count):
            for kind, count, capacity in (
                ("source_pending", sum(batch.count for batch in self._pending[core]), None),
                ("source_ni", len(self._source[core]), self.config.source_buffer_depth),
                ("sink_ni", len(self._sink[core]), self.config.sink_buffer_depth),
            ):
                rows.append({"kind": kind, "router": core, "port": "Local",
                             "occupancy": count, "capacity": capacity})
            for port in PORTS:
                rows.append({"kind": "router", "router": core, "port": port,
                             "occupancy": len(self._router[core][port]),
                             "capacity": self.config.router_buffer_depth})
        return rows

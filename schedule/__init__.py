"""Schedule package initialization - registers all schedulers."""
from .base import (
    BaseScheduler,
    MigrationEvent,
    SchedulerMetrics,
    get_scheduler,
    list_schedulers,
    register_scheduler,
)
from .placement_strategy import (
    PlacementStrategy,
    BestFitStrategy,
    P2CStrategy,
    RoundRobinStrategy,
    DRFStrategy,
)
from .bestfit import BestFitScheduler
from .roundrobin import RoundRobin

# Import to trigger registration
from . import bestfit     # noqa: F401
from . import glass       # noqa: F401
from . import gandiva     # noqa: F401
from . import drf         # noqa: F401
from . import p2c         # noqa: F401
from . import roundrobin  # noqa: F401
from . import glass_drl   # noqa: F401



__all__ = [
    # Base classes
    "BaseScheduler",
    "PlacementStrategy",
    # Placement strategies
    "BestFitStrategy",
    "P2CStrategy",
    "RoundRobinStrategy",
    "DRFStrategy",
    # Schedulers
    "BestFitScheduler",
    "RoundRobin",
    # Metrics
    "MigrationEvent",
    "SchedulerMetrics",
    # Registry functions
    "get_scheduler",
    "list_schedulers",
    "register_scheduler",
]

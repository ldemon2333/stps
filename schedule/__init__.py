"""Schedule package initialization - registers all schedulers."""
from .base import (
    BaseScheduler,
    get_scheduler,
    list_schedulers,
    register_scheduler,
)
from .bestfit import BestFitScheduler
from .roundrobin import RoundRobin

# Import to trigger registration
from . import bestfit     # noqa: F401
from . import drf         # noqa: F401
from . import p2c         # noqa: F401
from . import phase_wrappers  # noqa: F401
from . import roundrobin  # noqa: F401
from . import stps        # noqa: F401


__all__ = [
    "BaseScheduler",
    "BestFitScheduler",
    "RoundRobin",
    "get_scheduler",
    "list_schedulers",
    "register_scheduler",
]

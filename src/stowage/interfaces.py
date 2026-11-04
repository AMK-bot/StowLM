"""Import every module that declares a frozen interface, so ``stowage.frozen.FROZEN`` is complete.

    import stowage.interfaces  # noqa: F401
    from stowage.frozen import FROZEN
"""

import stowage.actions  # noqa: F401
import stowage.bounds  # noqa: F401
import stowage.checker  # noqa: F401
import stowage.feasibility  # noqa: F401
import stowage.io  # noqa: F401
import stowage.linear  # noqa: F401
import stowage.moves  # noqa: F401
import stowage.planner  # noqa: F401
import stowage.solver.ballast  # noqa: F401
import stowage.solver.repair  # noqa: F401

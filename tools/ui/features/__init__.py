"""ui.features — importing this package registers every workspace.

To add a feature: create a module here and import it below (the module calls
`feature.register(...)` at import). To remove one: delete the module and its
import line. The shell never references a feature directly.
"""
from . import bench       # noqa: F401
from . import library     # noqa: F401
from . import projects    # noqa: F401
from . import git         # noqa: F401
from . import settings    # noqa: F401

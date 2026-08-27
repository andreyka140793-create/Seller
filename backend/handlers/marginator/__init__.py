from handlers.marginator.router import marginator_router

# Import all submodules so handlers register on the router
from handlers.marginator import commands
from handlers.marginator import upload
from handlers.marginator import calculation
from handlers.marginator import params
from handlers.marginator import presets
from handlers.marginator import whatif
from handlers.marginator import compare
from handlers.marginator import history
from handlers.marginator import fx

__all__ = ["marginator_router"]

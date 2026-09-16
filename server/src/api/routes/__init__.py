"""Route router package — import and re-export all routers for registration in app.py."""

from __future__ import annotations

from .tasks import router as tasks_router
from .skills import router as skills_router
from .memory import router as memory_router
from .tools import router as tools_router
from .models import router as models_router
from .traces import router as traces_router
from .chat import router as chat_router
from .health import router as health_router

all_routers = [
    chat_router,
    tasks_router,
    skills_router,
    memory_router,
    tools_router,
    models_router,
    traces_router,
    health_router,
]
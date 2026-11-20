from .admin_analytics import router as admin_analytics_router

from .auth import router as auth_router
from .config import router as config_router
from .health import router as health_router
from .device import router as device_router
from .gallery import router as gallery_router
from .locations import router as locations_router
from .mobile import router as mobile_router
from .modes import router as modes_router
from .pages import router as pages_router
from .render import router as render_router
from .stats import router as stats_router
from .uploads import router as uploads_router
from .user import router as user_router
from .voice import router as voice_router

api_routers = [
    render_router,
    admin_analytics_router,
    config_router,
    health_router,
    device_router,
    gallery_router,
    modes_router,
    auth_router,
    user_router,
    mobile_router,
    stats_router,
    locations_router,
    uploads_router,
    voice_router,
]

page_routers = [pages_router]

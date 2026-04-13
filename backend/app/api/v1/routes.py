from fastapi import APIRouter

from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.video import router as video_router
from app.api.v1.endpoints.chat import router as chat_router
from app.api.v1.endpoints.feedback import router as feedback_router
from app.api.v1.endpoints.external_api import router as external_api_router
from app.api.v1.endpoints.lcj_linking import router as lcj_linking_router
from app.api.v1.endpoints.admin import router as admin_router
from app.api.v1.endpoints.live import router as live_router
from app.api.v1.endpoints.live_extension import router as live_extension_router
from app.api.v1.endpoints.live_ai import router as live_ai_router
from app.api.v1.endpoints.extension_events_api import router as ext_events_router
from app.api.v1.endpoints.report import router as report_router
from app.api.v1.endpoints.upload_core import router as upload_core_router
from app.api.v1.endpoints.feature_flags import router as feature_flags_router
from app.api.v1.endpoints.clip_feedback import router as clip_feedback_router
from app.api.v1.endpoints.feedback_loop import router as feedback_loop_router
from app.api.v1.endpoints.live_analysis import router as live_analysis_router
from app.api.v1.endpoints.clip_editor_v2 import router as clip_editor_v2_router
from app.api.v1.endpoints.dev_safety import router as dev_safety_router
from app.api.v1.endpoints.digital_human import router as digital_human_router
from app.api.v1.endpoints.face_swap_video import router as face_swap_video_router
from app.api.v1.endpoints.auto_video import router as auto_video_router
from app.api.v1.endpoints.persona import router as persona_router
from app.api.v1.endpoints.script_generator import router as script_generator_router
from app.api.v1.endpoints.clip_db import router as clip_db_router
from app.api.v1.endpoints.widget import router as widget_router

routers = APIRouter()
routers.include_router(auth_router, prefix="/auth", tags=["Auth"])
routers.include_router(upload_core_router)  # Upload Core: must be registered before video_router
routers.include_router(video_router)
routers.include_router(chat_router)
routers.include_router(feedback_router)
routers.include_router(external_api_router)
routers.include_router(lcj_linking_router)
routers.include_router(admin_router)
routers.include_router(live_router)
routers.include_router(live_extension_router)
routers.include_router(live_ai_router)
routers.include_router(ext_events_router)
routers.include_router(report_router)
routers.include_router(feature_flags_router)
routers.include_router(clip_feedback_router, prefix="/clips", tags=["Clip Feedback"])
routers.include_router(feedback_loop_router, prefix="/feedback", tags=["Feedback Loop"])
routers.include_router(live_analysis_router)
routers.include_router(clip_editor_v2_router, prefix="/editor", tags=["Clip Editor v2"])
routers.include_router(dev_safety_router)
routers.include_router(digital_human_router)
routers.include_router(face_swap_video_router)
routers.include_router(auto_video_router)
routers.include_router(persona_router)
routers.include_router(script_generator_router)
routers.include_router(clip_db_router, prefix="/clip-db", tags=["Clip DB"])
routers.include_router(widget_router, tags=["Widget"])

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
from app.api.v1.endpoints.brand_portal import router as brand_portal_router
from app.api.v1.endpoints.brand_sync import router as brand_sync_router
from app.api.v1.endpoints.shopee_live import router as shopee_live_router
from app.api.v1.endpoints.auto_live import router as auto_live_router
from app.api.v1.endpoints.subtitle_dictionary import router as subtitle_dict_router
from app.api.v1.endpoints.reviewer_auth import router as reviewer_auth_router
from app.api.v1.endpoints.ml_training import router as ml_training_router
from app.api.v1.endpoints.video_performance import router as video_performance_router
from app.api.v1.endpoints.tiktok_tracking import router as tiktok_tracking_router
from app.api.v1.endpoints.ai_clip_generator import router as ai_clip_generator_router
from app.api.v1.endpoints.liver_clone import router as liver_clone_router
from app.api.v1.endpoints.magic_cut import router as magic_cut_router
from app.api.v1.endpoints.editing_style import router as editing_style_router
from app.api.v1.endpoints.user_profile import router as user_profile_router
from app.api.v1.endpoints.ai_video_generator import router as ai_video_generator_router
from app.api.v1.endpoints.liver_guide import router as liver_guide_router

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
routers.include_router(brand_portal_router, tags=["Brand Portal"])
routers.include_router(brand_sync_router, tags=["Brand Sync"])
routers.include_router(shopee_live_router, tags=["Shopee Live"])
routers.include_router(auto_live_router, tags=["Auto Live"])
routers.include_router(subtitle_dict_router, tags=["Subtitle Dictionary"])
routers.include_router(reviewer_auth_router, tags=["Reviewer Auth"])
routers.include_router(ml_training_router, tags=["ML Training"])
routers.include_router(video_performance_router, tags=["Video Performance"])
routers.include_router(tiktok_tracking_router, tags=["TikTok Tracking"])
routers.include_router(ai_clip_generator_router, tags=["AI Clip Generator"])
routers.include_router(liver_clone_router, tags=["Liver Clone"])
routers.include_router(magic_cut_router, tags=["Magic Cut"])
routers.include_router(editing_style_router, tags=["Editing Style Learning"])
routers.include_router(user_profile_router, tags=["User Profile"])
routers.include_router(ai_video_generator_router, tags=["AI Video Generator"])
routers.include_router(liver_guide_router, tags=["Liver Guide"])

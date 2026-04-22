export const URL_CONSTANTS = {
  REGISTER: "/api/v1/auth/register",
  LOGIN: "/api/v1/auth/login",
  ME: "/api/v1/auth/me",
  CHANGE_PASSWORD: "/api/v1/auth/change-password",
  REFRESH_TOKEN: "/api/v1/auth/refresh",
  FORGOT_PASSWORD: "/api/v1/auth/forgot",
  RESET_PASSWORD: "/api/v1/auth/reset",
  GENERATE_UPLOAD_URL: "/api/v1/videos/generate-upload-url",
  GENERATE_EXCEL_UPLOAD_URL: "/api/v1/videos/generate-excel-upload-url",
  UPLOAD_COMPLETE: "/api/v1/videos/upload-complete",
  BATCH_UPLOAD_COMPLETE: "/api/v1/videos/batch-upload-complete",
  UPLOAD_RESUME_CHECK: "/api/v1/videos/uploads/check",
  UPLOADS_CLEAR: "/api/v1/videos/uploads/clear",
  UPLOAD_PROXY_BLOCK: "/api/v1/videos/upload-proxy/block",
  UPLOAD_PROXY_COMMIT: "/api/v1/videos/upload-proxy/commit",
  GET_USER_VIDEOS: "/api/v1/videos/user",
  GET_VIDEO: "/api/v1/videos",
  FEEDBACK_SUBMIT: "/api/v1/feedback",
  LCJ_LINK_STATUS: "/api/v1/lcj/link-status",
  LCJ_VERIFY_LIVER: "/api/v1/lcj/verify-liver",
  LCJ_LINK: "/api/v1/lcj/link",
  LCJ_UNLINK: "/api/v1/lcj/unlink",
  LIVE_CHECK: "/api/v1/videos/live-check",
  LIVE_CAPTURE: "/api/v1/videos/live-capture",
  // Real-time live monitoring
  LIVE_STREAM_EVENTS: "/api/v1/live",  // /{video_id}/stream
  LIVE_STATUS: "/api/v1/live",          // /{video_id}/status
  LIVE_START_MONITOR: "/api/v1/live",   // /{video_id}/start-monitor
  LIVE_PUSH_EVENTS: "/api/v1/live",     // /{video_id}/events
  LIVE_ACTIVE: "/api/v1/live/active",
  LIVE_EXTENSION_SESSIONS: "/api/v1/live/extension/sessions",
  LIVE_AI_CHAT: "/api/v1/live/ai/chat",
  LIVE_AI_ANALYZE: "/api/v1/live/ai/analyze",
  // Live Report v1
  REPORT_GENERATE: "/api/v1/reports",  // /{video_id}/generate
  REPORT_GET: "/api/v1/reports",        // /{video_id}
};

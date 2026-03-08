from contextlib import AbstractContextManager
from typing import Callable, Optional
import uuid as _uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.models.orm.video import Video
from app.repository.base_repository import BaseRepository


class VideoRepository(BaseRepository):
    def __init__(self, session_factory: Callable[..., AsyncSession]):
        self.session_factory = session_factory
        super().__init__(session_factory, Video)

    async def create_video(
        self,
        user_id: int,
        video_id: str,
        original_filename: str,
        status: str = "uploaded",
        upload_type: str = "screen_recording",
        excel_product_blob_url: Optional[str] = None,
        excel_trend_blob_url: Optional[str] = None,
        time_offset_seconds: float = 0,
    ) -> Video:
        """Create a new video record"""
        session = self.session_factory()
        try:
            video = Video(
                id=_uuid.UUID(video_id),
                user_id=user_id,
                original_filename=original_filename,
                status=status,
                upload_type=upload_type,
                excel_product_blob_url=excel_product_blob_url,
                excel_trend_blob_url=excel_trend_blob_url,
                time_offset_seconds=time_offset_seconds,
            )
            session.add(video)
            await session.commit()
            await session.refresh(video)
            return video
        finally:
            await session.close()

    async def get_video_by_id(self, video_id: str) -> Video | None:
        """Get video by ID"""
        session = self.session_factory()
        try:
            result = await session.execute(
                select(Video).filter(Video.id == _uuid.UUID(video_id))
            )
            return result.scalar_one_or_none()
        finally:
            await session.close()

    async def get_videos_by_user(self, user_id: int) -> list[Video]:
        """Get all videos for a user (includes videos with NULL user_id for backward compatibility)"""
        session = self.session_factory()
        try:
            from sqlalchemy import or_
            result = await session.execute(
                select(Video).filter(
                    or_(Video.user_id == user_id, Video.user_id.is_(None))
                )
                .order_by(desc(Video.created_at))
            )
            return result.scalars().all()
        finally:
            await session.close()

    async def delete_video(self, video_id: str, user_id: int) -> bool:
        """Delete a video record by ID (only if owned by user)"""
        session = self.session_factory()
        try:
            result = await session.execute(
                select(Video).filter(
                    Video.id == _uuid.UUID(video_id),
                    Video.user_id == user_id,
                )
            )
            video = result.scalar_one_or_none()
            if not video:
                return False
            await session.delete(video)
            await session.commit()
            return True
        finally:
            await session.close()

    async def rename_video(self, video_id: str, user_id: int, new_name: str) -> Video | None:
        """Rename a video (only if owned by user)"""
        session = self.session_factory()
        try:
            result = await session.execute(
                select(Video).filter(
                    Video.id == _uuid.UUID(video_id),
                    Video.user_id == user_id,
                )
            )
            video = result.scalar_one_or_none()
            if not video:
                return None
            video.original_filename = new_name
            await session.commit()
            await session.refresh(video)
            return video
        finally:
            await session.close()

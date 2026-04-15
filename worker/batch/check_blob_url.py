"""Check blob URL format and find local Excel files."""
import asyncio
import os
import sys
import glob

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def check():
    # Check blob URL
    async with AsyncSessionLocal() as session:
        r = await session.execute(text(
            "SELECT id, excel_trend_blob_url FROM videos "
            "WHERE status IN ('completed', 'DONE') "
            "AND excel_trend_blob_url IS NOT NULL "
            "LIMIT 3"
        ))
        for row in r.fetchall():
            vid = str(row[0])
            url = row[1]
            print(f"Video: {vid[:8]}")
            print(f"  URL length: {len(url)}")
            print(f"  URL start: {url[:150]}")
            print(f"  URL end: ...{url[-80:]}")
            print()

    # Check local files
    print("=== Local Excel files ===")
    patterns = [
        "/opt/aitherhub/data/**/*.xlsx",
        "/tmp/**/*.xlsx",
        "/opt/aitherhub/**/*.xlsx",
    ]
    for pat in patterns:
        files = glob.glob(pat, recursive=True)
        if files:
            print(f"  {pat}: {len(files)} files")
            for f in files[:3]:
                print(f"    {f}")

    # Check video dirs
    print("\n=== Video processing dirs ===")
    base = "/opt/aitherhub/data"
    if os.path.exists(base):
        dirs = os.listdir(base)[:5]
        for d in dirs:
            full = os.path.join(base, d)
            if os.path.isdir(full):
                contents = os.listdir(full)
                xlsx = [c for c in contents if c.endswith(".xlsx")]
                print(f"  {d[:8]}: {len(contents)} files, xlsx={xlsx}")


asyncio.run(check())

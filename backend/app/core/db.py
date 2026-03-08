from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from app.models.orm.base import Base
import os
import ssl
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

DATABASE_URL = os.getenv("DATABASE_URL")

def prepare_database_url(url: str) -> tuple[str, dict]:
    """
    Prepare database URL for asyncpg compatibility.
    asyncpg doesn't support 'sslmode' parameter, need to convert to 'ssl' context.
    """
    if not url:
        return url, {}
    
    # Parse the URL
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    
    connect_args = {}
    
    # Handle sslmode parameter for asyncpg
    if 'sslmode' in query_params:
        sslmode = query_params['sslmode'][0]
        # Remove sslmode from query params
        del query_params['sslmode']
        
        # Convert sslmode to ssl context
        if sslmode == 'require':
            # Create SSL context that requires SSL but doesn't verify certificates
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            connect_args['ssl'] = ssl_context
        elif sslmode == 'verify-ca' or sslmode == 'verify-full':
            # Create SSL context with certificate verification
            connect_args['ssl'] = ssl.create_default_context()
        elif sslmode == 'disable':
            # Disable SSL
            connect_args['ssl'] = False
    
    # Rebuild query string without sslmode
    new_query = urlencode(query_params, doseq=True)
    
    # Rebuild the cleaned URL
    cleaned_url = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment
    ))
    
    return cleaned_url, connect_args

cleaned_url, connect_args = prepare_database_url(DATABASE_URL)

engine = create_async_engine(
    cleaned_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args=connect_args,
)

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

# Alias for backward compatibility (used by health/ready endpoint)
get_async_session = get_db

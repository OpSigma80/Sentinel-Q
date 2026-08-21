import httpx
from loguru import logger

class HTTPClient:
    """Low-level implementation for outbound HTTP requests."""
    
    async def check_status(self, url: str) -> int:
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(url)
                return response.status_code
            except Exception as e:
                logger.error(f"Error connecting to {url}: {str(e)}")
                return 0 # 0 means the service is unreachable.
import httpx
from loguru import logger

class HTTPClient:
    """Implementación de bajo nivel para realizar peticiones."""
    
    async def check_status(self, url: str) -> int:
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(url)
                return response.status_code
            except Exception as e:
                logger.error(f"Error conectando a {url}: {str(e)}")
                return 0 # 0 indica que el servicio es inalcanzable
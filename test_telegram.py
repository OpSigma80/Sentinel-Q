import asyncio
import httpx
import pytest
from sentinel.config import settings

@pytest.mark.asyncio
async def test():
    token = settings.telegram_token
    chat_id = settings.telegram_chat_id
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    print(f"--- Probando con ChatID: {chat_id} ---")
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(url, json={
                "chat_id": chat_id,
                "text": "🚀 Sentinel-Q: Prueba de conexión directa exitosa."
            })
            print(f"Status Code: {r.status_code}")
            print(f"Respuesta: {r.text}")
        except Exception as e:
            print(f"Error de red: {e}")

if __name__ == "__main__":
    asyncio.run(test())
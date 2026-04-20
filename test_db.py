import psycopg2
from dotenv import load_dotenv
import os

load_dotenv()

try:
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("POSTGRES_DB"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD")
    )
    print("✅ ¡CONEXIÓN EXITOSA A LA DATA-FORGE!")
    conn.close()
except Exception as e:
    print(f"❌ Error: {e}")

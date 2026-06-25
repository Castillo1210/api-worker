# scripts/init_db.py
import asyncpg
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()


async def create_unique_index():
    """Crea unique index en numero_operacion"""
    conn = await asyncpg.connect(
        host=os.getenv("DATABASE_HOST", "localhost"),
        port=int(os.getenv("DATABASE_PORT", "5432")),
        database=os.getenv("DATABASE_NAME", "confirmo"),
        user=os.getenv("DATABASE_USER", "confirmo_app"),
        password=os.getenv("DATABASE_PASSWORD"),
    )
    
    try:
        # Verificar si ya existe
        exists = await conn.fetchval("""
            SELECT 1 FROM pg_indexes 
            WHERE indexname = 'uk_depositos_numero_operacion'
        """)
        
        if exists:
            print("Índice único ya existe")
            return
        
        # Crear índice único
        await conn.execute("""
            CREATE UNIQUE INDEX uk_depositos_numero_operacion 
            ON depositos (numero_operacion) 
            WHERE numero_operacion IS NOT NULL
        """)
        print("Índice único creado exitosamente")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(create_unique_index())
# config.py
import os
from dotenv import load_dotenv

# Cargar variables de entorno desde .env (opcional para pruebas locales)
load_dotenv()

# --- Credenciales de Telegram ---
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# --- Credenciales de Hydrax ---
HYDRAX_API_KEY = os.getenv("HYDRAX_API_KEY")

# --- Configuración para OAuth de Google Drive ---
TOKEN_JSON_DATA = os.getenv("TOKEN_JSON_DATA")
TOKEN_JSON_PATH = os.getenv("TOKEN_JSON_PATH", "token.json") # Valor por defecto

# --- Lista Blanca de Usuarios ---
# Leer la variable de entorno. Formato: "ID1,ID2,ID3"
WHITELISTED_USERS_STR = os.getenv("WHITELISTED_USERS", "") # Cadena vacía por defecto
# Convertir a un conjunto de enteros para una búsqueda rápida
WHITELISTED_USERS = set()
if WHITELISTED_USERS_STR:
    try:
        # Dividir por comas, quitar espacios y convertir a int
        WHITELISTED_USERS = {int(uid.strip()) for uid in WHITELISTED_USERS_STR.split(',') if uid.strip().isdigit()}
        print(f"Lista blanca cargada: {WHITELISTED_USERS}")
    except Exception as e:
        print(f"Error al parsear WHITELISTED_USERS: {e}. La lista blanca estará vacía.")
        WHITELISTED_USERS = set()
else:
    print("No se configuró WHITELISTED_USERS. Todos los usuarios pueden acceder (modo abierto).")

# --- Validaciones iniciales ---
# Nota: La validación de usuarios se hace en tiempo de ejecución, no aquí.
if not all([API_ID, API_HASH, BOT_TOKEN, HYDRAX_API_KEY]):
    raise ValueError("Faltan variables de entorno esenciales (API_ID, API_HASH, BOT_TOKEN, HYDRAX_API_KEY).")

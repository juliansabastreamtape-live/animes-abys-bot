import requests
import time

# Importa la clave desde config
from config import HYDRAX_API_KEY

def import_to_hydrax(drive_id: str):
    """Importa un archivo de Google Drive a Hydrax usando su API."""
    url = f"https://api.hydrax.net/{HYDRAX_API_KEY}/drive/{drive_id}"

    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=30) # Timeout de 30 segundos
            response.raise_for_status() # Lanza excepción para códigos 4xx/5xx
            data = response.json()

            if data.get("status") == True:
                return {"success": True, "slug": data.get("slug"), "status_video": data.get("status_video")}
            else:
                return {"success": False, "error": data.get("msg", "Error desconocido de Hydrax")}

        except requests.exceptions.RequestException as e:
            print(f"Intento {attempt+1} fallido al llamar a Hydrax: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt) # Espera exponencial
            else:
                return {"success": False, "error": f"Error de red al contactar Hydrax: {e}"}
        except Exception as e:
             print(f"Error inesperado al llamar a Hydrax: {e}")
             return {"success": False, "error": f"Error interno al procesar respuesta de Hydrax: {e}"}

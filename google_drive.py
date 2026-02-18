# google_drive.py (Versión completa con todas las funciones y correcciones)
import asyncio
import os
import json
import time
import tempfile
import math
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build # Eliminado BuildError
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError # Importar HttpError

import config # Importa el módulo config para acceder a las variables
# Importar funciones de db.py para manejar archivos subidos
from db import get_uploaded_files, remove_uploaded_file_record, clear_all_uploaded_file_records

# Variable global para almacenar las credenciales cargadas
_credentials = None

def load_credentials():
    """Carga las credenciales OAuth."""
    global _credentials
    creds = None

    if config.TOKEN_JSON_DATA:
        print("Cargando token OAuth desde la variable de entorno TOKEN_JSON_DATA.")
        try:
            token_data = json.loads(config.TOKEN_JSON_DATA)
            creds = Credentials.from_authorized_user_info(token_data, scopes=['https://www.googleapis.com/auth/drive'])
        except json.JSONDecodeError as e:
            print(f"Error al decodificar JSON desde TOKEN_JSON_DATA: {e}")
            raise ValueError("El contenido de TOKEN_JSON_DATA no es un JSON válido.") from e
        except Exception as e:
            print(f"Error al crear credenciales desde TOKEN_JSON_DATA: {e}")
            raise
    elif os.path.exists(config.TOKEN_JSON_PATH):
        print(f"Cargando token OAuth desde el archivo: {config.TOKEN_JSON_PATH}")
        try:
            creds = Credentials.from_authorized_user_file(config.TOKEN_JSON_PATH, scopes=['https://www.googleapis.com/auth/drive'])
        except Exception as e:
             print(f"Error al cargar credenciales desde el archivo {config.TOKEN_JSON_PATH}: {e}")
             raise
    else:
        raise FileNotFoundError(
            "No se encontró el token de autenticación de Google Drive. "
            "Debes proporcionar el contenido del archivo 'token.json' en la variable de entorno TOKEN_JSON_DATA "
            "o asegurarte de que el archivo 'token.json' exista en la ruta especificada por TOKEN_JSON_PATH."
        )

    if creds and creds.expired and creds.refresh_token:
        print("Token expirado, intentando refrescar...")
        try:
            creds.refresh(Request())
            print("Token refrescado exitosamente.")
            # Guardar el token refrescado si se usa un archivo físico (no recomendado con Render + var env)
            # if not config.TOKEN_JSON_DATA and os.path.exists(config.TOKEN_JSON_PATH):
            #      try:
            #          with open(config.TOKEN_JSON_PATH, 'w') as token_file:
            #              token_file.write(creds.to_json())
            #          print("Token refrescado guardado en el archivo.")
            #      except Exception as e:
            #          print(f"Advertencia: No se pudo guardar el token refrescado en el archivo: {e}")
        except Exception as e:
             print(f"Error al refrescar el token: {e}")
             raise

    _credentials = creds
    return creds

def get_drive_service():
    """Obtiene el servicio de la API de Google Drive e imprime la cuenta usada."""
    global _credentials
    if _credentials is None or not _credentials.valid:
        _credentials = load_credentials()
    # Imprimir información del usuario autenticado (opcional, para diagnóstico)
    try:
        service = build('drive', 'v3', credentials=_credentials)
        about = service.about().get(fields="user").execute()
        user_email = about.get('user', {}).get('emailAddress', 'Desconocido')
        print(f"Usando cuenta de Google Drive: {user_email}")
        return service
    except Exception as e:
        print(f"Error al obtener información del usuario de Drive o crear el servicio: {e}")
        # Si falla obtener 'about', devolver el servicio de todos modos
        # Es posible que 'about' no esté disponible con todas las credenciales o scopes
        # En ese caso, solo construimos el servicio.
        try:
             return build('drive', 'v3', credentials=_credentials)
        except Exception as build_error:
             print(f"Error al construir el servicio de Drive: {build_error}")
             raise build_error


# --- Función de subida modificada (sin cambios en la lógica principal, pero coherente) ---
async def upload_to_drive_async_with_progress(file_path: str, file_name: str, progress_callback=None):
    """
    Sube un archivo a Google Drive usando OAuth de forma asíncrona y lo comparte públicamente.
    Incluye un callback de progreso que se llama con poca frecuencia.
    """
    print(f"Iniciando subida asíncrona (CON PROGRESO LIMITADO) de '{file_name}' a Google Drive (OAuth)...")
    load_credentials()
    loop = asyncio.get_event_loop()
    service = await loop.run_in_executor(None, get_drive_service)

    def upload_and_share_task():
        print("Ejecutando tarea de subida y compartir en thread (CON PROGRESO LIMITADO)...")
        try:
            # Mantener el nombre original del archivo
            file_metadata = {'name': file_name}
            media = MediaFileUpload(file_path, mimetype='video/mp4', resumable=True, chunksize=1024*1024)
            request = service.files().create(body=file_metadata, media_body=media)

            response = None
            # last_callback_time = 0 # No se usa en la versión simplificada del callback
            while response is None:
                status, response = request.next_chunk()
                # La lógica de hitos está en main.py, aquí solo subimos.
                # Si se quisiera un progreso interno muy básico:
                # if progress_callback and status:
                #     now = time.time()
                #     percent = int((status.resumable_progress / status.total_size) * 100)
                #     if now - last_callback_time > 60:
                #         future = asyncio.run_coroutine_threadsafe(progress_callback(percent), loop)
                #         last_callback_time = now

            file_id = response.get('id')
            print(f"Subida a Google Drive completada. ID del archivo: {file_id}")

            print(f"Compartiendo archivo {file_id} públicamente...")
            permission = {
                'type': 'anyone',
                'role': 'reader',
                'allowFileDiscovery': False
            }
            service.permissions().create(
                fileId=file_id,
                body=permission,
                fields='id'
            ).execute()
            print(f"Archivo {file_id} compartido públicamente con éxito.")

            return file_id
        except Exception as e:
            print(f"Error interno en la tarea de subida y compartir: {e}")
            raise

    try:
        drive_id = await loop.run_in_executor(None, upload_and_share_task)
        print(f"ID de archivo en Google Drive (compartido) obtenido: {drive_id}")
        return drive_id
    except Exception as e:
        print(f"Error durante la subida/compartir a Google Drive (OAuth): {e}")
        import traceback
        traceback.print_exc()
        raise

# --- Constantes para la paginación ---
DRIVE_ITEMS_PER_PAGE = 10

# --- Funciones NUEVAS para gestión directa de la unidad de Drive ---

async def list_drive_contents_async(page_number: int = 1, folder_id: str = 'root'):
    """
    Lista el contenido de una carpeta/unidad de Google Drive usando las credenciales del bot.
    Por defecto, lista 'My Drive' (folder_id='root'). Para unidad compartida, usa su ID.

    Args:
        page_number (int): Número de página a mostrar (comenzando desde 1).
        folder_id (str): ID de la carpeta/unidad a listar. 'root' para My Drive.

    Returns:
        dict: Diccionario con 'files' (lista de archivos), 'total_files', 'pages', 'current_page'.
    """
    print(f"Iniciando listado asíncrono del contenido de Drive (pagina {page_number}, folder_id={folder_id})...")
    
    load_credentials() 
    loop = asyncio.get_event_loop()
    service = await loop.run_in_executor(None, get_drive_service)

    def list_drive_task():
        print("Ejecutando tarea de listado de contenido de Drive en thread...")
        try:
            # Calcular el índice de inicio para la paginación
            start_index = (page_number - 1) * DRIVE_ITEMS_PER_PAGE
            
            # Listar archivos/carpetas dentro de la carpeta/unidad especificada
            # fields minimiza la cantidad de datos transferidos
            # orderBy ordena por nombre
            # q filtra por items no borrados en la carpeta padre
            results = service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                pageSize=DRIVE_ITEMS_PER_PAGE,
                fields="nextPageToken, files(id, name, size, mimeType, createdTime)",
                orderBy="createdTime desc", # Más recientes primero
                pageToken=None # Simplificación para paginación básica
            ).execute()
            
            items = results.get('files', [])
            
            # Asegurar que 'size' sea un entero (0 si no existe o es inválido)
            processed_items = []
            for item in items:
                try:
                    size_bytes = int(item.get('size', 0))
                except (ValueError, TypeError):
                    size_bytes = 0
                item['size'] = size_bytes
                processed_items.append(item)

            # Para paginación precisa, se necesitaría el conteo total.
            # Como simplificación, asumimos que si hay items, podría haber más si se llenó la página.
            total_files_on_page = len(processed_items)
            pages = page_number if total_files_on_page == DRIVE_ITEMS_PER_PAGE else page_number
            if total_files_on_page == 0 and page_number > 1:
                 pages = page_number - 1 # Retroceder una página si está vacía
            
            print(f"Listado de contenido de Drive completado. Pagina {page_number}/{pages}, {total_files_on_page} items.")
            return {
                'files': processed_items,
                'total_files': total_files_on_page, # Aproximado
                'pages': pages, # Aproximado
                'current_page': page_number,
                'has_more': 'nextPageToken' in results # Indicador si hay más páginas
            }
        except Exception as e:
            print(f"Error interno en la tarea de listado de contenido de Drive: {e}")
            import traceback
            traceback.print_exc()
            raise

    try:
        result = await loop.run_in_executor(None, list_drive_task)
        return result
    except Exception as e:
        print(f"Error durante el listado del contenido de Google Drive (OAuth/Servicio): {e}")
        import traceback
        traceback.print_exc()
        raise


async def delete_drive_file_async(file_id: str):
    """
    Borra un archivo de Google Drive usando su ID, usando las credenciales del bot.

    Args:
        file_id (str): El ID del archivo a borrar.

    Raises:
        Exception: Si ocurre cualquier error durante el borrado.
    """
    print(f"Iniciando borrado asíncrono del archivo {file_id} en Google Drive (OAuth/Servicio)...")
    
    load_credentials() 
    loop = asyncio.get_event_loop()
    service = await loop.run_in_executor(None, get_drive_service)

    def delete_task():
        print(f"Ejecutando tarea de borrado para {file_id} en thread...")
        try:
            service.files().delete(fileId=file_id).execute()
            print(f"Archivo {file_id} borrado exitosamente de Google Drive.")
        except Exception as e:
             print(f"Error interno en la tarea de borrado para {file_id}: {e}")
             raise # Relanzar para que el handler exterior lo capture

    try:
        await loop.run_in_executor(None, delete_task)
    except Exception as e:
        print(f"Error durante el borrado del archivo {file_id} de Google Drive (OAuth/Servicio): {e}")
        import traceback
        traceback.print_exc()
        raise


async def delete_all_drive_files_async(folder_id: str = 'root'):
    """
    Borra todos los archivos NO EN LA PAPELERA de una carpeta/unidad de Google Drive.
    ADVERTENCIA: Esto borrará todos los archivos accesibles por la cuenta del bot en esa carpeta/unidad.

    Args:
        folder_id (str): ID de la carpeta/unidad. 'root' para My Drive.
    """
    print(f"Iniciando borrado MASIVO de archivos en Google Drive (carpeta {folder_id}) (OAuth/Servicio)...")
    
    load_credentials() 
    loop = asyncio.get_event_loop()
    service = await loop.run_in_executor(None, get_drive_service)

    def delete_all_task():
        print("Ejecutando tarea de borrado MASIVO en thread...")
        try:
            # Obtener lista de todos los archivos NO EN LA PAPELERA en la carpeta especificada
            results = service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                pageSize=1000, # Obtener muchos para minimizar llamadas
                fields="files(id, name)" # Solo necesitamos ID
            ).execute()
            items = results.get('files', [])
            
            if not items:
                print("No se encontraron archivos para borrar en la carpeta especificada.")
                return

            print(f"Se encontraron {len(items)} archivos para borrar en la carpeta {folder_id}.")
            
            # Borrar cada archivo
            deleted_count = 0
            failed_count = 0
            for item in items:
                file_id = item['id']
                file_name = item.get('name', 'Desconocido')
                try:
                    service.files().delete(fileId=file_id).execute()
                    print(f"Archivo borrado: {file_name} ({file_id})")
                    deleted_count += 1
                except Exception as e:
                    print(f"Error borrando archivo {file_name} ({file_id}): {e}")
                    failed_count += 1
                    # No lanzamos excepción aquí para intentar borrar los demás
            
            print(f"Borrado MASIVO completado en carpeta {folder_id}. Éxito: {deleted_count}, Fallidos: {failed_count}.")
        except Exception as e:
             print(f"Error interno en la tarea de borrado MASIVO: {e}")
             raise

    try:
        await loop.run_in_executor(None, delete_all_task)
    except Exception as e:
        print(f"Error durante el borrado MASIVO de archivos de Google Drive (OAuth/Servicio): {e}")
        import traceback
        traceback.print_exc()
        raise

# --- Función para limpiar el archivo temporal al finalizar (sin cambios) ---
import atexit
_temp_token_file = None
def cleanup_temp_token_file():
    global _temp_token_file
    if _temp_token_file and os.path.exists(_temp_token_file):
        try:
            os.unlink(_temp_token_file)
            print(f"Archivo temporal de token eliminado al finalizar: {_temp_token_file}")
        except Exception as e:
             print(f"Advertencia: Error al eliminar archivo temporal de token al finalizar: {e}")
atexit.register(cleanup_temp_token_file)

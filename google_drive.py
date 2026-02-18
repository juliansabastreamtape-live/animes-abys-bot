#google_drive.py (Versión completa con todas las funciones y correcciones)
import asyncio
import os
import json
import time
import tempfile
import math
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
import config
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
    
    try:
        service = build('drive', 'v3', credentials=_credentials)
        about = service.about().get(fields="user").execute()
        user_email = about.get('user', {}).get('emailAddress', 'Desconocido')
        print(f"Usando cuenta de Google Drive: {user_email}")
        return service
    except Exception as e:
        print(f"Error al obtener información del usuario de Drive o crear el servicio: {e}")
        try:
            return build('drive', 'v3', credentials=_credentials)
        except Exception as build_error:
            print(f"Error al construir el servicio de Drive: {build_error}")
            raise build_error

# =============================================================================
# FUNCIÓN DE SUBIDA CON PROGRESO
# =============================================================================

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
            file_metadata = {'name': file_name}
            media = MediaFileUpload(file_path, mimetype='video/mp4', resumable=True, chunksize=1024*1024)
            request = service.files().create(body=file_metadata, media_body=media)

            response = None
            while response is None:
                status, response = request.next_chunk()

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

# =============================================================================
# CONSTANTES PARA PAGINACIÓN
# =============================================================================
ITEMS_PER_PAGE = 10

# =============================================================================
# FUNCIONES PARA GESTIONAR ARCHIVOS SUBIDOS POR EL BOT
# =============================================================================

async def list_uploaded_files_async(page_number: int = 1):
    """
    Lista SOLO los archivos que el bot ha subido, obteniendo detalles de la API de Drive.
    """
    print(f"Iniciando listado asíncrono de archivos SUBIDOS POR EL BOT (pagina {page_number})...")
    load_credentials()
    loop = asyncio.get_event_loop()
    service = await loop.run_in_executor(None, get_drive_service)
    
    def list_task():
        print("Ejecutando tarea de listado de archivos subidos en thread...")
        try:
            uploaded_entries = get_uploaded_files()
            
            if not uploaded_entries:
                print("No hay archivos registrados como subidos por el bot.")
                return {
                    'files': [],
                    'total_files': 0,
                    'pages': 1,
                    'current_page': page_number
                }
            
            file_ids = [entry['file_id'] for entry in uploaded_entries]
            file_id_to_name = {entry['file_id']: entry['original_name'] for entry in uploaded_entries}

            if not file_ids:
                items = []
            else:
                items = []
                batch_size = 50
                for i in range(0, len(file_ids), batch_size):
                    batch_file_ids = file_ids[i:i + batch_size]
                    print(f"Procesando lote de IDs: {batch_file_ids[:3]}... (total {len(batch_file_ids)})")

                    if len(batch_file_ids) == 1:
                        single_id = batch_file_ids[0]
                        ids_query = f"id = '{single_id}'"
                        print(f"  Consulta para 1 archivo (lote): {ids_query}")
                    else:
                        escaped_ids = [fid.replace("'", "\\'") for fid in batch_file_ids]
                        ids_query = f"id in ({', '.join(repr(fid) for fid in escaped_ids)})"
                        print(f"  Consulta para {len(batch_file_ids)} archivos (lote): {ids_query[:100]}...")

                    try:
                        results = service.files().list(
                            q=ids_query,
                            fields="files(id, name, size, mimeType)",
                            orderBy="name"
                        ).execute()
                        batch_items = results.get('files', [])
                        items.extend(batch_items)
                        print(f"  Lote procesado, obtenidos {len(batch_items)} archivos.")
                    except HttpError as http_err:
                        print(f"  Error HTTP al listar lote {i//batch_size + 1}: {http_err}")
                        print(f"  Consulta fallida: {ids_query}")
                        continue
                    except Exception as batch_err:
                        print(f"  Error inesperado al listar lote {i//batch_size + 1}: {batch_err}")
                        print(f"  Consulta fallida: {ids_query}")
                        continue

            processed_items = []
            for item in items:
                file_id = item['id']
                original_name = file_id_to_name.get(file_id, item.get('name', 'Nombre_Desconocido'))
                item['original_name'] = original_name
                try:
                    size_bytes = int(item.get('size', 0))
                except (ValueError, TypeError):
                    size_bytes = 0
                item['size'] = size_bytes
                processed_items.append(item)

            total_files = len(processed_items)
            pages = math.ceil(total_files / ITEMS_PER_PAGE) if total_files > 0 else 1
            start_index = (page_number - 1) * ITEMS_PER_PAGE
            end_index = start_index + ITEMS_PER_PAGE
            paginated_items = processed_items[start_index:end_index]

            print(f"Listado de archivos subidos completado. Pagina {page_number}/{pages}, {len(paginated_items)} archivos encontrados en Drive.")
            return {
                'files': paginated_items,
                'total_files': total_files,
                'pages': pages,
                'current_page': page_number
            }
        except Exception as e:
            print(f"Error interno en la tarea de listado de archivos subidos: {e}")
            import traceback
            traceback.print_exc()
            raise

    try:
        result = await loop.run_in_executor(None, list_task)
        return result
    except Exception as e:
        print(f"Error durante el listado de archivos subidos de Google Drive (OAuth): {e}")
        import traceback
        traceback.print_exc()
        raise

async def delete_uploaded_file_async(file_id: str):
    """
    Borra un archivo subido por el bot de Google Drive y de la base de datos local.
    """
    print(f"Iniciando borrado asíncrono del archivo SUBIDO POR EL BOT {file_id} en Google Drive (OAuth)...")
    load_credentials()
    loop = asyncio.get_event_loop()
    service = await loop.run_in_executor(None, get_drive_service)
    
    def delete_task():
        print(f"Ejecutando tarea de borrado para {file_id} en thread...")
        try:
            service.files().delete(fileId=file_id).execute()
            print(f"Archivo {file_id} borrado exitosamente de Google Drive.")
            remove_uploaded_file_record(file_id)
        except Exception as e:
            print(f"Error interno en la tarea de borrado para {file_id}: {e}")
            remove_uploaded_file_record(file_id)
            raise

    try:
        await loop.run_in_executor(None, delete_task)
    except Exception as e:
        print(f"Error durante el borrado del archivo {file_id} de Google Drive (OAuth): {e}")
        import traceback
        traceback.print_exc()
        raise

async def delete_all_uploaded_files_async():
    """
    Borra todos los archivos que el bot ha subido, tanto de Drive como de la DB local.
    """
    print("Iniciando borrado MASIVO de archivos SUBIDOS POR EL BOT en Google Drive (OAuth)...")
    load_credentials()
    loop = asyncio.get_event_loop()
    service = await loop.run_in_executor(None, get_drive_service)
    
    def delete_all_task():
        print("Ejecutando tarea de borrado MASIVO de archivos subidos en thread...")
        try:
            uploaded_entries = get_uploaded_files()

            if not uploaded_entries:
                print("No hay archivos registrados como subidos por el bot para borrar.")
                return

            print(f"Se encontraron {len(uploaded_entries)} archivos registrados para borrar.")

            for entry in uploaded_entries:
                file_id = entry['file_id']
                try:
                    service.files().delete(fileId=file_id).execute()
                    print(f"Archivo {file_id} borrado de Google Drive.")
                except Exception as e:
                    print(f"Error borrando archivo {file_id} de Drive: {e}")

            clear_all_uploaded_file_records()
            print("Borrado MASIVO de archivos subidos completado.")
        except Exception as e:
            print(f"Error interno en la tarea de borrado MASIVO de archivos subidos: {e}")
            raise

    try:
        await loop.run_in_executor(None, delete_all_task)
    except Exception as e:
        print(f"Error durante el borrado MASIVO de archivos subidos de Google Drive (OAuth): {e}")
        import traceback
        traceback.print_exc()
        raise

# =============================================================================
# FUNCIONES PARA GESTIONAR TODO EL CONTENIDO DE GOOGLE DRIVE (No solo subidos)
# =============================================================================

async def list_drive_contents_async(page_number: int = 1, folder_id: str = 'root'):
    """
    Lista el contenido de una carpeta de Google Drive (por defecto 'root' = Mi Unidad).
    """
    print(f"Iniciando listado asíncrono de Google Drive (carpeta: {folder_id}, página {page_number})...")
    load_credentials()
    loop = asyncio.get_event_loop()
    service = await loop.run_in_executor(None, get_drive_service)
    
    def list_task():
        print(f"Ejecutando tarea de listado de Drive para carpeta: {folder_id}")
        try:
            query = f"'{folder_id}' in parents and trashed = false"
            
            results = service.files().list(
                q=query,
                pageSize=10,
                pageToken=None,
                fields="nextPageToken, files(id, name, size, mimeType, modifiedTime)",
                orderBy="modifiedTime desc"
            ).execute()
            
            files = results.get('files', [])
            next_page_token = results.get('nextPageToken')
            has_more = next_page_token is not None
            
            for item in files:
                try:
                    item['size'] = int(item.get('size', 0))
                except (ValueError, TypeError):
                    item['size'] = 0
            
            print(f"Listado de Drive completado. Página {page_number}, {len(files)} archivos encontrados.")
            
            return {
                'files': files,
                'current_page': page_number,
                'has_more': has_more,
                'next_page_token': next_page_token
            }
        except Exception as e:
            print(f"Error interno en la tarea de listado de Drive: {e}")
            import traceback
            traceback.print_exc()
            raise

    try:
        result = await loop.run_in_executor(None, list_task)
        return result
    except Exception as e:
        print(f"Error durante el listado de Google Drive: {e}")
        import traceback
        traceback.print_exc()
        raise

async def delete_drive_file_async(file_id: str):
    """
    Borra un archivo de Google Drive por su ID (cualquier archivo, no solo subidos por el bot).
    """
    print(f"Iniciando borrado asíncrono del archivo {file_id} en Google Drive...")
    load_credentials()
    loop = asyncio.get_event_loop()
    service = await loop.run_in_executor(None, get_drive_service)
    
    def delete_task():
        print(f"Ejecutando tarea de borrado para {file_id}")
        try:
            service.files().delete(fileId=file_id).execute()
            print(f"Archivo {file_id} borrado exitosamente de Google Drive.")
        except Exception as e:
            print(f"Error interno en la tarea de borrado para {file_id}: {e}")
            raise

    try:
        await loop.run_in_executor(None, delete_task)
    except Exception as e:
        print(f"Error durante el borrado del archivo {file_id} de Google Drive: {e}")
        import traceback
        traceback.print_exc()
        raise

async def delete_all_drive_files_async(folder_id: str = 'root'):
    """
    Borra todos los archivos de una carpeta de Google Drive (por defecto 'root').
    ⚠️ Acción destructiva: úsala con precaución.
    """
    print(f"Iniciando borrado MASIVO de archivos en carpeta: {folder_id}")
    load_credentials()
    loop = asyncio.get_event_loop()
    service = await loop.run_in_executor(None, get_drive_service)
    
    def delete_all_task():
        print(f"Ejecutando tarea de borrado masivo en carpeta: {folder_id}")
        try:
            all_files = []
            page_token = None
            while True:
                results = service.files().list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    pageSize=100,
                    pageToken=page_token,
                    fields="nextPageToken, files(id, name)"
                ).execute()
                
                files = results.get('files', [])
                all_files.extend(files)
                page_token = results.get('nextPageToken')
                
                if not page_token:
                    break
            
            print(f"Se encontraron {len(all_files)} archivos para borrar.")
            
            for file in all_files:
                file_id = file['id']
                file_name = file['name']
                try:
                    service.files().delete(fileId=file_id).execute()
                    print(f"✅ Borrado: {file_name} ({file_id})")
                except Exception as e:
                    print(f"❌ Error borrando {file_name} ({file_id}): {e}")
            
            print(f"Borrado masivo completado. {len(all_files)} archivos procesados.")
            
        except Exception as e:
            print(f"Error interno en la tarea de borrado masivo: {e}")
            raise

    try:
        await loop.run_in_executor(None, delete_all_task)
    except Exception as e:
        print(f"Error durante el borrado masivo de Google Drive: {e}")
        import traceback
        traceback.print_exc()
        raise

# =============================================================================
# LIMPIEZA DE ARCHIVO TEMPORAL AL FINALIZAR
# =============================================================================

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

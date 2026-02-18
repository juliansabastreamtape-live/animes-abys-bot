# db.py (Versión corregida y optimizada para diagnóstico y robustez)
from tinydb import TinyDB, Query
import time
import threading
import traceback # Para imprimir stack traces detallados

# --- DB para procesos antispam ---
DB_PATH = 'bot_db.json'
db = TinyDB(DB_PATH)
Message = Query()

# --- DB para archivos subidos por el bot ---
UPLOADED_FILES_DB_PATH = 'uploaded_files_db.json'
uploaded_files_db = TinyDB(UPLOADED_FILES_DB_PATH)
UploadedFile = Query()

# Un lock para operaciones críticas en la DB de procesos (ayuda con concurrencia básica)
_db_lock = threading.Lock()

# --- Funciones para procesos antispam ---
def _cleanup_old_processing():
    """Función auxiliar para limpiar entradas antiguas."""
    current_time = time.time()
    one_hour_ago = current_time - 3600 # 3600 segundos = 1 hora
    with _db_lock:
        removed = db.remove((Message.status == 'processing') & (Message.timestamp < one_hour_ago))
    if removed: # Solo imprimir si se eliminó algo
        print(f"Limpieza: Se eliminaron {len(removed)} registros de procesos antiguos.")

def try_start_processing(message_id: int) -> bool:
    """
    Intenta marcar un mensaje como 'en proceso'.
    Devuelve True si se pudo iniciar el procesamiento (no estaba en proceso).
    Devuelve False si ya estaba en proceso.
    Esta operación intenta ser lo más atómica posible con TinyDB.
    """
    current_time = time.time()
    _cleanup_old_processing()

    with _db_lock: # Adquirir el lock para la operación crítica
        existing_entry = db.get((Message.id == message_id) & (Message.status == 'processing'))
        if existing_entry:
            print(f"try_start_processing: [BLOQUEADO] Mensaje {message_id} ya está en proceso (registrado en DB).")
            return False # Ya está en proceso, no se puede iniciar
        db.upsert({'id': message_id, 'status': 'processing', 'timestamp': current_time}, Message.id == message_id)
        print(f"try_start_processing: [OK] Mensaje {message_id} marcado como en proceso.")
        return True # Se inició el procesamiento

def finish_processing(message_id: int):
    """Marca un mensaje como 'finalizado'."""
    current_time = time.time()
    with _db_lock:
        updated = db.update({'status': 'finished', 'end_timestamp': current_time}, Message.id == message_id)
    if updated: # updated es una lista de IDs actualizados
         print(f"finish_processing: Mensaje {message_id} marcado como finalizado.")
    else:
         print(f"finish_processing: Mensaje {message_id} no encontrado para finalizar (quizás ya fue limpiado o no se inició).")

# --- Funciones para archivos subidos por el bot ---

def record_uploaded_file(file_id: str, original_name: str):
    """
    Registra un archivo subido en la base de datos.
    Esta función ahora incluye logs detallados y manejo explícito de errores para diagnóstico.
    """
    print(f"record_uploaded_file: INICIANDO registro para ID={file_id}, Nombre='{original_name}'")
    timestamp = time.time()
    try:
        # Verificar si el archivo ya existe en la DB (opcional, para diagnóstico)
        existing_entry = uploaded_files_db.get(UploadedFile.file_id == file_id)
        if existing_entry:
            print(f"record_uploaded_file: AVISO - ID={file_id} ya existe en DB. Actualizando entrada existente.")
        
        # Datos a insertar/actualizar
        data_to_upsert = {
            'file_id': file_id,
            'original_name': original_name,
            'upload_timestamp': timestamp
        }
        print(f"record_uploaded_file: Preparando datos para upsert: {data_to_upsert}")

        # --- Operación de escritura en la base de datos ---
        print(f"record_uploaded_file: Intentando operación upsert en uploaded_files_db.json...")
        uploaded_files_db.upsert(data_to_upsert, UploadedFile.file_id == file_id)
        print(f"record_uploaded_file: ✅ ÉXITO - Archivo {file_id} ('{original_name}') REGISTRADO/ACTUALIZADO en uploaded_files_db.json.")
        
    except Exception as e:
        # --- Manejo de errores crítico ---
        error_msg = f"record_uploaded_file: ❌ ERROR CRÍTICO al registrar archivo {file_id} ('{original_name}') en uploaded_files_db.json: {e}"
        print(error_msg)
        traceback.print_exc() # Imprimir el stack trace completo para diagnóstico
        # Relanzar la excepción como RuntimeError para que la función llamadora (en main.py) la capture
        raise RuntimeError(error_msg) from e

def get_uploaded_files():
    """
    Obtiene la lista de todos los file_ids registrados.
    Devuelve siempre una lista de diccionarios [{'file_id': ..., 'original_name': ...}, ...].
    Devuelve una lista vacía [] si no hay archivos o si ocurre un error.
    """
    print("get_uploaded_files: INICIANDO obtención de lista de archivos registrados...")
    try:
        entries = uploaded_files_db.all()
        print(f"get_uploaded_files: Obtenidos {len(entries)} archivos brutos de uploaded_files_db.json.")
        
        # Transformar las entradas en el formato esperado
        # Asegurar que 'file_id' y 'original_name' existan en cada entrada
        processed_entries = []
        for entry in entries:
            file_id = entry.get('file_id')
            original_name = entry.get('original_name', 'Nombre_Desconocido')
            # Solo añadir entradas válidas (con file_id)
            if file_id:
                processed_entries.append({'file_id': file_id, 'original_name': original_name})
            else:
                 print(f"get_uploaded_files: AVISO - Entrada inválida omitida: {entry}")

        print(f"get_uploaded_files: ✅ ÉXITO - Lista procesada de archivos: {processed_entries}")
        return processed_entries # Devolver la lista procesada (puede estar vacía)
        
    except Exception as e:
        error_msg = f"get_uploaded_files: ❌ ERROR al obtener archivos de uploaded_files_db.json: {e}"
        print(error_msg)
        traceback.print_exc()
        # Devolver una lista vacía en caso de error para evitar romper la lógica del llamador
        return []

def remove_uploaded_file_record(file_id: str):
    """Elimina el registro de un archivo subido."""
    try:
        removed = uploaded_files_db.remove(UploadedFile.file_id == file_id)
        if removed:
            print(f"remove_uploaded_file_record: Registro de archivo {file_id} eliminado.")
        else:
            print(f"remove_uploaded_file_record: Registro de archivo {file_id} no encontrado.")
    except Exception as e:
         error_msg = f"remove_uploaded_file_record: ❌ ERROR al eliminar registro de archivo {file_id}: {e}"
         print(error_msg)
         traceback.print_exc()
         # No relanzamos la excepción aquí, ya que la eliminación fallida es menos crítica

def clear_all_uploaded_file_records():
    """Elimina todos los registros de archivos subidos."""
    try:
        count = len(uploaded_files_db.all())
        uploaded_files_db.truncate() # Elimina todos los documentos
        print(f"clear_all_uploaded_file_records: {count} registros eliminados.")
    except Exception as e:
         error_msg = f"clear_all_uploaded_file_records: ❌ ERROR al limpiar todos los registros: {e}"
         print(error_msg)
         traceback.print_exc()
         # No relanzamos la excepción aquí

# --- Limpieza inicial al importar el módulo ---
# No es necesaria una limpieza explícita aquí para uploaded_files_db
# ya que no tiene timestamps de expiración como los procesos.

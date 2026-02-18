# main.py (Versión completa y actualizada)
import asyncio
# --- Forzar la creación de un event loop antes de importar pyrogram ---
try:
    asyncio.get_running_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
# ---------------------------------------------------------------------
import os
import threading
import time
import math
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
# Importaciones para Flask
from flask import Flask

# --- Importar la lista blanca desde config ---
from config import WHITELISTED_USERS

# --- Configuración de Flask ---
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    """Punto de entrada simple para que Render detecte un puerto abierto."""
    return "✅ Bot is running!", 200

def run_flask():
    """Inicia el servidor Flask en un hilo separado."""
    port = int(os.environ.get('PORT', 8000))
    flask_app.run(host='0.0.0.0', port=port)

# --- Logs de diagnóstico iniciales ---
print("Iniciando configuracion del bot...")

# Importaciones locales
try:
    from config import API_ID, API_HASH, BOT_TOKEN # WHITELISTED_USERS ya importado arriba
    from db import try_start_processing, finish_processing, record_uploaded_file
    # Importar las nuevas funciones de google_drive
    from google_drive import (
        upload_to_drive_async_with_progress,
        list_uploaded_files_async,
        delete_uploaded_file_async,
        delete_all_uploaded_files_async,
        # --- NUEVAS FUNCIONES ---
        list_drive_contents_async,
        delete_drive_file_async,
        delete_all_drive_files_async
    )
    from hydrax_api import import_to_hydrax
    from utils import safe_edit_message, safe_reply_message, safe_send_message, safe_delete_file
    print("Importaciones locales completadas.")
except ImportError as e:
    print(f"Error al importar módulos: {e}")
    raise

print("Credenciales cargadas desde config.py.")
print(f"Usuarios en lista blanca: {WHITELISTED_USERS}")

# --- Función para verificar usuarios ---
def is_user_whitelisted(user_id: int) -> bool:
    """
    Verifica si un user_id está en la lista blanca.
    Si la lista está vacía, permite a todos (modo abierto).
    """
    if not WHITELISTED_USERS:
        return True # Modo abierto
    return user_id in WHITELISTED_USERS

# --- Logs de diagnóstico: Creación del cliente ---
print("Creando cliente de Pyrogram...")
try:
    # Renombrar la instancia de Client para evitar conflictos con Flask
    pyrogram_app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    print("Cliente de Pyrogram creado exitosamente.")
except Exception as e:
    print(f"Error al crear el cliente de Pyrogram: {e}")
    raise

# --- Diccionario para rastrear procesos cancelables ---
# {message_id: {'cancel_flag': asyncio.Event, 'process_task': asyncio.Task}}
cancelable_processes = {}

# --- Variable para rastrear si los comandos del bot ya se han establecido ---
_bot_commands_set = False
_bot_commands_lock = asyncio.Lock() # Lock para evitar concurrencia en la inicialización

async def update_progress(message: Message, status: str, reply_markup=None):
    """Actualiza el mensaje de progreso en Telegram usando la función segura."""
    try:
        # Pasar reply_markup a safe_edit_message
        await safe_edit_message(message, status, reply_markup=reply_markup)
    except Exception as e:
        print(f"Error al actualizar progreso: {e}")

# --- Función auxiliar para verificar hitos de progreso ---
def _should_update_progress(current_percent, last_percent, milestones=[25, 50, 75, 100]):
    """
    Determina si se debe actualizar el mensaje de progreso basado en hitos específicos.
    """
    for milestone in milestones:
        # Si el porcentaje actual ha cruzado un hito y el último no, debemos actualizar
        if last_percent < milestone <= current_percent:
            return True
    return False

# --- Funciones auxiliares para /list y /listdrive ---

def format_size(size_bytes: int) -> str:
    """Formatea un tamaño en bytes a una unidad legible (KB, MB, GB)."""
    if size_bytes == 0:
        return "0B"
    size_names = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_names[i]}"

def get_file_icon(mime_type: str) -> str:
    """Devuelve un emoji icono basado en el tipo MIME."""
    if mime_type.startswith('video/'):
        return "🎬"
    elif mime_type.startswith('image/'):
        return "🖼️"
    elif mime_type.startswith('audio/'):
        return "🎵"
    elif mime_type == 'application/vnd.google-apps.folder':
        return "📁"
    elif mime_type.startswith('application/'):
        return "📄"
    else:
        return "📄" # Icono por defecto

async def send_file_list(client: Client, chat_id: int, page: int = 1, message_to_edit: Message = None):
    """Envía o edita el mensaje con la lista de archivos SUBIDOS POR EL BOT paginada y botones."""
    try:
        print(f"Obteniendo lista de archivos SUBIDOS POR EL BOT, pagina {page}...")
        # --- Cambio aquí: usar la nueva función ---
        file_data = await list_uploaded_files_async(page_number=page)
        files = file_data['files']
        total_files = file_data['total_files']
        pages = file_data['pages']
        current_page = file_data['current_page']

        if not files:
            text = "📭 No se encontraron archivos subidos por este bot en Google Drive."
            if message_to_edit:
                await safe_edit_message(message_to_edit, text)
            else:
                await safe_send_message(client, chat_id, text)
            return

        text = f"📋 **Archivos subidos por el bot en Google Drive** (Página {current_page}/{pages}):\n\n"
        for i, file in enumerate(files):
            index = (current_page - 1) * 10 + i + 1
            # Usar el nombre original guardado
            name = file.get('original_name', file.get('name', 'Sin_nombre'))
            size = format_size(file.get('size', 0))
            file_id = file.get('id', '')
            display_name = (name[:30] + '...') if len(name) > 33 else name
            text += f"{index}. `{display_name}` ({size})\n"

        # Crear botones inline
        buttons = []

        # Botones de navegación de página
        nav_buttons = []
        if pages > 1:
            if current_page > 1:
                nav_buttons.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"list_{current_page - 1}"))
            if current_page < pages:
                nav_buttons.append(InlineKeyboardButton("Siguiente ➡️", callback_data=f"list_{current_page + 1}"))
            if nav_buttons:
                buttons.append(nav_buttons)

        # Botón de Refrescar
        buttons.append([InlineKeyboardButton("🔄 Refrescar", callback_data=f"list_{current_page}")])

        # Botón de Borrar Todo (colocado aparte para evitar accidentes)
        # Aclarar que es solo para archivos subidos
        buttons.append([InlineKeyboardButton("🗑️ Borrar TODO (Subidos)", callback_data="delete_all_confirm")])

        reply_markup = InlineKeyboardMarkup(buttons)

        if message_to_edit:
            await safe_edit_message(message_to_edit, text, reply_markup=reply_markup)
        else:
            await safe_send_message(client, chat_id, text, reply_markup=reply_markup)

    except Exception as e:
        error_msg = f"❌ Error al listar archivos subidos por el bot: {e}"
        print(error_msg)
        import traceback
        traceback.print_exc()
        if message_to_edit:
            await safe_edit_message(message_to_edit, error_msg)
        else:
            await safe_send_message(client, chat_id, error_msg)


# --- NUEVA: Función auxiliar para /listdrive ---
async def send_drive_file_list(client: Client, chat_id: int, page: int = 1, message_to_edit: Message = None):
    """Envía o edita el mensaje con la lista de archivos de Google Drive paginada."""
    try:
        print(f"Obteniendo lista de archivos de Google Drive, pagina {page}...")
        # --- Llamar a la nueva función ---
        # Asume que usas 'root' para My Drive. Si usas una unidad compartida,
        # reemplaza 'root' con el ID de tu unidad compartida.
        file_data = await list_drive_contents_async(page_number=page, folder_id='root') # <<< AJUSTA 'root' SI ES NECESARIO
        files = file_data['files']
        # total_files = file_data['total_files'] # No es preciso con la implementación básica
        # pages = file_data['pages'] # No es preciso con la implementación básica
        current_page = file_data['current_page']
        has_more = file_data['has_more']

        if not files:
            text = "📭 No se encontraron archivos/carpetas en esta unidad/carpeta de Google Drive."
            if message_to_edit:
                await safe_edit_message(message_to_edit, text)
            else:
                await safe_send_message(client, chat_id, text)
            return

        text = f"📋 **Contenido de Google Drive** (Página {current_page}):\n\n"
        # Crear botones inline
        buttons = []
        for i, file in enumerate(files):
            # index = (current_page - 1) * 10 + i + 1 # Si tuviéramos el total preciso
            index = i + 1 # Para simplificar
            name = file.get('name', 'Sin_nombre')
            size = format_size(file.get('size', 0))
            mime_type = file.get('mimeType', '')
            file_id = file.get('id', '')
            icon = get_file_icon(mime_type)
            
            # Limitar longitud del nombre para que el mensaje no sea demasiado largo
            display_name = (name[:35] + '...') if len(name) > 38 else name
            text += f"{index}. {icon} `{display_name}` ({size})\n   `ID: {file_id}`\n\n"
            
            # --- NUEVO: Botón de Borrar para cada archivo ---
            buttons.append([InlineKeyboardButton(f"🗑️ Borrar {display_name[:20]}...", callback_data=f"drive_delete_single_{file_id}")])

        # Botones de navegación de página (simplificados)
        nav_buttons = []
        if current_page > 1:
            nav_buttons.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"drivelist_{current_page - 1}"))
        if has_more:
            nav_buttons.append(InlineKeyboardButton("Siguiente ➡️", callback_data=f"drivelist_{current_page + 1}"))
        
        if nav_buttons:
            buttons.append(nav_buttons)

        # Botón de Refrescar
        buttons.append([InlineKeyboardButton("🔄 Refrescar", callback_data=f"drivelist_{current_page}")])
        
        # Botón de Borrar Todo (con confirmación)
        buttons.append([InlineKeyboardButton("🗑️ Borrar TODO de Drive", callback_data="drive_delete_all_confirm")])

        reply_markup = InlineKeyboardMarkup(buttons)

        if message_to_edit:
            await safe_edit_message(message_to_edit, text, reply_markup=reply_markup)
        else:
            await safe_send_message(client, chat_id, text, reply_markup=reply_markup)

    except Exception as e:
        error_msg = f"❌ Error al listar contenido de Google Drive: {e}"
        print(error_msg)
        import traceback
        traceback.print_exc()
        if message_to_edit:
            await safe_edit_message(message_to_edit, error_msg)
        else:
            await safe_send_message(client, chat_id, error_msg)


# --- Función para configurar el menú de comandos del bot ---
async def set_bot_commands(client: Client):
    """Establece el menú de comandos del bot."""
    commands = [
        BotCommand("start", "Mostrar mensaje de inicio"),
        BotCommand("ping", "Verificar si el bot está activo"),
        BotCommand("list", "Listar archivos subidos por el bot (gestionables)"),
        BotCommand("listdrive", "Listar todo el contenido de Google Drive"),
        BotCommand("deletedrive", "Borrar un archivo de Drive por ID (/deletedrive <ID>)"),
        BotCommand("deletedriveall", "Borrar todos los archivos de Drive (con confirmación)"),
        # Añade más comandos aquí si los tienes
    ]
    try:
        await client.set_bot_commands(commands)
        print("✅ Menú de comandos del bot establecido.")
    except Exception as e:
        print(f"❌ Error al establecer el menú de comandos del bot: {e}")

# --- Comandos para verificar el estado del bot ---

@pyrogram_app.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    """Responde al comando /start indicando que el bot está activo."""
    global _bot_commands_set, _bot_commands_lock
    user_id = message.from_user.id
    print(f"Comando /start recibido de {user_id} ({message.from_user.first_name}).")
    
    # --- Inicialización de comandos (solo una vez) ---
    if not _bot_commands_set:
        async with _bot_commands_lock: # Asegurar que solo un /start lo haga
            if not _bot_commands_set: # Doble verificación dentro del lock
                try:
                    await set_bot_commands(client)
                    print("✅ Menú de comandos del bot establecido en /start.")
                except Exception as e:
                    print(f"❌ Error al establecer comandos en /start: {e}")
                _bot_commands_set = True # Marcar como establecido

    # Verificar lista blanca
    if not is_user_whitelisted(user_id):
        print(f"Acceso denegado a {user_id}. No está en la lista blanca.")
        try:
            await message.reply_text("❌ Acceso denegado. No estás en la lista de usuarios permitidos.")
        except Exception as e:
            print(f"Error al enviar mensaje de denegación: {e}")
        return # Salir si no está autorizado

    # Obtener información de la cuenta de Drive (opcional, solo para mostrar en logs)
    try:
        from google_drive import get_drive_service
        drive_service = get_drive_service()
        about = drive_service.about().get(fields="user").execute()
        user_email = about.get('user', {}).get('emailAddress', 'Desconocido')
        drive_info = f"\n📁 Cuenta de Google Drive: `{user_email}`"
    except Exception as e:
        print(f"Error al obtener info de Drive para /start: {e}")
        drive_info = "\n📁 Cuenta de Google Drive: (Error al obtener)"

    welcome_text = (
        "¡Hola! 👋\n"
        "Este bot está activo y listo para recibir videos.\n"
        "Envíame un video (de hasta 2GB) y lo procesaré automáticamente.\n"
        "Usa /ping para verificar nuevamente si estoy activo.\n"
        "Usa /list para ver y gestionar archivos subidos por el bot en Google Drive.\n"
        "Usa /listdrive para ver el contenido completo de tu unidad de Google Drive.\n"
        "Usa /deletedrive <ID> para borrar un archivo de Drive.\n"
        "Usa /deletedriveall para borrar todos los archivos de Drive."
        f"{drive_info}"
    )
    await safe_reply_message(message, welcome_text)
    print(f"Mensaje de bienvenida enviado a {user_id}.")

@pyrogram_app.on_message(filters.command("ping") & filters.private)
async def ping_command(client: Client, message: Message):
    """Responde al comando /ping indicando que el bot está activo."""
    user_id = message.from_user.id
    print(f"Comando /ping recibido de {user_id} ({message.from_user.first_name}).")
    
    # Verificar lista blanca
    if not is_user_whitelisted(user_id):
        print(f"Acceso denegado a {user_id}. No está en la lista blanca.")
        try:
            await message.reply_text("❌ Acceso denegado. No estás en la lista de usuarios permitidos.")
        except Exception as e:
            print(f"Error al enviar mensaje de denegación: {e}")
        return # Salir si no está autorizado

    pong_text = "✅ ¡Pong! El bot está activo y funcionando correctamente."
    await safe_reply_message(message, pong_text)
    print(f"Pong enviado a {user_id}.")

# --- Comando /list ---
@pyrogram_app.on_message(filters.command("list") & filters.private)
async def list_command(client: Client, message: Message):
    """Muestra la lista de archivos en Google Drive."""
    user_id = message.from_user.id
    print(f"Comando /list recibido de {user_id} ({message.from_user.first_name}).")
    
    # Verificar lista blanca
    if not is_user_whitelisted(user_id):
        print(f"Acceso denegado a {user_id} para /list. No está en la lista blanca.")
        try:
            await message.reply_text("❌ Acceso denegado. No estás en la lista de usuarios permitidos.")
        except Exception as e:
            print(f"Error al enviar mensaje de denegación: {e}")
        return

    try:
        await message.reply_text("🔍 Obteniendo lista de archivos subidos por el bot en Google Drive...")
        await send_file_list(client, message.chat.id, page=1)
    except Exception as e:
        error_msg = f"❌ Error al iniciar el listado: {e}"
        print(error_msg)
        await message.reply_text(error_msg)

# --- NUEVO: Comando /listdrive ---
@pyrogram_app.on_message(filters.command("listdrive") & filters.private)
async def list_drive_command(client: Client, message: Message):
    """Muestra el contenido de Google Drive."""
    user_id = message.from_user.id
    print(f"Comando /listdrive recibido de {user_id} ({message.from_user.first_name}).")
    
    # Verificar lista blanca (si la tienes activa)
    if not is_user_whitelisted(user_id):
        print(f"Acceso denegado a {user_id} para /listdrive. No está en la lista blanca.")
        try:
            await message.reply_text("❌ Acceso denegado. No estás en la lista de usuarios permitidos.")
        except Exception as e:
            print(f"Error al enviar mensaje de denegación: {e}")
        return

    try:
        await message.reply_text("🔍 Obteniendo lista de archivos de Google Drive...")
        await send_drive_file_list(client, message.chat.id, page=1)
    except Exception as e:
        error_msg = f"❌ Error al iniciar el listado de Drive: {e}"
        print(error_msg)
        await message.reply_text(error_msg)

# --- NUEVO: Comando /deletedrive <file_id> ---
@pyrogram_app.on_message(filters.command("deletedrive") & filters.private)
async def delete_drive_command(client: Client, message: Message):
    """Borra un archivo de Google Drive usando su ID."""
    user_id = message.from_user.id
    print(f"Comando /deletedrive recibido de {user_id} ({message.from_user.first_name}).")
    
    if not is_user_whitelisted(user_id):
        print(f"Acceso denegado a {user_id} para /deletedrive.")
        try:
            await message.reply_text("❌ Acceso denegado.")
        except Exception as e:
            print(f"Error al enviar mensaje de denegación: {e}")
        return

    command_parts = message.text.split()
    if len(command_parts) != 2:
        await message.reply_text("❌ Uso: `/deletedrive <FILE_ID>`\nObtén el FILE_ID usando `/listdrive`.")
        return

    file_id_to_delete = command_parts[1].strip()

    # Pedir confirmación
    confirm_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Sí, borrar", callback_data=f"drive_delete_confirm_{file_id_to_delete}")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="drive_cancel")]
    ])
    await message.reply_text(
        f"⚠️ **¿Estás seguro de que quieres borrar el archivo con ID `{file_id_to_delete}`?**\n"
        f"Esta acción no se puede deshacer.",
        reply_markup=confirm_markup
    )

# --- NUEVO: Comando /deletedriveall ---
@pyrogram_app.on_message(filters.command("deletedriveall") & filters.private)
async def delete_all_drive_command(client: Client, message: Message):
    """Borra todos los archivos de Google Drive (con confirmación)."""
    user_id = message.from_user.id
    print(f"Comando /deletedriveall recibido de {user_id} ({message.from_user.first_name}).")
    
    if not is_user_whitelisted(user_id):
        print(f"Acceso denegado a {user_id} para /deletedriveall.")
        try:
            await message.reply_text("❌ Acceso denegado.")
        except Exception as e:
            print(f"Error al enviar mensaje de denegación: {e}")
        return

    # Pedir confirmación
    confirm_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Sí, borrar TODO", callback_data="drive_delete_all_confirm")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="drive_cancel")]
    ])
    await message.reply_text(
        "⚠️ **¿Estás SEGURO de que quieres BORRAR TODOS los archivos de la unidad/carpeta de Google Drive?**\n"
        "Esta acción no se puede deshacer.",
        reply_markup=confirm_markup
    )


# Aplicar el filtro de lista blanca al manejador de videos
@pyrogram_app.on_message(filters.private & filters.video)
async def handle_video(client: Client, message: Message):
    """Maneja los videos recibidos, evitando duplicados de forma más robusta."""
    user_id = message.from_user.id
    print(f"Video recibido de {user_id} (Message ID: {message.id}).")
    
    # Verificar lista blanca
    if not is_user_whitelisted(user_id):
        print(f"Acceso denegado a {user_id} para enviar video. No está en la lista blanca.")
        return # Salir inmediatamente si no está autorizado

    # --- El resto de tu lógica de handle_video sigue aquí ---
    print(f"Video recibido. Message ID: {message.id}")

    # --- Verificación y bloqueo atómico ---
    if not try_start_processing(message.id):
        print(f"Mensaje {message.id} ya está en proceso o no se pudo iniciar. Ignorando.")
        return

    # --- Variables para el manejo del proceso ---
    processing_message = None
    temp_file_path = None
    # Variables para controlar la actualización de progreso por hitos
    last_download_percent = -1
    last_upload_percent = -1
    
    # --- Variables para cancelación ---
    # Crear un evento para señalar la cancelación
    cancel_event = asyncio.Event()
    download_task = None
    upload_task = None
    # Almacenar la referencia del proceso cancelable
    cancelable_processes[message.id] = {'cancel_flag': cancel_event, 'process_task': None} # Se actualizará más tarde
    # Variable para almacenar el nombre del archivo original
    original_file_name = None

    try:
        # 2. Enviar mensaje inicial de procesamiento
        print("Enviando mensaje inicial de procesamiento...")
        processing_message = await safe_reply_message(message, "🔄 Preparando para procesar el video...")

        # 3. Descargar archivo - CON callback de progreso limitado por hitos y botón de cancelar
        video = message.video
        original_file_name = video.file_name or f"video_{message.id}.mp4"
        file_name = original_file_name # Usar el nombre original
        print(f"Iniciando descarga de video: {file_name}")
        
        # Crear el teclado inline con el botón de cancelar
        cancel_button = InlineKeyboardButton("❌ Cancelar", callback_data=f"cancel_{message.id}")
        reply_markup = InlineKeyboardMarkup([[cancel_button]])
        
        # Actualizar mensaje con el botón de cancelar (sin porcentaje aún)
        await update_progress(processing_message, "⬇️ Descargando video...", reply_markup=reply_markup)
        
        # Función de callback para progreso de descarga (limitada a hitos 25, 50, 75, 100)
        async def download_progress_milestones(current, total):
            # Verificar si se solicitó cancelación
            if cancel_event.is_set():
                raise asyncio.CancelledError("Descarga cancelada por el usuario.")
            nonlocal last_download_percent
            current_percent = int((current / total) * 100)
            
            # Verificar si se debe actualizar basado en hitos
            if _should_update_progress(current_percent, last_download_percent):
                 await update_progress(processing_message, f"⬇️ Descargando video ({current_percent}%)...", reply_markup=reply_markup)
                 print(f"Progreso descarga actualizado por hito: {current_percent}%")
                 last_download_percent = current_percent

        # Descargar el archivo con callback limitado
        # Envolver la descarga en una tarea para poder cancelarla
        async def download_task_func():
            return await client.download_media(message, progress=download_progress_milestones)
        
        download_task = asyncio.create_task(download_task_func())
        cancelable_processes[message.id]['process_task'] = download_task # Actualizar referencia
        
        try:
            temp_file_path = await download_task
            print(f"Video descargado exitosamente a: {temp_file_path}")
            # Asegurarse de mostrar 100% al finalizar la descarga si no se mostró
            if last_download_percent < 100:
                 await update_progress(processing_message, "⬇️ Descargando video (100%)...", reply_markup=reply_markup)
        except asyncio.CancelledError:
            raise # Relanzar para ser capturado por el handler exterior
        finally:
            # Limpiar la referencia de la tarea de descarga
            if message.id in cancelable_processes:
                cancelable_processes[message.id]['process_task'] = None

        # 4. Subir a Google Drive (y compartir) - CON callback de progreso limitado por hitos y botón de cancelar
        # Actualizar mensaje con el botón de cancelar para la subida (sin porcentaje aún)
        await update_progress(processing_message, "☁️ Subiendo a Google Drive...", reply_markup=reply_markup)
        print("Iniciando subida a Google Drive...")

        # Función de callback para progreso de subida (limitada a hitos 25, 50, 75, 100)
        async def upload_progress_milestones(percent):
             # Verificar si se solicitó cancelación
             if cancel_event.is_set():
                 raise asyncio.CancelledError("Subida cancelada por el usuario.")
             nonlocal last_upload_percent
             current_percent = percent
             
             # Verificar si se debe actualizar basado en hitos
             if _should_update_progress(current_percent, last_upload_percent):
                 await update_progress(processing_message, f"☁️ Subiendo a Google Drive ({current_percent}%)...", reply_markup=reply_markup)
                 print(f"Progreso subida actualizado por hito: {current_percent}%")
                 last_upload_percent = current_percent

        # --- Llamada modificada CON progress_callback limitado y control de cancelación ---
        async def upload_task_func():
            return await upload_to_drive_async_with_progress(temp_file_path, file_name, progress_callback=upload_progress_milestones)
        
        upload_task = asyncio.create_task(upload_task_func())
        cancelable_processes[message.id]['process_task'] = upload_task # Actualizar referencia
        
        drive_id = None
        try:
            drive_id = await upload_task
            print(f"✅ ÉXITO: Archivo subido y compartido en Google Drive. ID OBTENIDO: {drive_id}")
            # Asegurarse de mostrar 100% al finalizar la subida si no se mostró
            if last_upload_percent < 100:
                 await update_progress(processing_message, "☁️ Subiendo a Google Drive (100%)...", reply_markup=reply_markup)
            
            # --- NUEVO: Registrar el archivo subido en la DB local ---
            if drive_id and original_file_name:
                print(f"Intentando registrar archivo subido: ID={drive_id}, Nombre={original_file_name}")
                try:
                    record_uploaded_file(drive_id, original_file_name)
                    print(f"✅ CONFIRMACIÓN: Archivo {drive_id} ('{original_file_name}') REGISTRADO en uploaded_files_db.json.")
                except Exception as record_err:
                    error_msg = f"⚠️ Error al registrar archivo en DB local después de la subida: {record_err}"
                    print(error_msg)
                    # Opcional: Enviar mensaje al usuario
                    # await safe_edit_message(processing_message, f"{processing_message.text.markdown}\n{error_msg}")

        except asyncio.CancelledError:
            raise # Relanzar para ser capturado por el handler exterior
        finally:
            # Limpiar la referencia de la tarea de subida
            if message.id in cancelable_processes:
                cancelable_processes[message.id]['process_task'] = None

        # 5. Eliminar archivo local INMEDIATAMENTE
        print("Eliminando archivo temporal local...")
        await safe_delete_file(temp_file_path)
        temp_file_path = None

        # 6. Importar a Hydrax (Sin botón de cancelar en esta etapa)
        # Eliminar el botón de cancelar antes de la importación
        await update_progress(processing_message, "🚀 Importando a Hydrax...")
        print("Importando a Hydrax...")
        hydrax_result = import_to_hydrax(drive_id)

        # 7. Mostrar resultado final
        print(f"Resultado de Hydrax: {hydrax_result}")
        if hydrax_result["success"]:
            slug = hydrax_result["slug"]
            final_message = f"✅ **Proceso completado con éxito!**\nSlug: `{slug}`"
        else:
            error_msg = hydrax_result["error"]
            final_message = f"❌ **Error al importar a Hydrax:**\n`{error_msg}`"
        
        # Eliminar el botón de cancelar del mensaje final
        await safe_edit_message(processing_message, final_message)

    except asyncio.CancelledError:
        # Manejar la cancelación del proceso
        print(f"Proceso para el mensaje {message.id} cancelado por el usuario.")
        cancel_message = "⚠️ **Proceso cancelado por el usuario.**"
        # Eliminar el botón de cancelar del mensaje de cancelación
        await safe_edit_message(processing_message, cancel_message)
        
    except Exception as e:
        print(f"Error general en el manejo del video (Message ID: {message.id}): {e}")
        import traceback
        traceback.print_exc()
        error_message = f"⚠️ **Ocurrió un error inesperado:**\n`{str(e)}`"
        
        if processing_message:
            try:
                # Eliminar el botón de cancelar del mensaje de error
                await safe_edit_message(processing_message, error_message)
            except Exception as edit_error:
                print(f"Error al editar el mensaje con el error: {edit_error}")
        
        if temp_file_path:
            await safe_delete_file(temp_file_path)
    
    finally:
        print(f"Finalizando procesamiento para Message ID: {message.id}")
        finish_processing(message.id)
        # Limpiar el proceso cancelable del diccionario
        cancelable_processes.pop(message.id, None) # Usar pop con default para evitar KeyError

# --- Manejador para CallbackQuery (para botones de /list, /listdrive, cancelar y acciones) ---
@pyrogram_app.on_callback_query()
async def callback_handler(client: Client, callback_query):
    """Maneja las solicitudes de los botones inline."""
    data = callback_query.data
    user_id = callback_query.from_user.id
    message = callback_query.message
    chat_id = message.chat.id

    print(f"Callback recibido: {data} de User ID: {user_id}")

    # --- Verificar lista blanca para acciones sensibles ---
    if not is_user_whitelisted(user_id):
         await callback_query.answer("❌ Acceso denegado.", show_alert=True)
         return

    try:
        # --- Manejar callbacks de /list (archivos subidos por el bot) ---
        if data.startswith("list_"):
            # Navegación o refresco de lista
            try:
                page = int(data.split("_")[1])
                await send_file_list(client, chat_id, page=page, message_to_edit=message)
                await callback_query.answer() # Acknowledge silently
            except ValueError:
                 await callback_query.answer("Error: Número de página inválido.", show_alert=True)

        # --- Manejar callbacks de /listdrive (contenido de Drive) ---
        elif data.startswith("drivelist_"):
             try:
                 page_str = data.split("_")[1]
                 page = int(page_str)
                 await send_drive_file_list(client, chat_id, page=page, message_to_edit=message)
                 await callback_query.answer()
             except (ValueError, IndexError):
                  await callback_query.answer("Error: Número de página inválido.", show_alert=True)
        
        # --- Manejar confirmación de borrado individual de Drive (desde comando) ---
        elif data.startswith("drive_delete_confirm_"):
             file_id = data[len("drive_delete_confirm_"):]
             await callback_query.answer("🗑️ Borrando archivo...")
             try:
                 await delete_drive_file_async(file_id)
                 await safe_edit_message(message, f"✅ Archivo con ID `{file_id}` borrado exitosamente de Google Drive.")
                 # Opcional: Refrescar la lista
                 # await asyncio.sleep(1)
                 # await send_drive_file_list(client, chat_id, page=1, message_to_edit=message)
             except Exception as e:
                 error_msg = f"❌ Error al borrar archivo: {e}"
                 print(error_msg)
                 await safe_edit_message(message, error_msg)
        
        # --- Manejar solicitud de confirmación para borrar todo de Drive ---
        elif data == "drive_delete_all_confirm":
             confirm_markup = InlineKeyboardMarkup([
                 [InlineKeyboardButton("✅ Sí, borrar TODO", callback_data="drive_delete_all_final_confirm")],
                 [InlineKeyboardButton("❌ Cancelar", callback_data="drive_cancel")]
             ])
             await safe_edit_message(
                 message,
                 "⚠️ **¿Confirmas el BORRADO MASIVO de TODOS los archivos de la unidad/carpeta de Google Drive?**\n"
                 "Esta acción no se puede deshacer.",
                 reply_markup=confirm_markup
             )
             await callback_query.answer()
        
        # --- Manejar confirmación final de borrar todo de Drive ---
        elif data == "drive_delete_all_final_confirm":
             await callback_query.answer("🗑️ Iniciando borrado masivo...")
             await safe_edit_message(message, "🗑️ Borrando todos los archivos de Google Drive...")
             try:
                 # Asume que usas 'root' para My Drive. Si usas una unidad compartida,
                 # reemplaza 'root' con el ID de tu unidad compartida.
                 await delete_all_drive_files_async(folder_id='root') # <<< AJUSTA 'root' SI ES NECESARIO
                 await safe_edit_message(message, "✅ Todos los archivos han sido borrados de Google Drive.")
                 # Opcional: Refrescar la lista
                 # await asyncio.sleep(2)
                 # await send_drive_file_list(client, chat_id, page=1, message_to_edit=message)
             except Exception as e:
                 error_msg = f"❌ Error al borrar todos los archivos: {e}"
                 print(error_msg)
                 await safe_edit_message(message, error_msg)
        
        # --- NUEVO: Manejar borrado individual desde /listdrive (botón) ---
        elif data.startswith("drive_delete_single_"):
             file_id = data[len("drive_delete_single_"):] # Extraer el ID del archivo
             # Pedir confirmación específica para este archivo
             confirm_markup = InlineKeyboardMarkup([
                 [InlineKeyboardButton("✅ Sí, borrar", callback_data=f"drive_delete_confirm_{file_id}")], # Reusar el callback existente
                 [InlineKeyboardButton("❌ Cancelar", callback_data="drive_cancel")]
             ])
             await safe_edit_message(
                 message, # El mensaje es el de la lista
                 f"⚠️ **¿Confirmas el borrado del archivo con ID `{file_id}`?**\n"
                 "Esta acción no se puede deshacer.",
                 reply_markup=confirm_markup
             )
             await callback_query.answer("Confirmando borrado...")

        # --- Manejar cancelación general ---
        elif data == "drive_cancel":
             await callback_query.answer("❌ Operación cancelada.")
             await safe_edit_message(message, "❌ Operación cancelada.")

        # --- (Resto de los manejos de callbacks existentes: delete_, cancel_, etc.) ---
        elif data.startswith("delete_"):
            # ... (tu lógica existente para borrar archivos subidos por el bot) ...
            pass
        elif data.startswith("cancel_"):
            # ... (tu lógica existente para cancelar procesos) ...
            pass
        else:
            await callback_query.answer("Comando no reconocido.", show_alert=True)

    except Exception as e:
        error_msg = f"❌ Error en callback: {e}"
        print(error_msg)
        import traceback
        traceback.print_exc()
        await callback_query.answer(error_msg, show_alert=True)


# --- Comando para configurar el menú de comandos del bot ---
# Asegurarse de que WHITELISTED_USERS sea iterable para filters.user
# Si está vacío, no aplicar el filtro de usuario (permitir a todos, aunque el comando requiere autenticación de bot)
_WHITELIST_FILTER = filters.user(list(WHITELISTED_USERS)) if WHITELISTED_USERS else filters.all

@pyrogram_app.on_message(filters.command("setmenu") & filters.private & _WHITELIST_FILTER)
async def set_menu_command(client: Client, message: Message):
    """Comando manual para establecer el menú (opcional, útil para pruebas o si el auto-set falla)."""
    await set_bot_commands(client)
    await message.reply_text("✅ Menú de comandos actualizado (si tienes permisos de admin del bot).")

# --- Punto de entrada principal ---
if __name__ == "__main__":
    print("Iniciando servidor Flask en un hilo separado...")
    # Iniciar Flask en un hilo separado para que no bloquee pyrogram_app.run()
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True # El hilo se detendrá cuando el proceso principal termine
    flask_thread.start()
    print("Servidor Flask iniciado.")

    print("Entrando en pyrogram_app.run()...")
    print("Bot deberia estar escuchando...")
    # --- Corrección para Pyrogram v2.x: Usar pyrogram_app.run() directamente ---
    # pyrogram_app.run() se encarga internamente de:
    # 1. Iniciar el cliente de Pyrogram (app.start())
    # 2. Mantener el bucle de eventos corriendo para escuchar actualizaciones
    # 3. Ejecutar los manejadores definidos (@app.on_message, etc.)
    # 4. Mantener el proceso vivo hasta que se reciba una señal de cierre (Ctrl+C)
    try:
        # pyrogram_app.run() bloquea el hilo principal hasta que se detiene.
        pyrogram_app.run()
    except KeyboardInterrupt:
        print("🛑 Bot detenido por el usuario (Ctrl+C).")
    except Exception as e:
        print(f"❌ Error fatal en pyrogram_app.run(): {e}")
        import traceback
        traceback.print_exc()
    
    print("pyrogram_app.run() ha terminado.")

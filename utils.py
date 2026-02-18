# utils.py (Versión corregida y optimizada)
import asyncio
import time
from pyrogram.errors import FloodWait, MessageNotModified

# Variable global para rastrear el último FloodWait y su espera asociada
_last_flood_wait = {"until": 0, "delay": 0}

# Variable global para almacenar el último mensaje de progreso enviado
# para evitar enviar mensajes idénticos seguidos
_last_progress_message = {}

async def _handle_flood_wait(e: FloodWait, action_name: str):
    """Maneja la lógica de espera para FloodWait de forma centralizada."""
    global _last_flood_wait
    # CORRECCION: Usar e.value en lugar de e.x
    delay = e.value
    wait_until = time.time() + delay
    
    # Si ya esperamos por un FloodWait muy cercano (misma duración o más),
    # simplemente esperamos lo restante para evitar reintentos fallidos.
    if _last_flood_wait["until"] >= time.time() and _last_flood_wait["delay"] >= delay:
        remaining_delay = _last_flood_wait["until"] - time.time()
        if remaining_delay > 0:
            print(f"Evitando FloodWait para '{action_name}': Esperando {remaining_delay:.1f}s restantes del FloodWait anterior.")
            await asyncio.sleep(remaining_delay + 1) # +1 para asegurar
            return True # Indica que se esperó por un FloodWait previo

    print(f"FloodWait detectado para '{action_name}': Esperando {delay} segundos.")
    _last_flood_wait = {"until": wait_until, "delay": delay}
    await asyncio.sleep(delay)
    return False # Indica que se esperó el FloodWait actual

# --- Funciones seguras para operaciones comunes ---

async def safe_edit_message(message, text: str, **kwargs):
    """Edita un mensaje con manejo de FloodWait y evitando mensajes idénticos."""
    global _last_progress_message
    chat_id = message.chat.id
    message_id = message.id
    key = (chat_id, message_id)

    # --- CORRECCIÓN CLAVE ---
    # Verificar si el mensaje es idéntico al último enviado
    # Incluye el texto y el reply_markup para una comparación más precisa
    # kwargs.get('reply_markup') puede ser None
    message_content = (text, kwargs.get('reply_markup'))
    if _last_progress_message.get(key) == message_content:
        # print(f"Evitando editar mensaje {message_id} en chat {chat_id}: Contenido idéntico.")
        return message # Retornar el mensaje original si no se edita

    action_name = "edit_message"
    try:
        result = await message.edit_text(text, **kwargs)
        _last_progress_message[key] = message_content # Almacenar el contenido completo enviado
        return result
    except FloodWait as e:
        waited = await _handle_flood_wait(e, action_name)
        if not waited: # Solo reintentar si no esperamos por uno previo
            try:
                result = await message.edit_text(text, **kwargs)
                _last_progress_message[key] = message_content
                return result
            except Exception as e2:
                 print(f"Error al editar mensaje después de FloodWait: {e2}")
                 raise # Relanzar si falla el reintento
        else:
            # Si esperamos por uno previo, intentar de nuevo
            result = await message.edit_text(text, **kwargs)
            _last_progress_message[key] = message_content
            return result
    except MessageNotModified:
        # Si el mensaje no se modificó, simplemente lo registramos y continuamos
        # Esto puede pasar si el texto y el markup son idénticos a los actuales
        # print(f"Aviso: Mensaje {message_id} no modificado (posiblemente idéntico).")
        _last_progress_message[key] = message_content # Marcarlo como "enviado" para evitar futuros intentos
        return message
    except Exception as e: # Capturar otros errores
         print(f"Error inesperado al editar mensaje: {e}")
         raise

async def safe_send_message(client, chat_id, text: str, **kwargs):
    """Envía un mensaje con manejo de FloodWait."""
    action_name = "send_message"
    try:
        return await client.send_message(chat_id, text, **kwargs)
    except FloodWait as e:
        waited = await _handle_flood_wait(e, action_name)
        if not waited:
            try:
                return await client.send_message(chat_id, text, **kwargs)
            except Exception as e2:
                 print(f"Error al enviar mensaje después de FloodWait: {e2}")
                 raise
        else:
             return await client.send_message(chat_id, text, **kwargs)
    except Exception as e:
         print(f"Error inesperado al enviar mensaje: {e}")
         raise

async def safe_reply_message(message, text: str, **kwargs):
    """Responde a un mensaje con manejo de FloodWait."""
    action_name = "reply_message"
    try:
        return await message.reply_text(text, **kwargs)
    except FloodWait as e:
        waited = await _handle_flood_wait(e, action_name)
        if not waited:
            try:
                return await message.reply_text(text, **kwargs)
            except Exception as e2:
                 print(f"Error al responder mensaje después de FloodWait: {e2}")
                 raise
        else:
             return await message.reply_text(text, **kwargs)
    except Exception as e:
         print(f"Error inesperado al responder mensaje: {e}")
         raise

async def safe_delete_message(message):
    """Borra un mensaje con manejo de FloodWait."""
    action_name = "delete_message"
    try:
        return await message.delete()
    except FloodWait as e:
        waited = await _handle_flood_wait(e, action_name)
        if not waited:
            try:
                 return await message.delete()
            except Exception as e2:
                 print(f"Error al borrar mensaje después de FloodWait (puede ya estar borrado): {e2}")
                 # No relanzar error al borrar, es común que falle si ya se borró
        # Si esperamos, asumimos que el mensaje ya no existe o se borrará pronto
    except Exception as e:
         print(f"Error inesperado al borrar mensaje (puede ya estar borrado): {e}")
         # No relanzar error al borrar

async def safe_delete_file(file_path: str):
    """Elimina un archivo de forma segura."""
    import os
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"Archivo eliminado: {file_path}")
    except Exception as e:
        print(f"Error al eliminar archivo {file_path}: {e}")

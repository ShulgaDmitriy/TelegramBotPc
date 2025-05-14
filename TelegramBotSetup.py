import datetime
import os
import json
import subprocess
import tkinter as tk
import threading
import psutil
import platform
import shutil
import logging
from pydub import AudioSegment
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram import ReplyKeyboardMarkup
import pyautogui
from pathlib import Path
import telegram.error
import asyncio

# Настройка логирования
USER_HOME = os.path.expanduser("~")
TMP_DIR = os.path.join(USER_HOME, "telegrambottmp")
LOG_FILE = os.path.join(TMP_DIR, "bot_errors.log")
os.makedirs(TMP_DIR, exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.ERROR,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Определяем пути
CONFIG_FILE = os.path.join(TMP_DIR, "config.json")
SCRIPT_NAME = "bot.pyw"
SCRIPT_PATH = os.path.join(TMP_DIR, SCRIPT_NAME)

# Проверяем и завершаем старые процессы
def terminate_old_instances():
    """Завершает старые экземпляры скрипта."""
    current_pid = os.getpid()
    script_name_lower = SCRIPT_NAME.lower()
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.pid == current_pid:
                continue
            if proc.name().lower() in ["python.exe", "pythonw.exe"]:
                cmdline = proc.cmdline()
                if cmdline and any(script_name_lower in arg.lower() for arg in cmdline):
                    proc.terminate()
                    proc.wait(timeout=3)
                    print(f"Завершён процесс PID {proc.pid}: {cmdline}")
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

# Проверяем наличие конфигурации
def load_config():
    """Загружает конфигурацию из файла."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return None

# Сохраняем конфигурацию
def save_config(token, user_id):
    """Сохраняет конфигурацию в файл."""
    config = {"TOKEN": token, "AUTHORIZED_USER_ID": int(user_id)}
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f)
    return config

# Окно для первоначальной настройки
def setup_window():
    """Открывает окно для ввода токена и ID пользователя."""
    root = tk.Tk()
    root.title("Настройка Telegram бота")
    root.geometry("400x200")
    root.resizable(False, False)

    tk.Label(root, text="Введите токен бота:").pack(pady=10)
    token_entry = tk.Entry(root, width=50)
    token_entry.pack()

    tk.Label(root, text="Введите авторизованный ID пользователя:").pack(pady=10)
    user_id_entry = tk.Entry(root, width=50)
    user_id_entry.pack()

    def on_submit():
        token = token_entry.get().strip()
        user_id = user_id_entry.get().strip()
        if token and user_id:
            try:
                int(user_id)
                save_config(token, user_id)
                root.destroy()
            except ValueError:
                tk.Label(root, text="ID должен быть числом!", fg="red").pack()

    tk.Button(root, text="Сохранить", command=on_submit).pack(pady=20)
    root.mainloop()

# Настройка автозагрузки
def setup_autostart():
    """Настраивает автозагрузку бота через VBS-скрипт и ярлык."""
    current_script = os.path.abspath(__file__)
    if current_script != SCRIPT_PATH:
        shutil.copy(current_script, SCRIPT_PATH)

    vbs_script = os.path.join(TMP_DIR, "run_bot.vbs")
    # Формируем содержимое VBScript с правильной экранировкой
    vbs_content = f'Set WShell = CreateObject("WScript.Shell")\nWShell.Run "pythonw.exe ""{SCRIPT_PATH}""", 0'
    with open(vbs_script, "w", encoding="ansi") as f:
        f.write(vbs_content.strip())

    startup_dir = os.path.join(
        USER_HOME, "AppData", "Roaming", "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
    )
    shortcut_path = os.path.join(startup_dir, "TelegramBot.lnk")
    
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(shortcut_path)
        shortcut.TargetPath = vbs_script
        shortcut.WorkingDirectory = TMP_DIR
        shortcut.save()
    except Exception as e:
        logging.error(f"Ошибка создания ярлыка: {e}")

# Загружаем конфигурацию или запрашиваем
config = load_config()
if not config:
    setup_window()
    config = load_config()
    if config:
        setup_autostart()

if not config:
    raise SystemExit("Конфигурация не создана. Запустите скрипт снова.")

TOKEN = config["TOKEN"]
AUTHORIZED_USER_ID = config["AUTHORIZED_USER_ID"]

# Путь для сохранения аудиофайла
last_audio_path = None

# Флаг для режима ожидания текста после /msg
waiting_for_msg = False

# Флаг для определения текущего действия в confirm_shutdown
current_action = None

# Создание клавиатур
main_keyboard = [["Power", "Screenshot","Status", "Fun"]]
main_reply_markup = ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True)

confirm_keyboard = [["Подтвердить", "Отменить"]]
confirm_reply_markup = ReplyKeyboardMarkup(confirm_keyboard, resize_keyboard=True)

audio_keyboard = [["Play", "Назад"]]
audio_reply_markup = ReplyKeyboardMarkup(audio_keyboard, resize_keyboard=True)

fun_keyboard = [["Msg", "Назад"]]
fun_reply_markup = ReplyKeyboardMarkup(fun_keyboard, resize_keyboard=True)

power_keyboard = [["PowerOff", "Reboot", "Lock", "Назад"]]
power_reply_markup = ReplyKeyboardMarkup(power_keyboard, resize_keyboard=True)

async def clear_chat(context):
    """Очищает чат для авторизованного пользователя."""
    chat_id = AUTHORIZED_USER_ID
    try:
        # Проверяем права бота (если группа)
        try:
            chat = await asyncio.wait_for(context.bot.get_chat(chat_id), timeout=5.0)
            if chat.type in ["group", "supergroup"]:
                member = await asyncio.wait_for(
                    context.bot.get_chat_member(chat_id, context.bot.id), timeout=5.0
                )
                if not member.can_delete_messages:
                    logging.warning("Бот не имеет прав на удаление сообщений в группе")
                    return
        except asyncio.TimeoutError:
            logging.error("Таймаут при проверке прав бота")
            return
        except Exception as e:
            logging.error(f"Ошибка проверки прав бота: {e}")
            return

        # Отправляем тестовое сообщение
        try:
            message = await asyncio.wait_for(
                context.bot.send_message(chat_id=chat_id, text="Очистка чата..."), timeout=5.0
            )
            current_message_id = message.message_id
        except asyncio.TimeoutError:
            logging.error("Таймаут при отправке тестового сообщения")
            return
        except Exception as e:
            logging.error(f"Ошибка отправки тестового сообщения: {e}")
            return

        # Удаляем тестовое сообщение
        try:
            await asyncio.wait_for(
                context.bot.delete_message(chat_id=chat_id, message_id=current_message_id), timeout=5.0
            )
        except asyncio.TimeoutError:
            logging.error("Таймаут при удалении тестового сообщения")
        except Exception as e:
            logging.error(f"Ошибка удаления тестового сообщения: {e}")

        # Удаляем до 50 предыдущих сообщений
        for message_id in range(current_message_id - 1, current_message_id - 51, -1):
            try:
                await asyncio.wait_for(
                    context.bot.delete_message(chat_id=chat_id, message_id=message_id), timeout=5.0
                )
            except telegram.error.BadRequest as e:
                if "Message can't be deleted" in str(e) or "Message to delete not found" in str(e):
                    continue
                logging.error(f"Ошибка удаления сообщения {message_id}: {e}")
            except asyncio.TimeoutError:
                logging.error(f"Таймаут при удалении сообщения {message_id}")
            except Exception as e:
                logging.error(f"Ошибка удаления сообщения {message_id}: {e}")

    except Exception as e:
        logging.error(f"Общая ошибка очистки чата: {e}")

async def clear_command(update, context):
    """Ручная очистка чата по команде /clear."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if user_id != AUTHORIZED_USER_ID:
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="А хуй пососать не хочешь?"
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
        return

    await clear_chat(context)
    await delete_previous_bot_message(context, chat_id)
    message = await context.bot.send_message(
        chat_id=chat_id,
        text="Чат очищен! Отправь голосовое сообщение или выбери команду.",
        reply_markup=main_reply_markup
    )
    context.user_data["last_bot_message_id"] = message.message_id
    context.user_data["bot_message_job"] = await schedule_message_deletion(
        context, chat_id, message.message_id, "bot_message_job"
    )

async def delete_previous_bot_message(context, chat_id):
    """Удаляет предыдущее сообщение бота и отменяет его задачу автоудаления."""
    if "last_bot_message_id" in context.user_data:
        if "bot_message_job" in context.user_data:
            context.user_data["bot_message_job"].schedule_removal()
            del context.user_data["bot_message_job"]
        try:
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=context.user_data["last_bot_message_id"]
            )
        except Exception:
            pass
        del context.user_data["last_bot_message_id"]

async def delete_previous_user_message(context, chat_id):
    """Удаляет предыдущее сообщение пользователя и отменяет его задачу автоудаления."""
    if "last_user_message_id" in context.user_data:
        if "user_message_job" in context.user_data:
            context.user_data["user_message_job"].schedule_removal()
            del context.user_data["user_message_job"]
        try:
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=context.user_data["last_user_message_id"]
            )
        except Exception:
            pass
        del context.user_data["last_user_message_id"]

async def schedule_message_deletion(context, chat_id, message_id, job_key):
    """Планирует удаление сообщения через 10 минут."""
    async def delete_message(context):
        try:
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=message_id
            )
        except Exception:
            pass
        if job_key == "bot_message_job" and context.user_data.get("last_bot_message_id") == message_id:
            del context.user_data["last_bot_message_id"]
            if "bot_message_job" in context.user_data:
                del context.user_data["bot_message_job"]
        elif job_key == "user_message_job" and context.user_data.get("last_user_message_id") == message_id:
            del context.user_data["last_user_message_id"]
            if "user_message_job" in context.user_data:
                del context.user_data["user_message_job"]

    job = context.job_queue.run_once(delete_message, 600, data={"chat_id": chat_id, "message_id": message_id})
    return job

async def start(update, context):
    """Запускает бота и отправляет приветственное сообщение."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if user_id == AUTHORIZED_USER_ID:
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="Привет! Отправь голосовое сообщение или выбери команду.",
            reply_markup=main_reply_markup
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
    else:
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="А хуй пососать не хочешь?"
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )

async def msg_command(update, context):
    """Обрабатывает команду /msg для ввода текста."""
    global waiting_for_msg
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if user_id != AUTHORIZED_USER_ID:
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="А хуй пососать не хочешь?"
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
        return
    
    waiting_for_msg = True
    await delete_previous_bot_message(context, chat_id)
    message = await context.bot.send_message(
        chat_id=chat_id,
        text="Введите текст для отображения.",
        reply_markup=main_reply_markup
    )
    context.user_data["last_bot_message_id"] = message.message_id
    context.user_data["bot_message_job"] = await schedule_message_deletion(
        context, chat_id, message.message_id, "bot_message_job"
    )

async def play_audio(update, context):
    """Воспроизводит последнее сохранённое аудио."""
    global last_audio_path
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if user_id != AUTHORIZED_USER_ID:
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="А хуй пососать не хочешь?"
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
        return
    
    if last_audio_path is None or not os.path.exists(last_audio_path):
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="Аудиофайл не найден. Отправьте голосовое сообщение.",
            reply_markup=main_reply_markup
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
        return
    
    try:
        audio = AudioSegment.from_ogg(last_audio_path)
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="Воспроизведение начато..."
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
        
        subprocess.run(["ffplay", "-nodisp", "-autoexit", last_audio_path], check=True)
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="Воспроизведение закончено.",
            reply_markup=audio_reply_markup
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
    except FileNotFoundError:
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="Ошибка: ffplay не найден. Убедитесь, что FFmpeg установлен и добавлен в PATH.",
            reply_markup=main_reply_markup
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
    except subprocess.CalledProcessError as e:
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text=f"Ошибка ffplay: {e}",
            reply_markup=main_reply_markup
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
    except Exception as e:
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text=f"Ошибка воспроизведения: {e}",
            reply_markup=main_reply_markup
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )

async def handle_audio(update, context):
    """Обрабатывает входящее голосовое сообщение."""
    global last_audio_path
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    context.user_data["last_user_message_id"] = update.message.message_id
    context.user_data["user_message_job"] = await schedule_message_deletion(
        context, chat_id, update.message.message_id, "user_message_job"
    )
    
    if user_id != AUTHORIZED_USER_ID:
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="А хуй пососать не хочешь?"
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
        return
    
    voice = update.message.voice
    if voice:
        try:
            if last_audio_path and os.path.exists(last_audio_path):
                os.remove(last_audio_path)
            
            last_audio_path = os.path.join(TMP_DIR, "sound.ogg")
            file = await voice.get_file()
            await file.download_to_drive(last_audio_path)
            if os.path.exists(last_audio_path):
                await delete_previous_bot_message(context, chat_id)
                message = await context.bot.send_message(
                    chat_id=chat_id,
                    text="Аудио сохранено.",
                    reply_markup=audio_reply_markup
                )
                context.user_data["last_bot_message_id"] = message.message_id
                context.user_data["bot_message_job"] = await schedule_message_deletion(
                    context, chat_id, message.message_id, "bot_message_job"
                )
            else:
                await delete_previous_bot_message(context, chat_id)
                message = await context.bot.send_message(
                    chat_id=chat_id,
                    text="Ошибка: файл не сохранён.",
                    reply_markup=main_reply_markup
                )
                context.user_data["last_bot_message_id"] = message.message_id
                context.user_data["bot_message_job"] = await schedule_message_deletion(
                    context, chat_id, message.message_id, "bot_message_job"
                )
        except Exception as e:
            await delete_previous_bot_message(context, chat_id)
            message = await context.bot.send_message(
                chat_id=chat_id,
                text=f"Ошибка сохранения: {e}",
                reply_markup=main_reply_markup
            )
            context.user_data["last_bot_message_id"] = message.message_id
            context.user_data["bot_message_job"] = await schedule_message_deletion(
                context, chat_id, message.message_id, "bot_message_job"
            )

async def poweroff(update, context):
    """Инициирует выключение компьютера."""
    global current_action
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if user_id == AUTHORIZED_USER_ID:
        current_action = "poweroff"
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="Выключить компьютер?",
            reply_markup=confirm_reply_markup
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
    else:
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="А хуй пососать не хочешь?"
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )

async def reboot(update, context):
    """Инициирует перезагрузку компьютера."""
    global current_action
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if user_id == AUTHORIZED_USER_ID:
        current_action = "reboot"
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="Перезагрузить компьютер?",
            reply_markup=confirm_reply_markup
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
    else:
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="А хуй пососать не хочешь?"
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )

async def lock(update, context):
    """Инициирует блокировку экрана."""
    global current_action
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if user_id == AUTHORIZED_USER_ID:
        current_action = "lock"
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="Заблокировать экран?",
            reply_markup=confirm_reply_markup
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
    else:
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="А хуй пососать не хочешь?"
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )

async def screenshot(update, context):
    """Делает скриншот рабочего стола."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if user_id == AUTHORIZED_USER_ID:
        try:
            screenshot_path = os.path.join(TMP_DIR, "screenshot.png")
            pyautogui.screenshot(screenshot_path)
            with open(screenshot_path, 'rb') as photo:
                await delete_previous_bot_message(context, chat_id)
                message = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption="Скриншот рабочего стола",
                    reply_markup=main_reply_markup
                )
                context.user_data["last_bot_message_id"] = message.message_id
                context.user_data["bot_message_job"] = await schedule_message_deletion(
                    context, chat_id, message.message_id, "bot_message_job"
                )
            os.remove(screenshot_path)
        except Exception as e:
            await delete_previous_bot_message(context, chat_id)
            message = await context.bot.send_message(
                chat_id=chat_id,
                text=f"Ошибка создания скриншота: {e}",
                reply_markup=main_reply_markup
            )
            context.user_data["last_bot_message_id"] = message.message_id
            context.user_data["bot_message_job"] = await schedule_message_deletion(
                context, chat_id, message.message_id, "bot_message_job"
            )
    else:
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="А хуй пососать не хочешь?"
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )

async def confirm_shutdown(update, context):
    """Подтверждает действие выключения, перезагрузки или блокировки."""
    global current_action
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if user_id != AUTHORIZED_USER_ID:
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="А хуй пососать не хочешь?"
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
        return
    
    if current_action == "poweroff":
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="Выключение компьютера...",
            reply_markup=power_reply_markup
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
        subprocess.run(["shutdown", "/s", "/t", "0"])
    elif current_action == "reboot":
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="Перезагрузка компьютера...",
            reply_markup=power_reply_markup
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
        subprocess.run(["shutdown", "/r", "/t", "0"])
    elif current_action == "lock":
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="Блокировка экрана...",
            reply_markup=power_reply_markup
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
        subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"])
    current_action = None

async def cancel_shutdown(update, context):
    """Отменяет действие выключения, перезагрузки или блокировки."""
    global current_action
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if user_id == AUTHORIZED_USER_ID:
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="Действие отменено.",
            reply_markup=power_reply_markup
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
        current_action = None
    else:
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="А хуй пососать не хочешь?"
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )

async def status(update, context):
    """Отправляет статус компьютера (время работы, загрузка CPU, память, ОС)."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if user_id != AUTHORIZED_USER_ID:
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="А хуй пососать не хочешь?"
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
        return
    
    try:
        boot_time = psutil.boot_time()
        uptime = datetime.datetime.now().timestamp() - boot_time
        hours, remainder = divmod(int(uptime), 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours} ч {minutes} мин {seconds} сек"
        
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        memory_used = memory.used / (1024 ** 3)
        memory_total = memory.total / (1024 ** 3)
        os_version = platform.system() + " " + platform.release()
        
        status_message = (
            f"Статус компьютера:\n"
            f"Время работы: {uptime_str}\n"
            f"Загрузка процессора: {cpu_percent}%\n"
            f"Оперативная память: {memory_used:.1f} ГБ / {memory_total:.1f} ГБ\n"
            f"ОС: {os_version}"
        )
        
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text=status_message,
            reply_markup=main_reply_markup
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
    except Exception as e:
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text=f"Ошибка получения статуса: {e}",
            reply_markup=main_reply_markup
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )

def show_message_window(text):
    """Отображает текстовое сообщение в окне."""
    try:
        root = tk.Tk()
        root.withdraw()
        top = tk.Toplevel(root)
        top.attributes('-topmost', True)
        top.focus_force()
        top.title("Сообщение")
        label = tk.Label(top, text=text, wraplength=400, padx=20, pady=20)
        label.pack()
        button = tk.Button(top, text="Иди нахуй", command=lambda: [top.destroy(), root.destroy()])
        button.pack(pady=10)
        top.update_idletasks()
        width = top.winfo_width()
        height = top.winfo_height()
        x = (top.winfo_screenwidth() // 2) - (width // 2)
        y = (top.winfo_screenheight() // 2) - (height // 2)
        top.geometry(f"+{x}+{y}")
        root.mainloop()
    except Exception as e:
        print(f"Ошибка в show_message_window: {e}")

async def handle_message(update, context):
    """Обрабатывает текстовые сообщения и кнопки."""
    global waiting_for_msg, current_action
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = update.message.text
    context.user_data["last_user_message_id"] = update.message.message_id
    context.user_data["user_message_job"] = await schedule_message_deletion(
        context, chat_id, update.message.message_id, "user_message_job"
    )
    
    if user_id != AUTHORIZED_USER_ID:
        await delete_previous_bot_message(context, chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="А хуй пососать не хочешь?"
        )
        context.user_data["last_bot_message_id"] = message.message_id
        context.user_data["bot_message_job"] = await schedule_message_deletion(
            context, chat_id, message.message_id, "bot_message_job"
        )
        return
    
    await delete_previous_user_message(context, chat_id)
    context.user_data["last_user_message_id"] = update.message.message_id
    context.user_data["user_message_job"] = await schedule_message_deletion(
        context, chat_id, update.message.message_id, "user_message_job"
    )
    
    if waiting_for_msg:
        try:
            threading.Thread(target=show_message_window, args=(text,), daemon=True).start()
            await delete_previous_bot_message(context, chat_id)
            message = await context.bot.send_message(
                chat_id=chat_id,
                text="Текст отображён на экране.",
                reply_markup=main_reply_markup
            )
            context.user_data["last_bot_message_id"] = message.message_id
            context.user_data["bot_message_job"] = await schedule_message_deletion(
                context, chat_id, message.message_id, "bot_message_job"
            )
        except Exception as e:
            await delete_previous_bot_message(context, chat_id)
            message = await context.bot.send_message(
                chat_id=chat_id,
                text=f"Ошибка отображения: {e}",
                reply_markup=main_reply_markup
            )
            context.user_data["last_bot_message_id"] = message.message_id
            context.user_data["bot_message_job"] = await schedule_message_deletion(
                context, chat_id, message.message_id, "bot_message_job"
            )
        finally:
            waiting_for_msg = False
    else:
        if text == "Power":
            await delete_previous_bot_message(context, chat_id)
            message = await context.bot.send_message(
                chat_id=chat_id,
                text="Выберите действие:",
                reply_markup=power_reply_markup
            )
            context.user_data["last_bot_message_id"] = message.message_id
            context.user_data["bot_message_job"] = await schedule_message_deletion(
                context, chat_id, message.message_id, "bot_message_job"
            )
        elif text == "PowerOff":
            await poweroff(update, context)
        elif text == "Reboot":
            await reboot(update, context)
        elif text == "Lock":
            await lock(update, context)
        elif text == "Screenshot":
            await screenshot(update, context)
        elif text == "Fun":
            await delete_previous_bot_message(context, chat_id)
            message = await context.bot.send_message(
                chat_id=chat_id,
                text="Выберите действие:",
                reply_markup=fun_reply_markup
            )
            context.user_data["last_bot_message_id"] = message.message_id
            context.user_data["bot_message_job"] = await schedule_message_deletion(
                context, chat_id, message.message_id, "bot_message_job"
            )
        elif text == "Status":
            await status(update, context)
        elif text == "Msg":
            await msg_command(update, context)
        elif text == "Play":
            await play_audio(update, context)
        elif text == "Подтвердить":
            await confirm_shutdown(update, context)
        elif text == "Отменить":
            await cancel_shutdown(update, context)
        elif text == "Назад":
            await delete_previous_bot_message(context, chat_id)
            message = await context.bot.send_message(
                chat_id=chat_id,
                text="Возврат в главное меню.",
                reply_markup=main_reply_markup
            )
            context.user_data["last_bot_message_id"] = message.message_id
            context.user_data["bot_message_job"] = await schedule_message_deletion(
                context, chat_id, message.message_id, "bot_message_job"
            )
        else:
            await delete_previous_bot_message(context, chat_id)
            message = await context.bot.send_message(
                chat_id=chat_id,
                text="Неизвестная команда. Используйте кнопки.",
                reply_markup=main_reply_markup
            )
            context.user_data["last_bot_message_id"] = message.message_id
            context.user_data["bot_message_job"] = await schedule_message_deletion(
                context, chat_id, message.message_id, "bot_message_job"
            )

async def error_handler(update, context):
    """Обрабатывает ошибки, включая Conflict."""
    error = context.error
    logging.error(f"Ошибка: {error}")
    if isinstance(error, telegram.error.Conflict):
        try:
            await context.bot.send_message(
                chat_id=AUTHORIZED_USER_ID,
                text="Обнаружен конфликт: запущен другой экземпляр бота. Перезапускаю..."
            )
            context.application.stop_running()
            context.job_queue.run_once(
                lambda ctx: ctx.application.run_polling(),
                5,
                data={}
            )
        except Exception as e:
            logging.error(f"Ошибка при обработке Conflict: {e}")
    else:
        try:
            await context.bot.send_message(
                chat_id=AUTHORIZED_USER_ID,
                text=f"Произошла ошибка: {error}"
            )
        except Exception as e:
            logging.error(f"Ошибка при отправке сообщения об ошибке: {e}")

async def schedule_clear_chat(application):
    """Планирует очистку чата через 5 секунд после запуска."""
    await asyncio.sleep(5)
    await clear_chat(application)

async def post_init(application):
    """Вызывается после инициализации приложения для планирования clear_chat."""
    application.job_queue.run_once(
        lambda ctx: schedule_clear_chat(application), 0, data={}
    )

def main():
    """Запускает основной цикл бота."""
    terminate_old_instances()
    
    application = Application.builder().token(TOKEN).post_init(post_init).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("msg", msg_command))
    application.add_handler(CommandHandler("play", play_audio))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(MessageHandler(filters.VOICE, handle_audio))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    
    try:
        application.run_polling()
    except Exception as e:
        logging.error(f"Ошибка при запуске polling: {e}")
        raise

if __name__ == "__main__":
    main()
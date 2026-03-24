import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import asyncio
import csv
import os
import sqlite3
import logging

def run_web_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running")

    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

logging.basicConfig(level=logging.INFO)

# =========================
# CONFIG
# =========================
import os
BOT_TOKEN = "8522189329:AAECANmtq1bNYMHMttO7-uVWLhlKF2yEFg8"

ADMIN_ID = 0  # 8522189329

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "db.sqlite3")
CSV_PATH = os.path.join(BASE_DIR, "program.csv")


# =========================
# FSM
# =========================
class UploadState(StatesGroup):
    waiting_for_material = State()


# =========================
# DB
# =========================
def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section TEXT NOT NULL,
            title TEXT NOT NULL,
            speaker TEXT,
            date TEXT,
            time_start TEXT,
            time_end TEXT,
            room TEXT,
            description TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            user_id INTEGER,
            username TEXT,
            full_name TEXT,
            file_type TEXT NOT NULL,
            file_id TEXT NOT NULL,
            file_name TEXT,
            caption TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            FOREIGN KEY(event_id) REFERENCES events(id)
        )
    """)

    conn.commit()
    conn.close()


def clear_program_tables():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM sections")
    cur.execute("DELETE FROM events")
    conn.commit()
    conn.close()


def import_csv():
    if not os.path.exists(CSV_PATH):
        raise FileNotFoundError(f"Не найден файл program.csv: {CSV_PATH}")

    clear_program_tables()

    conn = get_conn()
    cur = conn.cursor()

    sections = set()

    with open(CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
        # У тебя CSV с ;, оставляем так
        reader = csv.DictReader(f, delimiter=';')
        print("Колонки в CSV:", reader.fieldnames)

        for row in reader:
            section = (row.get("section") or "").strip()
            title = (row.get("title") or "").strip()

            if not section or not title:
                continue

            sections.add(section)

            cur.execute("""
                INSERT INTO events (
                    section, title, speaker, date, time_start, time_end, room, description
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                section,
                title,
                (row.get("speaker") or "").strip(),
                (row.get("date") or "").strip(),
                (row.get("time_start") or "").strip(),
                (row.get("time_end") or "").strip(),
                (row.get("room") or "").strip(),
                (row.get("description") or "").strip(),
            ))

    for section in sorted(sections):
        cur.execute("INSERT OR IGNORE INTO sections (name) VALUES (?)", (section,))

    conn.commit()
    conn.close()


# =========================
# HELPERS
# =========================
def get_sections():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sections ORDER BY name")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_events_by_section(section: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, title, speaker, date, time_start, time_end, room
        FROM events
        WHERE section = ?
        ORDER BY date, time_start, title
    """, (section,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_event(event_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, section, title, speaker, date, time_start, time_end, room, description
        FROM events
        WHERE id = ?
    """, (event_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_approved_materials(event_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, file_type, file_id, file_name, caption
        FROM materials
        WHERE event_id = ? AND status = 'approved'
        ORDER BY id
    """, (event_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def save_material(event_id: int, user: types.User, file_type: str, file_id: str, file_name: str = "", caption: str = ""):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO materials (
            event_id, user_id, username, full_name, file_type, file_id, file_name, caption, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
    """, (
        event_id,
        user.id,
        user.username or "",
        user.full_name or "",
        file_type,
        file_id,
        file_name or "",
        caption or "",
    ))
    material_id = cur.lastrowid
    conn.commit()
    conn.close()
    return material_id


def approve_material(material_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE materials SET status = 'approved' WHERE id = ?", (material_id,))
    conn.commit()
    conn.close()


def reject_material(material_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE materials SET status = 'rejected' WHERE id = ?", (material_id,))
    conn.commit()
    conn.close()


# =========================
# KEYBOARDS
# =========================
def sections_kb():
    builder = InlineKeyboardBuilder()
    for s in get_sections():
        builder.button(text=s[0], callback_data=f"section:{s[0]}")
    builder.adjust(1)
    return builder.as_markup()


def events_kb(section: str):
    builder = InlineKeyboardBuilder()
    for event_id, title, speaker, date, t1, t2, room in get_events_by_section(section):
        time_label = f"{t1}-{t2}" if t1 or t2 else "Без времени"
        label = f"{time_label} | {title}"
        if len(label) > 60:
            label = label[:57] + "..."
        builder.button(text=label, callback_data=f"event:{event_id}")
    builder.button(text="← К разделам", callback_data="back:sections")
    builder.adjust(1)
    return builder.as_markup()


def event_kb(event_id: int, section: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="📎 Материалы", callback_data=f"materials:{event_id}")
    builder.button(text="⬆️ Загрузить материалы", callback_data=f"upload:{event_id}")
    builder.button(text="← Назад", callback_data=f"section:{section}")
    builder.adjust(1)
    return builder.as_markup()


def moderation_kb(material_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Одобрить", callback_data=f"approve:{material_id}")
    builder.button(text="❌ Отклонить", callback_data=f"reject:{material_id}")
    builder.adjust(2)
    return builder.as_markup()


# =========================
# HANDLERS
# =========================
async def start_handler(message: types.Message):
    await message.answer(
        "📍 Конференция МГИ\n\nВыбери раздел программы:",
        reply_markup=sections_kb()
    )


async def help_handler(message: types.Message):
    await message.answer(
        "Как пользоваться:\n\n"
        "1. Нажми /start\n"
        "2. Выбери раздел\n"
        "3. Выбери событие\n"
        "4. Смотри материалы или загружай свои\n\n"
        "Форматы файлов: PDF, PPTX, DOCX, JPG, PNG"
    )


async def myid_handler(message: types.Message):
    await message.answer(f"Твой Telegram ID: {message.from_user.id}")


async def section_handler(callback: types.CallbackQuery):
    section = callback.data.split(":", 1)[1]

    await callback.message.edit_text(
        f"📂 {section}\n\nВыбери событие:",
        reply_markup=events_kb(section)
    )
    await callback.answer()


async def back_sections_handler(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "📍 Выбери раздел программы:",
        reply_markup=sections_kb()
    )
    await callback.answer()


async def event_handler(callback: types.CallbackQuery):
    event_id = int(callback.data.split(":", 1)[1])
    event = get_event(event_id)

    if not event:
        await callback.answer("Событие не найдено", show_alert=True)
        return

    _, section, title, speaker, date, t1, t2, room, description = event

    text = (
        f"🎤 {title}\n\n"
        f"👤 {speaker or '—'}\n"
        f"📅 {date or '—'}\n"
        f"⏰ {(t1 or '—')} – {(t2 or '—')}\n"
        f"📍 Кабинет: {room or '—'}\n"
    )

    if description:
        text += f"\n{description}"

    await callback.message.edit_text(
        text,
        reply_markup=event_kb(event_id, section)
    )
    await callback.answer()


async def materials_handler(callback: types.CallbackQuery):
    event_id = int(callback.data.split(":", 1)[1])
    event = get_event(event_id)

    if not event:
        await callback.answer("Событие не найдено", show_alert=True)
        return

    title = event[2]
    materials = get_approved_materials(event_id)

    if not materials:
        await callback.message.answer(f"К событию «{title}» пока нет одобренных материалов.")
        await callback.answer()
        return

    await callback.message.answer(f"📎 Материалы к событию: {title}")

    for _, file_type, file_id, file_name, caption in materials:
        text_caption = caption or file_name or ""
        if file_type == "document":
            await callback.message.answer_document(document=file_id, caption=text_caption)
        elif file_type == "photo":
            await callback.message.answer_photo(photo=file_id, caption=text_caption)
        else:
            await callback.message.answer_document(document=file_id, caption=text_caption)

    await callback.answer()


async def upload_start_handler(callback: types.CallbackQuery, state: FSMContext):
    event_id = int(callback.data.split(":", 1)[1])
    event = get_event(event_id)

    if not event:
        await callback.answer("Событие не найдено", show_alert=True)
        return

    await state.set_state(UploadState.waiting_for_material)
    await state.update_data(event_id=event_id)

    await callback.message.answer(
        f"⬆️ Отправь файл для события:\n\n"
        f"{event[2]}\n\n"
        f"Поддерживается: PDF, DOCX, PPTX, JPG, PNG.\n"
        f"Можно отправить документ или фото.\n\n"
        f"Материал уйдёт на модерацию."
    )
    await callback.answer()


async def receive_document(message: types.Message, state: FSMContext, bot: Bot):
    current_state = await state.get_state()
    if current_state != UploadState.waiting_for_material:
        return

    data = await state.get_data()
    event_id = data.get("event_id")
    event = get_event(event_id)

    if not event:
        await message.answer("Событие не найдено.")
        await state.clear()
        return

    material_id = save_material(
        event_id=event_id,
        user=message.from_user,
        file_type="document",
        file_id=message.document.file_id,
        file_name=message.document.file_name or "",
        caption=message.caption or ""
    )

    title = event[2]
    sender = message.from_user.full_name
    username = f"@{message.from_user.username}" if message.from_user.username else "без username"

    if ADMIN_ID != 0:
        await bot.send_document(
            ADMIN_ID,
            document=message.document.file_id,
            caption=(
                f"Новый материал на модерацию\n\n"
                f"Событие: {title}\n"
                f"От: {sender} ({username})\n"
                f"Файл: {message.document.file_name or 'без имени'}\n"
                f"ID материала: {material_id}"
            ),
            reply_markup=moderation_kb(material_id)
        )

    await message.answer("Материал получен и отправлен на модерацию.")
    await state.clear()


async def receive_photo(message: types.Message, state: FSMContext, bot: Bot):
    current_state = await state.get_state()
    if current_state != UploadState.waiting_for_material:
        return

    data = await state.get_data()
    event_id = data.get("event_id")
    event = get_event(event_id)

    if not event:
        await message.answer("Событие не найдено.")
        await state.clear()
        return

    photo = message.photo[-1]
    material_id = save_material(
        event_id=event_id,
        user=message.from_user,
        file_type="photo",
        file_id=photo.file_id,
        file_name="photo.jpg",
        caption=message.caption or ""
    )

    title = event[2]
    sender = message.from_user.full_name
    username = f"@{message.from_user.username}" if message.from_user.username else "без username"

    if ADMIN_ID != 0:
        await bot.send_photo(
            ADMIN_ID,
            photo=photo.file_id,
            caption=(
                f"Новый материал на модерацию\n\n"
                f"Событие: {title}\n"
                f"От: {sender} ({username})\n"
                f"Файл: photo.jpg\n"
                f"ID материала: {material_id}"
            ),
            reply_markup=moderation_kb(material_id)
        )

    await message.answer("Фото получено и отправлено на модерацию.")
    await state.clear()


async def approve_handler(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return

    material_id = int(callback.data.split(":", 1)[1])
    approve_material(material_id)
    await callback.message.answer(f"Материал {material_id} одобрен.")
    await callback.answer("Одобрено")


async def reject_handler(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return

    material_id = int(callback.data.split(":", 1)[1])
    reject_material(material_id)
    await callback.message.answer(f"Материал {material_id} отклонён.")
    await callback.answer("Отклонено")


# =========================
# MAIN
# =========================
async def main():
    print("Запуск бота...")
    threading.Thread(target=run_web_server).start()
    init_db()
    import_csv()

    bot = Bot(token=BOT_TOKEN)
    me = await bot.get_me()
    print(f"Бот авторизован: @{me.username}")

    dp = Dispatcher()

    dp.message.register(start_handler, Command("start"))
    dp.message.register(help_handler, Command("help"))
    dp.message.register(myid_handler, Command("myid"))

    dp.callback_query.register(section_handler, F.data.startswith("section:"))
    dp.callback_query.register(back_sections_handler, F.data == "back:sections")
    dp.callback_query.register(event_handler, F.data.startswith("event:"))
    dp.callback_query.register(materials_handler, F.data.startswith("materials:"))
    dp.callback_query.register(upload_start_handler, F.data.startswith("upload:"))
    dp.callback_query.register(approve_handler, F.data.startswith("approve:"))
    dp.callback_query.register(reject_handler, F.data.startswith("reject:"))

    dp.message.register(receive_document, F.document)
    dp.message.register(receive_photo, F.photo)

    print("Polling started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

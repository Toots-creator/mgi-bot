import asyncio
import csv
import hashlib
import logging
import os
import sqlite3
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO)

# =========================
# CONFIG
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "db.sqlite3")
CSV_PATH = os.path.join(BASE_DIR, "program.csv")


# =========================
# RENDER WEB STUB
# =========================
def run_web_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running")

        def do_HEAD(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, format, *args):
            return

    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


# =========================
# FSM
# =========================
class UploadState(StatesGroup):
    waiting_for_material = State()


# =========================
# DB
# =========================
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
            event_key TEXT UNIQUE NOT NULL,
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
            event_key TEXT NOT NULL,
            user_id INTEGER,
            username TEXT,
            full_name TEXT,
            file_type TEXT NOT NULL,
            file_id TEXT NOT NULL,
            file_name TEXT,
            caption TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
        )
    """)

    conn.commit()
    conn.close()


def make_event_key(section: str, title: str, speaker: str, date: str, time_start: str, time_end: str, room: str) -> str:
    raw = "|".join([
        section.strip(),
        title.strip(),
        speaker.strip(),
        date.strip(),
        time_start.strip(),
        time_end.strip(),
        room.strip(),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def import_csv():
    if not os.path.exists(CSV_PATH):
        raise FileNotFoundError(f"Не найден файл program.csv: {CSV_PATH}")

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("DELETE FROM sections")

    with open(CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        print("Колонки в CSV:", reader.fieldnames)

        sections = set()

        for row in reader:
            section = (row.get("section") or "").strip()
            title = (row.get("title") or "").strip()
            speaker = (row.get("speaker") or "").strip()
            date = (row.get("date") or "").strip()
            time_start = (row.get("time_start") or "").strip()
            time_end = (row.get("time_end") or "").strip()
            room = (row.get("room") or "").strip()
            description = (row.get("description") or "").strip()

            if not section or not title:
                continue

            sections.add(section)
            event_key = make_event_key(section, title, speaker, date, time_start, time_end, room)

            cur.execute("""
                INSERT INTO events (
                    event_key, section, title, speaker, date, time_start, time_end, room, description
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_key) DO UPDATE SET
                    section=excluded.section,
                    title=excluded.title,
                    speaker=excluded.speaker,
                    date=excluded.date,
                    time_start=excluded.time_start,
                    time_end=excluded.time_end,
                    room=excluded.room,
                    description=excluded.description
            """, (
                event_key, section, title, speaker, date, time_start, time_end, room, description
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
        SELECT id, event_key, section, title, speaker, date, time_start, time_end, room, description
        FROM events
        WHERE id = ?
    """, (event_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_approved_materials(event_key: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, file_type, file_id, file_name, caption
        FROM materials
        WHERE event_key = ? AND status = 'approved'
        ORDER BY id
    """, (event_key,))
    rows = cur.fetchall()
    conn.close()
    return rows


def save_material(event_key: str, user: types.User, file_type: str, file_id: str, file_name: str = "", caption: str = ""):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO materials (
            event_key, user_id, username, full_name, file_type, file_id, file_name, caption, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
    """, (
        event_key,
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
        builder.button(text=s["name"], callback_data=f"section:{s['name']}")
    builder.adjust(1)
    return builder.as_markup()


def events_kb(section: str):
    builder = InlineKeyboardBuilder()

    for row in get_events_by_section(section):
        time_label = f"{row['time_start']}-{row['time_end']}" if row["time_start"] or row["time_end"] else "Без времени"
        label = f"{time_label} | {row['title']}"
        if len(label) > 60:
            label = label[:57] + "..."
        builder.button(text=label, callback_data=f"event:{row['id']}")

    builder.button(text="← К разделам", callback_data="back:sections")
    builder.adjust(1)
    return builder.as_markup()


def event_kb(event_id: int, section: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="📎 Материалы", callback_data=f"materials:{event_id}")
    builder.button(text="⬆️ Загрузить материалы", callback_data=f"upload:{event_id}")
    builder.button(text="← Назад", callback_data=f"section:{section}")
    builder.button(text="🏠 В начало", callback_data="back:sections")
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
        "📍 Конференция МГИ\n\n"
        "Выбери раздел программы.\n\n"
        "ℹ️ Если после паузы кнопки не отвечают — просто отправь /start",
        reply_markup=sections_kb()
    )


async def help_handler(message: types.Message):
    await message.answer(
        "Как пользоваться:\n\n"
        "1. Нажми /start\n"
        "2. Выбери раздел\n"
        "3. Выбери событие\n"
        "4. Смотри материалы или загружай свои\n\n"
        "Форматы: PDF, PPTX, DOCX, JPG, PNG\n\n"
        "Если бот молчит после паузы — отправь /start ещё раз."
    )


async def myid_handler(message: types.Message):
    await message.answer(f"Твой Telegram ID: {message.from_user.id}")


async def section_handler(callback: types.CallbackQuery):
    section = callback.data.split(":", 1)[1]

    try:
        await callback.message.edit_text(
            f"📂 {section}\n\nВыбери событие:",
            reply_markup=events_kb(section)
        )
    except Exception:
        await callback.message.answer(
            f"📂 {section}\n\nВыбери событие:",
            reply_markup=events_kb(section)
        )

    await callback.answer()


async def back_sections_handler(callback: types.CallbackQuery):
    await callback.message.answer(
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

    section = event["section"]
    title = event["title"]
    speaker = event["speaker"]
    date = event["date"]
    t1 = event["time_start"]
    t2 = event["time_end"]
    room = event["room"]
    description = event["description"]

    text = (
        f"🎤 {title}\n\n"
        f"👤 {speaker or '—'}\n"
        f"📅 {date or '—'}\n"
        f"⏰ {(t1 or '—')} – {(t2 or '—')}\n"
        f"📍 Кабинет: {room or '—'}\n"
    )

    if description:
        text += f"\n{description}"

    try:
        await callback.message.edit_text(
            text,
            reply_markup=event_kb(event_id, section)
        )
    except Exception:
        await callback.message.answer(
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

    title = event["title"]
    event_key = event["event_key"]
    materials = get_approved_materials(event_key)

    if not materials:
        await callback.message.answer(f"К событию «{title}» пока нет одобренных материалов.")
        await callback.answer()
        return

    await callback.message.answer(f"📎 Материалы к событию: {title}")

    for row in materials:
        text_caption = row["caption"] or row["file_name"] or ""
        if row["file_type"] == "document":
            await callback.message.answer_document(document=row["file_id"], caption=text_caption)
        elif row["file_type"] == "photo":
            await callback.message.answer_photo(photo=row["file_id"], caption=text_caption)
        else:
            await callback.message.answer_document(document=row["file_id"], caption=text_caption)

    await callback.answer()


async def upload_start_handler(callback: types.CallbackQuery, state: FSMContext):
    event_id = int(callback.data.split(":", 1)[1])
    event = get_event(event_id)

    if not event:
        await callback.answer("Событие не найдено", show_alert=True)
        return

    await state.set_state(UploadState.waiting_for_material)
    await state.update_data(event_id=event_id, event_key=event["event_key"])

    await callback.message.answer(
        f"⬆️ Отправь файл для события:\n\n"
        f"{event['title']}\n\n"
        f"Поддерживается: PDF, DOCX, PPTX, JPG, PNG.\n"
        f"Можно отправить документ или фото.\n\n"
        f"Материал уйдёт на модерацию."
    )
    await callback.answer()


async def receive_document(message: types.Message, state: FSMContext, bot: Bot):
    if await state.get_state() != UploadState.waiting_for_material:
        return

    data = await state.get_data()
    event_id = data.get("event_id")
    event_key = data.get("event_key")
    event = get_event(event_id)

    if not event or not event_key:
        await message.answer("Событие не найдено.")
        await state.clear()
        return

    material_id = save_material(
        event_key=event_key,
        user=message.from_user,
        file_type="document",
        file_id=message.document.file_id,
        file_name=message.document.file_name or "",
        caption=message.caption or ""
    )

    sender = message.from_user.full_name
    username = f"@{message.from_user.username}" if message.from_user.username else "без username"

    if ADMIN_ID != 0:
        await bot.send_document(
            ADMIN_ID,
            document=message.document.file_id,
            caption=(
                f"Новый материал на модерацию\n\n"
                f"Событие: {event['title']}\n"
                f"От: {sender} ({username})\n"
                f"Файл: {message.document.file_name or 'без имени'}\n"
                f"ID материала: {material_id}"
            ),
            reply_markup=moderation_kb(material_id)
        )

    await message.answer("Материал получен и отправлен на модерацию.")
    await state.clear()


async def receive_photo(message: types.Message, state: FSMContext, bot: Bot):
    if await state.get_state() != UploadState.waiting_for_material:
        return

    data = await state.get_data()
    event_id = data.get("event_id")
    event_key = data.get("event_key")
    event = get_event(event_id)

    if not event or not event_key:
        await message.answer("Событие не найдено.")
        await state.clear()
        return

    photo = message.photo[-1]

    material_id = save_material(
        event_key=event_key,
        user=message.from_user,
        file_type="photo",
        file_id=photo.file_id,
        file_name="photo.jpg",
        caption=message.caption or ""
    )

    sender = message.from_user.full_name
    username = f"@{message.from_user.username}" if message.from_user.username else "без username"

    if ADMIN_ID != 0:
        await bot.send_photo(
            ADMIN_ID,
            photo=photo.file_id,
            caption=(
                f"Новый материал на модерацию\n\n"
                f"Событие: {event['title']}\n"
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

    threading.Thread(target=run_web_server, daemon=True).start()

    init_db()
    import_csv()

    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан")

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

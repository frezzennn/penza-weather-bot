import logging
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
CITY = "Пенза"
LAT, LON = 53.1959, 45.0183
TZ = ZoneInfo("Europe/Moscow")
DB_PATH = os.getenv("DB_PATH", "subscribers.db")
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

WEATHER_CODES = {
    0: "Ясно", 1: "Преимущественно ясно", 2: "Переменная облачность",
    3: "Пасмурно", 45: "Туман", 48: "Изморозь",
    51: "Морось", 53: "Умеренная морось", 55: "Сильная морось",
    56: "Ледяная морось", 57: "Сильная ледяная морось",
    61: "Небольшой дождь", 63: "Умеренный дождь", 65: "Сильный дождь",
    66: "Ледяной дождь", 67: "Сильный ледяной дождь",
    71: "Небольшой снег", 73: "Умеренный снег", 75: "Сильный снег",
    77: "Снежные зёрна", 80: "Ливень", 81: "Сильный ливень",
    82: "Очень сильный ливень", 85: "Снегопад", 86: "Сильный снегопад",
    95: "Гроза", 96: "Гроза с градом", 99: "Сильная гроза с градом",
}

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS subscribers (chat_id INTEGER PRIMARY KEY)")
    conn.commit()
    return conn

def add_subscriber(chat_id: int):
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO subscribers(chat_id) VALUES (?)", (chat_id,))

def subscribers():
    with db() as conn:
        return [row[0] for row in conn.execute("SELECT chat_id FROM subscribers")]

def remove_subscriber(chat_id: int):
    with db() as conn:
        conn.execute("DELETE FROM subscribers WHERE chat_id = ?", (chat_id,))

def fetch_weather():
    params = {
        "latitude": LAT, "longitude": LON, "timezone": "Europe/Moscow",
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                   "is_day,precipitation,weather_code,wind_speed_10m,"
                   "wind_direction_10m,pressure_msl",
        "hourly": "temperature_2m,precipitation_probability,precipitation,"
                  "weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "sunrise,sunset,precipitation_probability_max,"
                 "wind_speed_10m_max",
        "forecast_days": 7,
    }
    response = requests.get(WEATHER_URL, params=params, timeout=20)
    response.raise_for_status()
    return response.json()

def desc(code):
    return WEATHER_CODES.get(code, "Переменная погода")

def current_text(data):
    c = data["current"]
    updated = c.get("time", "").replace("T", " ")
    return (
        f"🌤 <b>Погода в Пензе</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"<b>{c['temperature_2m']:+.0f}°C</b> · {desc(c['weather_code'])}\n"
        f"Ощущается как: {c['apparent_temperature']:+.0f}°C\n\n"
        f"💧 Влажность: {c['relative_humidity_2m']}%\n"
        f"💨 Ветер: {c['wind_speed_10m']} км/ч\n"
        f"🌧 Осадки: {c['precipitation']} мм\n"
        f"🧭 Давление: {c['pressure_msl']:.0f} гПа\n"
        f"\n<i>Обновлено: {updated} (московское время)</i>"
    )

def hourly_text(data):
    h = data["hourly"]
    now = datetime.now(TZ).replace(tzinfo=None)
    lines = ["🕒 <b>Почасовой прогноз · Пенза</b>", "━━━━━━━━━━━━━━"]
    count = 0
    for i, stamp in enumerate(h["time"]):
        dt = datetime.fromisoformat(stamp)
        if dt >= now and count < 12:
            lines.append(
                f"<b>{dt:%H:%M}</b>  {h['temperature_2m'][i]:+.0f}°C · "
                f"{desc(h['weather_code'][i])} · "
                f"осадки {h['precipitation_probability'][i]}% · "
                f"ветер {h['wind_speed_10m'][i]:.0f} км/ч"
            )
            count += 1
    return "\n".join(lines)

def weekly_text(data):
    d = data["daily"]
    lines = ["📅 <b>Прогноз на 7 дней · Пенза</b>", "━━━━━━━━━━━━━━"]
    for i, stamp in enumerate(d["time"]):
        dt = datetime.fromisoformat(stamp).date()
        lines.append(
            f"<b>{dt:%d.%m}</b> · {desc(d['weather_code'][i])}\n"
            f"   🌡 {d['temperature_2m_min'][i]:+.0f}…"
            f"{d['temperature_2m_max'][i]:+.0f}°C · "
            f"🌧 {d['precipitation_probability_max'][i]}%"
        )
    return "\n".join(lines)

def sun_text(data):
    d = data["daily"]
    return (
        f"🌅 <b>Солнце в Пензе</b>\n━━━━━━━━━━━━━━\n"
        f"Восход: {d['sunrise'][0][-5:]}\n"
        f"Закат: {d['sunset'][0][-5:]}\n"
        f"Данные по московскому времени."
    )

def alerts_text(data):
    h = data["hourly"]
    now = datetime.now(TZ).replace(tzinfo=None)
    found = []
    for i, stamp in enumerate(h["time"]):
        dt = datetime.fromisoformat(stamp)
        if now <= dt and (dt - now).total_seconds() <= 24 * 3600:
            rain = h["precipitation_probability"][i] or 0
            wind = h["wind_speed_10m"][i] or 0
            code = h["weather_code"][i]
            if rain >= 70:
                found.append(f"🌧 {dt:%H:%M}: вероятность осадков {rain}%")
            if wind >= 40:
                found.append(f"💨 {dt:%H:%M}: ветер до {wind:.0f} км/ч")
            if code in (95, 96, 99):
                found.append(f"⛈ {dt:%H:%M}: возможна гроза")
    if not found:
        return ("⚠️ <b>Погодные предупреждения</b>\n━━━━━━━━━━━━━━\n"
                "По прогнозу на ближайшие 24 часа сильных погодных явлений "
                "по заданным критериям не обнаружено.\n\n"
                "<i>Это автоматическая оценка прогноза, не официальное "
                "экстренное оповещение МЧС.</i>")
    return ("⚠️ <b>Возможные неблагоприятные условия · 24 часа</b>\n"
            "━━━━━━━━━━━━━━\n" + "\n".join(found) +
            "\n\n<i>Это оценка прогноза, не официальное предупреждение МЧС.</i>")

def clothes_text(data):
    c = data["current"]
    t = c["apparent_temperature"]
    wind = c["wind_speed_10m"]
    rain = c["precipitation"]
    if t < -20:
        advice = "Очень тёплая зимняя одежда, утеплённая обувь, шапка и варежки."
    elif t < -10:
        advice = "Тёплая зимняя куртка, шапка, перчатки и утеплённая обувь."
    elif t < 0:
        advice = "Зимняя куртка, тёплая обувь и перчатки."
    elif t < 8:
        advice = "Демисезонная куртка и закрытая обувь; пригодится шарф."
    elif t < 16:
        advice = "Куртка или плотный свитер поверх обычной одежды."
    elif t < 23:
        advice = "Лёгкая одежда; на вечер можно взять тонкую кофту."
    else:
        advice = "Лёгкая, дышащая одежда. Не забывай о воде и защите от солнца."
    if wind >= 25:
        advice += " Из-за ветра лучше добавить дополнительный слой."
    if rain > 0:
        advice += " Возьми зонт или непромокаемую куртку."
    return (f"🧥 <b>Что надеть в Пензе</b>\n━━━━━━━━━━━━━━\n"
            f"По ощущениям: {t:+.0f}°C\n{advice}\n\n"
            f"<i>Совет сформирован автоматически по текущей погоде.</i>")

def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌤 Погода сейчас", callback_data="current"),
         InlineKeyboardButton("🕒 По часам", callback_data="hourly")],
        [InlineKeyboardButton("📅 На 7 дней", callback_data="weekly"),
         InlineKeyboardButton("⚠️ Предупреждения", callback_data="alerts")],
        [InlineKeyboardButton("🌅 Солнце", callback_data="sun"),
         InlineKeyboardButton("🧥 Что надеть", callback_data="clothes")],
    ])

async def send_section(chat_id, section, context):
    try:
        data = fetch_weather()
        text_map = {
            "current": current_text, "hourly": hourly_text,
            "weekly": weekly_text, "alerts": alerts_text,
            "sun": sun_text, "clothes": clothes_text,
        }
        await context.bot.send_message(
            chat_id=chat_id, text=text_map[section](data),
            parse_mode="HTML", reply_markup=menu(),
        )
    except Exception:
        log.exception("Weather request failed")
        await context.bot.send_message(
            chat_id=chat_id,
            text="Не удалось получить прогноз. Попробуй ещё раз чуть позже."
        )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_subscriber(update.effective_chat.id)
    await update.message.reply_text(
        "Привет! Я показываю погоду в Пензе 🌤\n"
        "Буду присылать сводку каждые 6 часов.\n"
        "Выбери нужный прогноз в меню ниже.",
        reply_markup=menu(),
    )
    await send_section(update.effective_chat.id, "current", context)

async def weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_section(update.effective_chat.id, "current", context)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await send_section(query.message.chat_id, query.data, context)

async def broadcast_weather(context: ContextTypes.DEFAULT_TYPE):
    for chat_id in subscribers():
        try:
            data = fetch_weather()
            await context.bot.send_message(
                chat_id=chat_id, text=current_text(data),
                parse_mode="HTML", reply_markup=menu(),
            )
        except Exception as exc:
            log.warning("Could not message %s: %s", chat_id, exc)
            if "Forbidden" in str(exc) or "chat not found" in str(exc).lower():
                remove_subscriber(chat_id)

async def post_init(application: Application):
    application.job_queue.run_repeating(
        broadcast_weather, interval=6 * 60 * 60, first=6 * 60 * 60,
        name="six_hour_weather",
    )

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("weather", weather))
    app.add_handler(CallbackQueryHandler(button_handler))
    log.info("Starting Penza weather bot")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

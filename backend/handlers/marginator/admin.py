"""Панель разработчика Маржинатора (сводка, юзеры, оценки, рассылка)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from aiogram import F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.enums import ChatMemberStatus

from handlers.marginator.router import marginator_router
from states.marginator_states import AdminState
from keyboards.marginator_keyboards import (
    get_admin_keyboard,
    get_admin_broadcast_keyboard,
    get_admin_back_keyboard,
    get_main_reply_keyboard,
)
from database import SessionLocal
from services.marginator.db_service import MarginatorDBService
from config import get_admin_ids

logger = logging.getLogger(__name__)


def is_admin(user_id: int | None) -> bool:
    if user_id is None:
        return False
    return int(user_id) in get_admin_ids()


async def notify_admins(bot, text: str) -> None:
    for aid in get_admin_ids():
        try:
            await bot.send_message(aid, text)
        except Exception as e:
            logger.warning("notify admin %s failed: %s", aid, e)


def rating_keyboard() -> InlineKeyboardMarkup:
    row1 = [InlineKeyboardButton(text=str(i), callback_data="rate_%d" % i) for i in range(1, 6)]
    row2 = [InlineKeyboardButton(text=str(i), callback_data="rate_%d" % i) for i in range(6, 11)]
    return InlineKeyboardMarkup(inline_keyboard=[row1, row2])


RATING_MESSAGE = (
    "Здравствуйте!\n\n"
    "Мы развиваем Маржинатор и будем благодарны за вашу оценку.\n"
    "Насколько приложение полезно вам в работе с прайсами и маржой?\n\n"
    "Пожалуйста, выберите оценку от 1 до 10:"
)


def _bar(n: int, max_n: int, width: int = 10) -> str:
    if max_n <= 0 or n <= 0:
        return "░" * width
    filled = max(1, int(round(width * n / max_n))) if n else 0
    filled = min(width, filled)
    return "█" * filled + "░" * (width - filled)


def format_dev_panel() -> tuple[str, InlineKeyboardMarkup]:
    with SessionLocal() as db:
        stats = MarginatorDBService.user_stats(db)
        rstat = MarginatorDBService.rating_stats(db)
    avg = rstat["avg"]
    votes = rstat["count"]
    text = "\n".join([
        "🛠 Панель разработчика",
        "────────────────────",
        "👥 Пользователи (ЛС): %d" % stats["total"],
        "✅ Активных: %d" % stats["active"],
        "🚫 Остановили бота: %d" % stats["blocked"],
        "⭐ Оценка бота: %.2f/10 (%d голосов)" % (avg, votes),
        "············",
        "Рассылка идёт от имени бота.",
        "ЛС — только тем, кто жал /start.",
    ])
    return text, get_admin_keyboard()


def format_ratings_screen() -> str:
    with SessionLocal() as db:
        rstat = MarginatorDBService.rating_stats(db)
    dist = rstat["dist"]
    max_n = max(dist.values()) if dist else 0
    lines = [
        "⭐ Оценки бота",
        "────────────────────",
        "Средняя: %.2f / 10 · Голосов: %d" % (rstat["avg"], rstat["count"]),
        "············",
    ]
    for score in range(10, 0, -1):
        n = dist.get(score, 0)
        lines.append("%2d %s %d" % (score, _bar(n, max_n), n))
    return "\n".join(lines)


def format_users_screen(limit: int = 50) -> str:
    with SessionLocal() as db:
        users = MarginatorDBService.list_recent_users(db, limit=limit)
    lines = [
        "👥 Пользователи (последние %d)" % limit,
        "────────────────────",
    ]
    if not users:
        lines.append("Пока никого нет.")
        return "\n".join(lines)
    for u in users:
        name = (u.full_name or "—").strip() or "—"
        un = ("@" + u.username) if u.username else "—"
        dt = ""
        if getattr(u, "created_at", None):
            try:
                dt = u.created_at.strftime("%Y-%m-%d")
            except Exception:
                dt = ""
        flag = " 🚫" if getattr(u, "is_blocked", False) else ""
        rate = ""
        if getattr(u, "last_rating", None):
            rate = " · ★%d" % u.last_rating
        lines.append("• %s %s\n  %s · %s%s%s" % (name, un, u.telegram_id, dt, rate, flag))
    return "\n".join(lines)


@marginator_router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        await message.answer("Команда только для администратора.")
        return
    await state.clear()
    text, kb = format_dev_panel()
    await message.answer(text, reply_markup=kb)


@marginator_router.message(F.text.in_({"🛠 Админ", "Админ", "🛠 Разработчик"}))
async def btn_admin(message: Message, state: FSMContext):
    await cmd_admin(message, state)


@marginator_router.callback_query(F.data.in_({"admin_panel", "adm_summary"}))
async def cb_admin_summary(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    text, kb = format_dev_panel()
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await callback.message.answer(text, reply_markup=kb)


@marginator_router.callback_query(F.data == "adm_users")
async def cb_admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    text = format_users_screen(50)
    # Telegram limit 4096
    if len(text) > 4000:
        text = text[:3900] + "\n… (обрезано)"
    try:
        await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
    except Exception:
        await callback.message.answer(text, reply_markup=get_admin_back_keyboard())


@marginator_router.callback_query(F.data == "adm_ratings")
async def cb_admin_ratings(callback: CallbackQuery):
    if not is_admin(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    text = format_ratings_screen()
    try:
        await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
    except Exception:
        await callback.message.answer(text, reply_markup=get_admin_back_keyboard())


@marginator_router.callback_query(F.data == "adm_broadcast_menu")
async def cb_broadcast_menu(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    text = "\n".join([
        "📣 Рассылка обновлений",
        "────────────────────",
        "Выберите аудиторию, затем пришлите текст одним сообщением.",
        "Отмена: /cancel",
    ])
    try:
        await callback.message.edit_text(text, reply_markup=get_admin_broadcast_keyboard())
    except Exception:
        await callback.message.answer(text, reply_markup=get_admin_broadcast_keyboard())


@marginator_router.callback_query(F.data == "adm_close")
async def cb_admin_close(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    try:
        await callback.message.edit_text("Панель закрыта. Снова: /admin")
    except Exception:
        await callback.message.answer("Панель закрыта. Снова: /admin")


@marginator_router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await state.set_state(AdminState.broadcast_text)
    await callback.message.answer(
        "📢 Рассылка всем в ЛС\n\n"
        "Пришлите текст одним сообщением.\n"
        "Отмена: /cancel"
    )


@marginator_router.message(AdminState.broadcast_text)
async def admin_broadcast_send(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        await state.clear()
        return
    text = (message.text or message.caption or "").strip()
    if not text:
        await message.answer("Нужен текст. Или /cancel")
        return
    if text.startswith("/"):
        await state.clear()
        await message.answer("Рассылка отменена.")
        return

    with SessionLocal() as db:
        ids = MarginatorDBService.list_broadcast_ids(db)
    await state.clear()
    await message.answer("Отправляю %d пользователям…" % len(ids))

    ok, fail = 0, 0
    bot = message.bot
    for tid in ids:
        try:
            await bot.send_message(tid, text)
            ok += 1
        except Exception:
            fail += 1
            try:
                with SessionLocal() as db:
                    MarginatorDBService.mark_user_blocked(db, tid)
            except Exception:
                pass
        await asyncio.sleep(0.05)

    await message.answer(
        "Готово.\n✅ Доставлено: %d\n❌ Ошибок: %d" % (ok, fail),
        reply_markup=get_admin_keyboard(),
    )


@marginator_router.callback_query(F.data == "admin_rating")
async def cb_admin_rating(callback: CallbackQuery):
    if not is_admin(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer("Рассылаю опрос…")

    with SessionLocal() as db:
        ids = MarginatorDBService.list_broadcast_ids(db)

    ok, fail = 0, 0
    bot = callback.bot
    msg = RATING_MESSAGE.replace("\\n", "\n")
    for tid in ids:
        try:
            await bot.send_message(tid, msg, reply_markup=rating_keyboard())
            ok += 1
        except Exception:
            fail += 1
            try:
                with SessionLocal() as db:
                    MarginatorDBService.mark_user_blocked(db, tid)
            except Exception:
                pass
        await asyncio.sleep(0.05)

    await callback.message.answer(
        "Опрос оценки отправлен.\n✅ %d · ❌ %d" % (ok, fail),
        reply_markup=get_admin_keyboard(),
    )


@marginator_router.callback_query(F.data.startswith("rate_"))
async def cb_user_rate(callback: CallbackQuery):
    try:
        score = int((callback.data or "").replace("rate_", ""))
        if score < 1 or score > 10:
            raise ValueError
    except ValueError:
        await callback.answer("Некорректная оценка", show_alert=True)
        return

    uid = callback.from_user.id if callback.from_user else None
    if uid is None:
        await callback.answer()
        return

    with SessionLocal() as db:
        MarginatorDBService.set_user_rating(db, uid, score)

    await callback.answer("Спасибо! Ваша оценка: %d" % score)
    try:
        await callback.message.edit_text(
            "Спасибо за оценку: %d/10.\nМы учтём ваш отзыв при развитии Маржинатора." % score
        )
    except Exception:
        await callback.message.answer("Спасибо за оценку: %d/10!" % score)

    uname = callback.from_user.username
    name = callback.from_user.full_name or ""
    who = ("@" + uname) if uname else name or str(uid)
    await notify_admins(
        callback.bot,
        "⭐ Оценка Маржинатора: %d/10\nот %s (%s)" % (score, who, uid),
    )


@marginator_router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated):
    user = event.from_user
    if not user:
        return
    new = event.new_chat_member.status
    old = event.old_chat_member.status

    blocked_statuses = {ChatMemberStatus.KICKED, ChatMemberStatus.LEFT}
    active_statuses = {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.RESTRICTED,
        ChatMemberStatus.ADMINISTRATOR,
    }

    if new in blocked_statuses and old not in blocked_statuses:
        with SessionLocal() as db:
            MarginatorDBService.mark_user_blocked(db, user.id)
        uname = ("@" + user.username) if user.username else (user.full_name or str(user.id))
        await notify_admins(
            event.bot,
            "🚫 Пользователь остановил бота:\n%s (%s)" % (uname, user.id),
        )
    elif new in active_statuses and old in blocked_statuses:
        with SessionLocal() as db:
            is_new = []
            MarginatorDBService.touch_user(
                db,
                user.id,
                username=user.username,
                full_name=user.full_name,
                is_new_out=is_new,
            )
        uname = ("@" + user.username) if user.username else (user.full_name or str(user.id))
        await notify_admins(
            event.bot,
            "✅ Пользователь снова запустил бота:\n%s (%s)" % (uname, user.id),
        )

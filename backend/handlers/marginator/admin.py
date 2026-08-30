"""Админ-панель разработчика: уведомления, рассылка, оценка."""
from __future__ import annotations

import asyncio
import logging

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
from keyboards.marginator_keyboards import get_admin_keyboard, get_main_reply_keyboard
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
    row1 = [InlineKeyboardButton(text=str(i), callback_data=f"rate_{i}") for i in range(1, 6)]
    row2 = [InlineKeyboardButton(text=str(i), callback_data=f"rate_{i}") for i in range(6, 11)]
    return InlineKeyboardMarkup(inline_keyboard=[row1, row2])


RATING_MESSAGE = (
    "Здравствуйте!\n\n"
    "Мы развиваем *Маржинатор* и будем благодарны за вашу оценку.\n"
    "Насколько приложение полезно вам в работе с прайсами и маржой?\n\n"
    "Пожалуйста, выберите оценку от *1* до *10*:"
)


@marginator_router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        await message.answer("Команда только для администратора.")
        return
    await state.clear()
    with SessionLocal() as db:
        stats = MarginatorDBService.user_stats(db)
    await message.answer(
        "🛠 *Админ-панель Маржинатора*\n\n"
        f"Пользователей: *{stats['total']}*\n"
        f"Активных: *{stats['active']}*\n"
        f"Остановили бота: *{stats['blocked']}*\n"
        f"Оценили: *{stats['rated']}*\n\n"
        "Выберите действие:",
        parse_mode="Markdown",
        reply_markup=get_admin_keyboard(),
    )


@marginator_router.message(F.text.in_({"🛠 Админ", "Админ"}))
async def btn_admin(message: Message, state: FSMContext):
    await cmd_admin(message, state)


@marginator_router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    with SessionLocal() as db:
        stats = MarginatorDBService.user_stats(db)
    await callback.message.answer(
        "🛠 *Админ-панель*\n\n"
        f"Всего: {stats['total']} · активных: {stats['active']} · "
        f"стоп: {stats['blocked']} · оценок: {stats['rated']}",
        parse_mode="Markdown",
        reply_markup=get_admin_keyboard(),
    )


@marginator_router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await state.set_state(AdminState.broadcast_text)
    await callback.message.answer(
        "📢 *Рассылка*\n\n"
        "Пришлите текст сообщения одним сообщением.\n"
        "Оно уйдёт всем *активным* пользователям.\n\n"
        "Отмена: /cancel",
        parse_mode="Markdown",
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
    await message.answer(f"Отправляю {len(ids)} пользователям…")

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
        f"Готово.\n✅ Доставлено: {ok}\n❌ Ошибок: {fail}",
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
    for tid in ids:
        try:
            await bot.send_message(
                tid,
                RATING_MESSAGE,
                parse_mode="Markdown",
                reply_markup=rating_keyboard(),
            )
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
        f"Опрос оценки отправлен.\n✅ {ok} · ❌ {fail}",
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

    await callback.answer(f"Спасибо! Ваша оценка: {score}")
    try:
        await callback.message.edit_text(
            f"Спасибо за оценку: *{score}/10*.\nМы учтём ваш отзыв при развитии Маржинатора.",
            parse_mode="Markdown",
        )
    except Exception:
        await callback.message.answer(f"Спасибо за оценку: {score}/10!")

    uname = callback.from_user.username
    name = callback.from_user.full_name or ""
    who = f"@{uname}" if uname else name or str(uid)
    await notify_admins(
        callback.bot,
        f"⭐ Оценка Маржинатора: *{score}/10*\nот {who} (`{uid}`)",
    )


@marginator_router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated):
    """Пользователь заблокировал/разблокировал бота."""
    user = event.from_user
    if not user:
        return
    new = event.new_chat_member.status
    old = event.old_chat_member.status

    blocked_statuses = {ChatMemberStatus.KICKED, ChatMemberStatus.LEFT}
    active_statuses = {ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED, ChatMemberStatus.ADMINISTRATOR}

    if new in blocked_statuses and old not in blocked_statuses:
        with SessionLocal() as db:
            MarginatorDBService.mark_user_blocked(db, user.id)
        uname = f"@{user.username}" if user.username else (user.full_name or str(user.id))
        await notify_admins(
            event.bot,
            f"🚫 Пользователь *остановил* бота:\n{uname} (`{user.id}`)",
        )
    elif new in active_statuses and old in blocked_statuses:
        with SessionLocal() as db:
            is_new: list = []
            MarginatorDBService.touch_user(
                db,
                user.id,
                username=user.username,
                full_name=user.full_name,
                is_new_out=is_new,
            )
        uname = f"@{user.username}" if user.username else (user.full_name or str(user.id))
        await notify_admins(
            event.bot,
            f"✅ Пользователь *снова запустил* бота:\n{uname} (`{user.id}`)",
        )

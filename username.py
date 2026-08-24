"""Очередь на юзернеймы: ждёт, пока чужой юз освободится, и занимает его каналом.

Показывает, кто держит юзернейм, и считает, когда Telegram может удалить
аккаунт владельца за простой. Как только юз освобождается — создаёт канал,
вешает на него юзернейм и пишет туда отчёт.
"""

# meta developer: @soika
# meta description: Следит за занятыми юзернеймами, считает срок автоудаления аккаунта владельца и занимает юз каналом, как только он освободится

import asyncio
import logging
import math
import re
import time

from telethon import errors
from telethon.tl import functions, types

from .. import loader, utils

logger = logging.getLogger(__name__)

HOUR = 3600
DAY = 86400

#: Сколько дней простоя Telegram даёт аккаунту при каждом варианте настройки
TTL_DAYS = {1: 30, 3: 90, 6: 182, 12: 365, 18: 548, 24: 730}

#: Каким может быть юзернейм: латиница, цифры, подчёркивание, первая — буква
USERNAME = re.compile(r"^[a-z][a-z0-9_]{3,31}$")


@loader.tds
class ЮзернеймыМод(loader.Module):
    """Очередь на чужие юзернеймы с автозахватом"""

    strings = {
        "name": "Юзернеймы",
        # ── карточка ────────────────────────────────────────────────────
        "head": "🔎 <b>@{}</b>",
        "st_free": "🟢 <b>Свободен</b> — можно занимать",
        "st_taken": "🔴 <b>Занят</b> · {}",
        "st_yours": "✅ <b>Занят вашим чатом</b>",
        "st_fragment": "🟣 <b>Свободен, но продаётся на Fragment</b>",
        "st_flood": "⏳ <b>Telegram просит подождать {}</b>\n<i>Ниже — данные с прошлой проверки.</i>",
        "st_unknown": "❔ <b>Проверить не вышло</b>\n<code>{}</code>",
        "invalid": (
            "⚠️ <b>Такого юзернейма не бывает</b>\n\n"
            "<i>Латиница, цифры и подчёркивание, 4–32 символа, первый — буква.</i>"
        ),
        "kind_user": "👤 пользователь",
        "kind_bot": "🤖 бот",
        "kind_channel": "📢 канал",
        "kind_group": "👥 группа",
        "kind_unknown": "❔ неизвестно кем",
        # ── блок владельца ──────────────────────────────────────────────
        "owner_title": "{} <b>Владелец</b>",
        "lbl_name": "Имя",
        "lbl_id": "ID",
        "lbl_seen": "Был в сети",
        "lbl_premium": "Premium",
        "yes": "да",
        "seen_online": "прямо сейчас",
        "seen_exact": "{date} · {ago} назад",
        "seen_recently": "недавно — не больше 3 дн. назад",
        "seen_week": "на этой неделе — 3–7 дн. назад",
        "seen_month": "в этом месяце — 7–30 дн. назад",
        "seen_unknown": "давно — больше месяца назад",
        # ── блок автоудаления ───────────────────────────────────────────
        "ttl_title": "⏳ <b>Автоудаление аккаунта</b>",
        "lbl_limit": "Лимит простоя",
        "lbl_deadline": "Крайний срок",
        "lbl_free_in": "Юз освободится",
        "val_limit": "{} мес.",
        "val_limit_max": "{} мес. — максимум у Telegram",
        "dl_approx": "~{}",
        "free_exact": "через {days}",
        "free_not_before": "не раньше чем через {days}",
        "free_not_after": "не позже чем через {days}",
        "free_overdue": "срок вышел — удалить могут в любой день",
        "ttl_note": (
            "<i>Оценка: считаем по лимиту {} мес. Владелец мог выбрать срок"
            " короче — тогда аккаунт удалят раньше. Зайдёт в Telegram —"
            " отсчёт начнётся заново.</i>"
        ),
        "ttl_none": (
            "⏳ <b>Автоудаление аккаунта</b>\n"
            "Не тот случай: за простой Telegram удаляет только аккаунты людей."
            " Юз освободится, если владелец снимет его сам."
        ),
        # ── блок очереди ────────────────────────────────────────────────
        "queue_title": "📌 <b>В очереди</b>",
        "lbl_added": "Добавлен",
        "lbl_checks": "Проверок",
        "lbl_next": "Следующая проверка",
        "in_span": "через {}",
        "now": "вот-вот",
        # ── меню очереди ────────────────────────────────────────────────
        "menu": "📋 <b>Очередь юзернеймов</b> · {}\n\n{}",
        "menu_empty": (
            "📋 <b>Очередь пуста</b>\n\n"
            "<code>{0}uzadd durov</code> — встать в очередь за юзернеймом.\n"
            "<code>{0}uz durov</code> — просто посмотреть, кто его держит."
        ),
        "menu_foot": "<i>Обхожу очередь раз в {}, ближайшая проверка {}.</i>",
        "row_wait": "⚪️ <b>@{}</b>\n     жду первой проверки",
        "row_taken": "🔴 <b>@{}</b>\n     занят · {}",
        "row_free": "🟢 <b>@{}</b>\n     свободен — занимаю",
        "row_fragment": "🟣 <b>@{}</b>\n     ушёл на Fragment",
        "row_done": "✅ <b>@{}</b>\n     взят {}",
        "row_error": "⚠️ <b>@{}</b>\n     {}",
        "row_free_in": "освободится {}",
        "row_free_never": "освободится, только если владелец снимет его сам",
        "row_free_unknown": "когда освободится — неизвестно",
        # ── команды ─────────────────────────────────────────────────────
        "usage": (
            "🔎 <b>Юзернеймы</b>\n\n"
            "<code>{0}uz durov</code> — кто держит юз и когда освободится\n"
            "<code>{0}uzadd durov</code> — встать в очередь\n"
            "<code>{0}uzdel durov</code> — выйти из очереди\n"
            "<code>{0}uzlist</code> — вся очередь\n\n"
            "<i>Как юз освободится — Сойка создаст канал, повесит юзернейм"
            " и пришлёт отчёт.</i>"
        ),
        "already": "📌 <b>@{}</b> <b>уже в очереди</b>",
        "dropped": "🗑 <b>@{}</b> <b>убран из очереди</b>",
        "not_queued": "🔎 <b>@{}</b> <b>в очереди не значится</b>",
        "full": "🚧 <b>В очереди уже {} — больше не влезет</b>\n<i>Освободи место или подними лимит в</i> <code>{}cfg</code>",
        "wiped": "🧹 <b>Очередь очищена</b>",
        "checking": "⏳ <b>Проверяю @{}…</b>",
        "take_busy": (
            "🔴 <b>@{0}</b> <b>занят — занять сейчас не выйдет</b>\n\n"
            "<i>Встать в очередь и дождаться:</i> <code>{1}uzadd {0}</code>"
        ),
        # ── отчёт в созданном чате ──────────────────────────────────────
        "report": "🎉 <b>@{}</b> <b>— наш</b>\n\n{}",
        "report_title": "📊 <b>Хроника ожидания</b>",
        "lbl_since": "Встали в очередь",
        "lbl_freed": "Юз освободился",
        "lbl_waited": "Прождали",
        "prev_title": "👤 <b>Прошлый владелец</b>",
        "lbl_kind": "Тип",
        "report_foot": (
            "<i>Чат создан автоматически. Переименуй его, поставь аватар —"
            " или перенеси юзернейм в другой чат через настройки.</i>"
        ),
        "caught": (
            "🎉 <b>Юзернейм @{0} взят</b>\n\n"
            "Ждали {1}, проверок: {2}.\n"
            "Отчёт лежит в новом чате: t.me/{0}"
        ),
        "vacant": (
            "🟢 <b>Юзернейм @{}</b> <b>свободен</b>\n\n"
            "<i>Автозахват выключен — занимай руками.</i>"
        ),
        "gone_fragment": (
            "🟣 <b>Юзернейм @{}</b> <b>выставлен на Fragment</b>\n\n"
            "<i>Бесплатно его уже не занять.</i>"
        ),
        "failed": "⚠️ <b>С @{}</b> <b>не вышло:</b> <code>{}</code>",
        # ── кнопки ──────────────────────────────────────────────────────
        "btn_watch": "🔔 В очередь",
        "btn_unwatch": "🔕 Из очереди",
        "btn_take": "⚡️ Занять сейчас",
        "btn_refresh": "🔄 Обновить",
        "btn_menu": "📋 Очередь",
        "btn_open": "↗️ Открыть",
        "btn_recheck": "🔄 Проверить всё",
        "btn_wipe": "🧹 Очистить",
        "btn_close": "✖️ Закрыть",
        "cb_added": "📌 В очереди",
        "cb_dropped": "🗑 Убрал",
        "cb_working": "⏳ Проверяю…",
        "cb_taking": "⚡️ Занимаю…",
        # ── единицы ─────────────────────────────────────────────────────
        "u_day": "{} дн.",
        "u_hour": "{} ч.",
        "u_min": "{} мин.",
        "u_sec": "{} сек.",
        "u_less": "меньше минуты",
        "p_days": "день|дня|дней",
    }

    strings_en = {
        "head": "🔎 <b>@{}</b>",
        "st_free": "🟢 <b>Free</b> — yours to take",
        "st_taken": "🔴 <b>Taken</b> · {}",
        "st_yours": "✅ <b>Taken by your chat</b>",
        "st_fragment": "🟣 <b>Free, but sold on Fragment</b>",
        "st_flood": "⏳ <b>Telegram asks to wait {}</b>\n<i>Showing the previous check.</i>",
        "st_unknown": "❔ <b>Check failed</b>\n<code>{}</code>",
        "invalid": (
            "⚠️ <b>Not a valid username</b>\n\n"
            "<i>Latin letters, digits and underscore, 4–32 chars, starts with a letter.</i>"
        ),
        "kind_user": "👤 user",
        "kind_bot": "🤖 bot",
        "kind_channel": "📢 channel",
        "kind_group": "👥 group",
        "kind_unknown": "❔ someone",
        "owner_title": "{} <b>Holder</b>",
        "lbl_name": "Name",
        "lbl_id": "ID",
        "lbl_seen": "Last seen",
        "lbl_premium": "Premium",
        "yes": "yes",
        "seen_online": "right now",
        "seen_exact": "{date} · {ago} ago",
        "seen_recently": "recently — up to 3 days ago",
        "seen_week": "this week — 3–7 days ago",
        "seen_month": "this month — 7–30 days ago",
        "seen_unknown": "long ago — over a month",
        "ttl_title": "⏳ <b>Account self-destruct</b>",
        "lbl_limit": "Inactivity limit",
        "lbl_deadline": "Deadline",
        "lbl_free_in": "Username frees",
        "val_limit": "{} months",
        "val_limit_max": "{} months — the longest Telegram allows",
        "dl_approx": "~{}",
        "free_exact": "in {days}",
        "free_not_before": "no sooner than in {days}",
        "free_not_after": "no later than in {days}",
        "free_overdue": "overdue — may be wiped any day now",
        "ttl_note": (
            "<i>Estimate: counted against a {}-month limit. The holder may have"
            " picked a shorter one, then the account goes earlier. One login"
            " restarts the countdown.</i>"
        ),
        "ttl_none": (
            "⏳ <b>Account self-destruct</b>\n"
            "Not the case here: Telegram only wipes people's accounts for"
            " inactivity. This username frees only if the holder drops it."
        ),
        "queue_title": "📌 <b>Queued</b>",
        "lbl_added": "Added",
        "lbl_checks": "Checks",
        "lbl_next": "Next check",
        "in_span": "in {}",
        "now": "any moment",
        "menu": "📋 <b>Username queue</b> · {}\n\n{}",
        "menu_empty": (
            "📋 <b>Queue is empty</b>\n\n"
            "<code>{0}uzadd durov</code> — queue up for a username.\n"
            "<code>{0}uz durov</code> — just look up who holds it."
        ),
        "menu_foot": "<i>Sweeping the queue every {}, next check {}.</i>",
        "row_wait": "⚪️ <b>@{}</b>\n     awaiting first check",
        "row_taken": "🔴 <b>@{}</b>\n     taken · {}",
        "row_free": "🟢 <b>@{}</b>\n     free — claiming it",
        "row_fragment": "🟣 <b>@{}</b>\n     moved to Fragment",
        "row_done": "✅ <b>@{}</b>\n     claimed {}",
        "row_error": "⚠️ <b>@{}</b>\n     {}",
        "row_free_in": "frees {}",
        "row_free_never": "frees only if the holder drops it",
        "row_free_unknown": "no telling when it frees",
        "usage": (
            "🔎 <b>Usernames</b>\n\n"
            "<code>{0}uz durov</code> — who holds it and when it frees\n"
            "<code>{0}uzadd durov</code> — queue up\n"
            "<code>{0}uzdel durov</code> — leave the queue\n"
            "<code>{0}uzlist</code> — the whole queue\n\n"
            "<i>Once free, Soika creates a chat, puts the username on it"
            " and sends a report.</i>"
        ),
        "already": "📌 <b>@{}</b> <b>is already queued</b>",
        "dropped": "🗑 <b>@{}</b> <b>left the queue</b>",
        "not_queued": "🔎 <b>@{}</b> <b>is not queued</b>",
        "full": "🚧 <b>Queue is full: {}</b>\n<i>Free a slot or raise the limit in</i> <code>{}cfg</code>",
        "wiped": "🧹 <b>Queue cleared</b>",
        "checking": "⏳ <b>Checking @{}…</b>",
        "take_busy": (
            "🔴 <b>@{0}</b> <b>is taken — cannot claim it now</b>\n\n"
            "<i>Queue up and wait:</i> <code>{1}uzadd {0}</code>"
        ),
        "report": "🎉 <b>@{}</b> <b>is ours</b>\n\n{}",
        "report_title": "📊 <b>The wait</b>",
        "lbl_since": "Queued at",
        "lbl_freed": "Freed at",
        "lbl_waited": "Waited",
        "prev_title": "👤 <b>Previous holder</b>",
        "lbl_kind": "Type",
        "report_foot": (
            "<i>Chat created automatically. Rename it, set a picture —"
            " or move the username to another chat in its settings.</i>"
        ),
        "caught": (
            "🎉 <b>Username @{0} claimed</b>\n\n"
            "Waited {1}, checks: {2}.\n"
            "The report is in the new chat: t.me/{0}"
        ),
        "vacant": (
            "🟢 <b>Username @{}</b> <b>is free</b>\n\n"
            "<i>Auto-claim is off — take it by hand.</i>"
        ),
        "gone_fragment": (
            "🟣 <b>Username @{}</b> <b>went to Fragment</b>\n\n"
            "<i>No taking it for free now.</i>"
        ),
        "failed": "⚠️ <b>@{}</b> <b>failed:</b> <code>{}</code>",
        "btn_watch": "🔔 Queue up",
        "btn_unwatch": "🔕 Unqueue",
        "btn_take": "⚡️ Claim now",
        "btn_refresh": "🔄 Refresh",
        "btn_menu": "📋 Queue",
        "btn_open": "↗️ Open",
        "btn_recheck": "🔄 Check all",
        "btn_wipe": "🧹 Clear",
        "btn_close": "✖️ Close",
        "cb_added": "📌 Queued",
        "cb_dropped": "🗑 Removed",
        "cb_working": "⏳ Checking…",
        "cb_taking": "⚡️ Claiming…",
        "u_day": "{}d",
        "u_hour": "{}h",
        "u_min": "{}m",
        "u_sec": "{}s",
        "u_less": "under a minute",
        "p_days": "day|days",
    }

    config = loader.ModuleConfig(
        loader.ConfigValue(
            "interval",
            10,
            "Раз во сколько минут проверять юзернеймы из очереди",
            validator=loader.validators.Integer(minimum=1, maximum=1440),
        ),
        loader.ConfigValue(
            "deep_sleep",
            True,
            "Реже проверять юзы, до освобождения которых далеко",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "ttl_months",
            24,
            "Лимит простоя, по которому считать автоудаление аккаунта (мес.)",
            validator=loader.validators.Choice([1, 3, 6, 12, 18, 24]),
        ),
        loader.ConfigValue(
            "auto_claim",
            True,
            "Занимать юзернейм сразу, как освободится",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "chat_kind",
            "channel",
            "Что создавать под юзернейм: канал или супергруппу",
            validator=loader.validators.Choice(["channel", "supergroup"]),
        ),
        loader.ConfigValue(
            "title",
            "@{username}",
            "Название создаваемого чата, {username} подставится",
            validator=loader.validators.String(max_len=128),
        ),
        loader.ConfigValue(
            "about",
            "",
            "Описание создаваемого чата",
            validator=loader.validators.String(max_len=255),
        ),
        loader.ConfigValue(
            "rollback",
            True,
            "Удалять только что созданный пустой чат, если юзернейм на него не встал",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "report_to",
            "me",
            "Куда слать уведомления: me, @ник или id чата",
            validator=loader.validators.String(max_len=64),
        ),
        loader.ConfigValue(
            "max_queue",
            10,
            "Сколько юзернеймов держать в очереди одновременно",
            validator=loader.validators.Integer(minimum=1, maximum=50),
        ),
    )

    # ------------------------------------------------------------------ #
    #  Жизненный цикл
    # ------------------------------------------------------------------ #
    async def client_ready(self, client, db):
        """Подчистить очередь от мусора, если базу правили руками."""
        queue = self._queue()

        for username in [name for name in queue if not USERNAME.match(str(name))]:
            queue.pop(username)

    def on_unload(self):
        """Погасить фоновый обход очереди."""
        watchdog = getattr(self, "_watchdog", None)

        if watchdog is not None and hasattr(watchdog, "stop"):
            watchdog.stop()

    # ------------------------------------------------------------------ #
    #  Команды
    # ------------------------------------------------------------------ #
    @loader.command(aliases=["юз"])
    async def uzcmd(self, message):
        """<юзернейм> — кто держит, когда был в сети и когда юз освободится"""
        username = self._clean(utils.get_args_raw(message))

        if not username:
            await self._answer(message, self.strings["usage"].format(self._prefix))
            return

        if not USERNAME.match(username):
            await utils.answer(message, self.strings["invalid"])
            return

        sent = await utils.answer(message, self.strings["checking"].format(username))
        await self._answer(sent, await self._card(username), username)

    @loader.owner
    @loader.command(aliases=["uzw", "юзадд"])
    async def uzaddcmd(self, message):
        """<юзернейм> — встать в очередь: освободится — займём каналом сами"""
        username = self._clean(utils.get_args_raw(message))

        if not username:
            await self._answer(message, self.strings["usage"].format(self._prefix))
            return

        if not USERNAME.match(username):
            await utils.answer(message, self.strings["invalid"])
            return

        queue = self._queue()

        if username in queue:
            await utils.answer(message, self.strings["already"].format(username))
            return

        if len(queue) >= self.config["max_queue"]:
            await utils.answer(
                message,
                self.strings["full"].format(len(queue), self._prefix),
            )
            return

        queue[username] = self._blank()
        sent = await utils.answer(message, self.strings["checking"].format(username))
        await self._tick(username, queue[username])
        await self._answer(sent, await self._card(username), username)

    @loader.owner
    @loader.command(aliases=["uzt", "юззанять"])
    async def uztakecmd(self, message):
        """<юзернейм> — занять свободный юз прямо сейчас, не вставая в очередь"""
        username = self._clean(utils.get_args_raw(message))

        if not username:
            await self._answer(message, self.strings["usage"].format(self._prefix))
            return

        if not USERNAME.match(username):
            await utils.answer(message, self.strings["invalid"])
            return

        sent = await utils.answer(message, self.strings["checking"].format(username))
        entry = self._blank()
        await self._tick(username, entry, force_claim=True)

        # В очередь кладём только удачу: ждать нас не просили
        if entry.get("state") != "done":
            await utils.answer(
                sent, self.strings["take_busy"].format(username, self._prefix)
            )
            return

        self._queue()[username] = entry
        await self._answer(sent, await self._card(username), username)

    @loader.owner
    @loader.command(aliases=["юздел"])
    async def uzdelcmd(self, message):
        """<юзернейм> — убрать юзернейм из очереди слежки"""
        username = self._clean(utils.get_args_raw(message))
        queue = self._queue()

        if username not in queue:
            await utils.answer(
                message,
                self.strings["not_queued"].format(utils.escape_html(username)),
            )
            return

        queue.pop(username)
        await utils.answer(message, self.strings["dropped"].format(username))

    @loader.command(aliases=["uzl", "юзлист"])
    async def uzlistcmd(self, message):
        """— вся очередь: за чем слежу и когда что освободится"""
        await self._answer(message, self._menu(), menu=True)

    # ------------------------------------------------------------------ #
    #  Кнопки
    # ------------------------------------------------------------------ #
    async def _open(self, call, username: str) -> None:
        await call.answer(self.strings["cb_working"])
        await call.edit(await self._card(username), reply_markup=self._card_markup(username))

    async def _watch(self, call, username: str) -> None:
        queue = self._queue()

        if username not in queue:
            if len(queue) >= self.config["max_queue"]:
                await call.answer(
                    self.strings["full"].format(len(queue), self._prefix),
                    show_alert=True,
                )
                return

            queue[username] = self._blank()
            await self._tick(username, queue[username])

        await call.answer(self.strings["cb_added"])
        await call.edit(await self._card(username), reply_markup=self._card_markup(username))

    async def _unwatch(self, call, username: str) -> None:
        self._queue().pop(username, None)
        await call.answer(self.strings["cb_dropped"])
        await call.edit(await self._card(username), reply_markup=self._card_markup(username))

    async def _take_now(self, call, username: str) -> None:
        await call.answer(self.strings["cb_taking"])
        queue = self._queue()
        entry = queue.setdefault(username, self._blank())
        await self._tick(username, entry, force_claim=True)
        await call.edit(await self._card(username), reply_markup=self._card_markup(username))

    async def _sweep(self, call) -> None:
        await call.answer(self.strings["cb_working"])

        for username, entry in list(self._queue().items()):
            if entry.get("state") != "done":
                await self._tick(username, entry)

        await call.edit(self._menu(), reply_markup=self._menu_markup())

    async def _to_menu(self, call) -> None:
        await call.edit(self._menu(), reply_markup=self._menu_markup())

    async def _wipe(self, call) -> None:
        self._queue().clear()
        await call.answer(self.strings["wiped"])
        await call.edit(self._menu(), reply_markup=self._menu_markup())

    async def _close(self, call) -> None:
        await call.delete()

    # ------------------------------------------------------------------ #
    #  Фоновый обход очереди
    # ------------------------------------------------------------------ #
    @loader.loop(interval=60, autostart=True)
    async def _watchdog(self):
        """Раз в минуту смотрит, кому из очереди пора на проверку."""
        queue = self._queue()

        if not queue:
            return

        now = time.time()

        for username, entry in list(queue.items()):
            if entry.get("state") == "done" or entry.get("next_check", 0) > now:
                continue

            try:
                await self._tick(username, entry)
            except Exception:
                logger.exception("Обход очереди споткнулся на @%s", username)
                entry["next_check"] = int(now) + HOUR

            await asyncio.sleep(2)

    # ------------------------------------------------------------------ #
    #  Проверка и захват
    # ------------------------------------------------------------------ #
    async def _tick(self, username: str, entry: dict, force_claim: bool = False) -> None:
        """Одна проверка юзернейма с решением, что делать дальше."""
        probe = await self._probe(username)
        now = int(time.time())

        entry["checks"] = entry.get("checks", 0) + 1
        entry["last_check"] = now

        state = probe["state"]

        if state == "flood":
            entry["next_check"] = now + probe["wait"] + 5
            return

        if state in {"invalid", "error"}:
            entry["state"] = "error"
            entry["note"] = probe.get("note", "")
            entry["next_check"] = now + HOUR
            return

        if state == "taken":
            entry["state"] = "taken"
            entry["holder"] = probe["holder"] or entry.get("holder")
            entry["told"] = False
            entry["next_check"] = now + self._delay(entry)
            return

        if state == "fragment":
            entry["state"] = "fragment"
            entry["next_check"] = now + self._delay(entry)

            if not entry.get("told"):
                entry["told"] = True
                await self._shout(self.strings["gone_fragment"].format(username))

            return

        entry["state"] = "free"
        entry["freed_at"] = entry.get("freed_at") or now

        if not (self.config["auto_claim"] or force_claim):
            entry["next_check"] = now + self._delay(entry)

            if not entry.get("told"):
                entry["told"] = True
                await self._shout(self.strings["vacant"].format(username))

            return

        await self._take(username, entry)

    async def _probe(self, username: str) -> dict:
        """Спросить у Telegram, занят ли юзернейм и кем."""
        try:
            resolved = await self.client(
                functions.contacts.ResolveUsernameRequest(username=username)
            )
        except errors.FloodWaitError as error:
            return {"state": "flood", "wait": int(error.seconds)}
        except errors.RPCError as error:
            if self._rpc_is(error, "FLOOD"):
                return {"state": "flood", "wait": int(getattr(error, "seconds", 60))}

            if self._rpc_is(error, "USERNAME_NOT_OCCUPIED"):
                return await self._confirm_free(username)

            if self._rpc_is(error, "USERNAME_INVALID"):
                return {"state": "invalid", "note": self._reason(error)}

            logger.exception("Telegram не отдал @%s", username)
            return {"state": "error", "note": self._reason(error)}
        except ValueError as error:
            return {"state": "invalid", "note": self._reason(error)}
        except Exception as error:
            logger.exception("Проверка @%s сорвалась", username)
            return {"state": "error", "note": self._reason(error)}

        holder = self._holder(resolved)

        if holder is None:
            return await self._confirm_free(username)

        return {"state": "taken", "holder": holder}

    async def _confirm_free(self, username: str) -> dict:
        """Юз никем не занят — уточняем, отдадут ли его даром."""
        try:
            free = await self.client(
                functions.account.CheckUsernameRequest(username=username)
            )
        except errors.FloodWaitError as error:
            return {"state": "flood", "wait": int(error.seconds)}
        except errors.RPCError as error:
            if self._rpc_is(error, "FLOOD"):
                return {"state": "flood", "wait": int(getattr(error, "seconds", 60))}

            if self._rpc_is(error, "PURCHASE_AVAILABLE"):
                return {"state": "fragment"}

            if self._rpc_is(error, "USERNAME_INVALID"):
                return {"state": "invalid", "note": self._reason(error)}

            if self._rpc_is(error, "USERNAME_OCCUPIED"):
                return {"state": "taken", "holder": None}

            logger.exception("Не вышло уточнить @%s", username)
            return {"state": "error", "note": self._reason(error)}
        except Exception as error:
            logger.exception("Не вышло уточнить @%s", username)
            return {"state": "error", "note": self._reason(error)}

        return {"state": "free"} if free else {"state": "taken", "holder": None}

    async def _take(self, username: str, entry: dict) -> None:
        """Создать чат и повесить на него освободившийся юзернейм."""
        now = int(time.time())
        broadcast = self.config["chat_kind"] == "channel"

        try:
            title = str(self.config["title"]).format(username=username)
        except Exception:
            title = "@" + username

        try:
            created = await self.client(
                functions.channels.CreateChannelRequest(
                    title=title[:128] or "@" + username,
                    about=str(self.config["about"])[:255],
                    broadcast=broadcast,
                    megagroup=not broadcast,
                )
            )
            chat = created.chats[0]
        except errors.FloodWaitError as error:
            entry["next_check"] = now + int(error.seconds) + 5
            return
        except Exception as error:
            logger.exception("Не вышло создать чат под @%s", username)
            entry["state"] = "error"
            entry["note"] = self._reason(error)
            entry["next_check"] = now + HOUR
            await self._shout(
                self.strings["failed"].format(
                    username, utils.escape_html(entry["note"])
                )
            )
            return

        try:
            await self.client(
                functions.channels.UpdateUsernameRequest(channel=chat, username=username)
            )
        except Exception as error:
            note = self._reason(error)
            lost = self._rpc_is(error, "USERNAME_OCCUPIED")

            if lost:
                logger.info("Юзернейм @%s заняли раньше нас", username)
            else:
                logger.exception("Юзернейм @%s на новый чат не встал", username)

            if self.config["rollback"]:
                try:
                    await self.client(
                        functions.channels.DeleteChannelRequest(channel=chat)
                    )
                except Exception:
                    logger.exception("Пустой чат под @%s остался висеть", username)

            if isinstance(error, errors.FloodWaitError):
                entry["next_check"] = now + int(error.seconds) + 5
                return

            if lost:
                entry["state"] = "taken"
                entry["next_check"] = now + self._delay(entry)
                return

            entry["state"] = "error"
            entry["note"] = note
            entry["next_check"] = now + HOUR
            await self._shout(
                self.strings["failed"].format(username, utils.escape_html(note))
            )
            return

        entry["state"] = "done"
        entry["done_at"] = now
        entry["freed_at"] = entry.get("freed_at") or now
        entry["chat_id"] = int(f"-100{chat.id}")

        try:
            report = await self.client.send_message(
                chat,
                self._report(username, entry),
                link_preview=False,
            )
            await self.client.pin_message(chat, report, notify=False)
        except Exception:
            logger.exception("Отчёт в @%s не ушёл", username)

        await self._shout(
            self.strings["caught"].format(
                username,
                self._span(now - entry.get("added", now)),
                entry.get("checks", 1),
            )
        )

    async def _shout(self, text: str) -> None:
        """Уведомление владельцу — туда, куда он попросил."""
        target = str(self.config["report_to"] or "me").strip() or "me"

        if target.lstrip("-").isdigit():
            target = int(target)

        try:
            await self.client.send_message(target, text, link_preview=False)
        except Exception:
            logger.exception("Уведомление в %s не ушло", target)

            try:
                await self.client.send_message("me", text, link_preview=False)
            except Exception:
                logger.exception("И в избранное тоже не ушло")

    # ------------------------------------------------------------------ #
    #  Карточка юзернейма
    # ------------------------------------------------------------------ #
    async def _card(self, username: str) -> str:
        """Всё, что известно про юзернейм, одним сообщением."""
        entry = self._queue().get(username)
        probe = await self._probe(username)
        state = probe["state"]

        if state == "flood":
            head = self.strings["st_flood"].format(self._span(probe["wait"]))
            holder = (entry or {}).get("holder")
        elif state == "invalid":
            return self.strings["invalid"]
        elif state == "error":
            head = self.strings["st_unknown"].format(
                utils.escape_html(probe.get("note", ""))
            )
            holder = (entry or {}).get("holder")
        else:
            holder = probe.get("holder")

            if entry is not None:
                entry["last_check"] = int(time.time())
                entry["holder"] = holder or entry.get("holder")

                if entry.get("state") != "done":
                    entry["state"] = state

            if state == "free":
                head = self.strings["st_free"]
            elif state == "fragment":
                head = self.strings["st_fragment"]
            elif holder is None:
                head = self.strings["st_taken"].format(self.strings["kind_unknown"])
            elif entry is not None and holder["id"] == entry.get("chat_id"):
                head = self.strings["st_yours"]
            else:
                head = self.strings["st_taken"].format(
                    self.strings[f"kind_{holder['kind']}"]
                )

        blocks = [self.strings["head"].format(username), head]

        if holder is not None:
            blocks.append(self._owner_block(holder))
            blocks.append(self._ttl_block(holder))

        if entry is not None:
            blocks.append(self._queue_block(entry))

        return "\n\n".join(block for block in blocks if block)

    def _owner_block(self, holder: dict) -> str:
        rows = [
            (self.strings["lbl_name"], utils.escape_html(holder.get("name", "—"))),
            (self.strings["lbl_id"], f"<code>{holder.get('id', 0)}</code>"),
        ]

        if holder["kind"] in {"user", "bot"}:
            rows.append((self.strings["lbl_seen"], self._seen(holder)))

        if holder.get("premium"):
            rows.append((self.strings["lbl_premium"], self.strings["yes"]))

        title = self.strings["owner_title"].format(
            self.strings[f"kind_{holder['kind']}"].split(" ", 1)[0]
        )

        return self._block(title, rows)

    def _ttl_block(self, holder: dict) -> str:
        """Когда Telegram удалит аккаунт за простой — и отпустит юзернейм."""
        verdict = self._deadline(holder)

        if verdict is None:
            return self.strings["ttl_none"]

        deadline, sureness = verdict
        months = self.config["ttl_months"]
        date = self._date(deadline)

        if sureness != "exact":
            date = self.strings["dl_approx"].format(date)

        block = self._block(
            self.strings["ttl_title"],
            [
                (
                    self.strings["lbl_limit"],
                    self.strings["val_limit_max" if months == 24 else "val_limit"].format(months),
                ),
                (self.strings["lbl_deadline"], date),
                (self.strings["lbl_free_in"], self._frees(holder)),
            ],
        )

        return f"{block}\n\n{self.strings['ttl_note'].format(months)}"

    def _queue_block(self, entry: dict) -> str:
        rows = [
            (self.strings["lbl_added"], self._date(entry.get("added", 0), full=True)),
            (self.strings["lbl_checks"], entry.get("checks", 0)),
        ]

        if entry.get("state") != "done":
            rows.append((self.strings["lbl_next"], self._when(entry.get("next_check", 0))))

        return self._block(self.strings["queue_title"], rows)

    def _card_markup(self, username: str) -> list:
        entry = self._queue().get(username)
        queued = entry is not None
        rows = []

        if entry is not None and entry.get("state") == "done":
            rows.append([{"text": self.strings["btn_open"], "url": f"https://t.me/{username}"}])
        elif entry is not None and entry.get("state") == "free":
            rows.append([{"text": self.strings["btn_take"], "callback": self._take_now, "args": (username,)}])

        rows.append(
            [
                {
                    "text": self.strings["btn_unwatch" if queued else "btn_watch"],
                    "callback": self._unwatch if queued else self._watch,
                    "args": (username,),
                },
                {"text": self.strings["btn_refresh"], "callback": self._open, "args": (username,)},
            ]
        )
        rows.append(
            [
                {"text": self.strings["btn_menu"], "callback": self._to_menu},
                {"text": self.strings["btn_close"], "callback": self._close},
            ]
        )

        return rows

    # ------------------------------------------------------------------ #
    #  Меню очереди
    # ------------------------------------------------------------------ #
    def _menu(self) -> str:
        queue = self._queue()

        if not queue:
            return self.strings["menu_empty"].format(self._prefix)

        lines = [self._row(username, entry) for username, entry in self._sorted(queue)]
        soonest = min(
            (entry.get("next_check", 0) for entry in queue.values() if entry.get("state") != "done"),
            default=0,
        )
        foot = self.strings["menu_foot"].format(
            self._span(self.config["interval"] * 60),
            self._when(soonest),
        )

        return self.strings["menu"].format(len(queue), "\n".join(lines) + "\n\n" + foot)

    def _row(self, username: str, entry: dict) -> str:
        state = entry.get("state", "wait")

        if state == "done":
            return self.strings["row_done"].format(
                username, self._date(entry.get("done_at", 0), full=True)
            )

        if state == "error":
            return self.strings["row_error"].format(
                username, utils.escape_html(entry.get("note", "")) or "—"
            )

        if state in {"free", "fragment", "wait"}:
            return self.strings[f"row_{state}"].format(username)

        holder = entry.get("holder")
        frees = self._frees(holder)

        if frees is not None:
            hint = self.strings["row_free_in"].format(frees)
        elif holder is None:
            hint = self.strings["row_free_unknown"]
        else:
            hint = self.strings["row_free_never"]

        return self.strings["row_taken"].format(username, hint)

    def _menu_markup(self) -> list:
        queue = self._queue()
        rows = []
        pair = []

        for username, _ in self._sorted(queue):
            pair.append(
                {"text": f"@{username}", "callback": self._open, "args": (username,)}
            )

            if len(pair) == 2:
                rows.append(pair)
                pair = []

        if pair:
            rows.append(pair)

        if queue:
            rows.append(
                [
                    {"text": self.strings["btn_recheck"], "callback": self._sweep},
                    {"text": self.strings["btn_wipe"], "callback": self._wipe},
                ]
            )

        rows.append([{"text": self.strings["btn_close"], "callback": self._close}])

        return rows

    # ------------------------------------------------------------------ #
    #  Отчёт в созданном чате
    # ------------------------------------------------------------------ #
    def _report(self, username: str, entry: dict) -> str:
        added = entry.get("added", 0)
        freed = entry.get("freed_at", entry.get("done_at", 0))

        blocks = [
            self._block(
                self.strings["report_title"],
                [
                    (self.strings["lbl_since"], self._date(added, full=True)),
                    (self.strings["lbl_freed"], self._date(freed, full=True)),
                    (self.strings["lbl_waited"], self._span(max(freed - added, 0))),
                    (self.strings["lbl_checks"], entry.get("checks", 0)),
                ],
            )
        ]

        holder = entry.get("holder")

        if holder:
            rows = [
                (self.strings["lbl_kind"], self.strings[f"kind_{holder['kind']}"]),
                (self.strings["lbl_name"], utils.escape_html(holder.get("name", "—"))),
                (self.strings["lbl_id"], f"<code>{holder.get('id', 0)}</code>"),
            ]

            if holder["kind"] in {"user", "bot"}:
                rows.append((self.strings["lbl_seen"], self._seen(holder)))

            blocks.append(self._block(self.strings["prev_title"], rows))

        blocks.append(self.strings["report_foot"])

        return self.strings["report"].format(username, "\n\n".join(blocks))

    # ------------------------------------------------------------------ #
    #  Разбор ответов Telegram
    # ------------------------------------------------------------------ #
    def _holder(self, resolved) -> dict:
        """Из ответа resolveUsername сделать словарь про владельца."""
        for user in getattr(resolved, "users", None) or []:
            return {
                "kind": "bot" if getattr(user, "bot", False) else "user",
                "name": " ".join(
                    part
                    for part in (
                        getattr(user, "first_name", None),
                        getattr(user, "last_name", None),
                    )
                    if part
                )
                or "—",
                "id": user.id,
                "premium": bool(getattr(user, "premium", False)),
                **self._status(user),
            }

        for chat in getattr(resolved, "chats", None) or []:
            return {
                "kind": "channel" if getattr(chat, "broadcast", False) else "group",
                "name": getattr(chat, "title", None) or "—",
                "id": int(f"-100{chat.id}"),
                "premium": False,
                "seen": None,
                "seen_kind": "unknown",
            }

        return None

    def _status(self, user) -> dict:
        """Последняя активность: точная дата либо оценка по границе."""
        status = getattr(user, "status", None)
        now = int(time.time())

        if isinstance(status, types.UserStatusOnline):
            return {"seen": now, "seen_kind": "online"}

        if isinstance(status, types.UserStatusOffline):
            return {"seen": int(status.was_online.timestamp()), "seen_kind": "exact"}

        # Точную дату скрыли — Telegram отдаёт лишь вилку. Берём её дальний
        # край: это самый ранний момент, когда владелец мог заходить.
        if isinstance(status, types.UserStatusRecently):
            return {"seen": now - 3 * DAY, "seen_kind": "recently"}

        if isinstance(status, types.UserStatusLastWeek):
            return {"seen": now - 7 * DAY, "seen_kind": "week"}

        if isinstance(status, types.UserStatusLastMonth):
            return {"seen": now - 30 * DAY, "seen_kind": "month"}

        # Пусто — значит «давно»: месяц назад или раньше
        return {"seen": None, "seen_kind": "unknown"}

    def _deadline(self, holder: dict):
        """(момент удаления, насколько уверены) либо None, если не про аккаунт."""
        if not holder or holder.get("kind") != "user":
            return None

        ttl = TTL_DAYS.get(self.config["ttl_months"], 730) * DAY
        kind = holder.get("seen_kind", "unknown")
        seen = holder.get("seen")

        if kind == "unknown":
            # Заходил больше месяца назад, а насколько раньше — неизвестно.
            # Значит удалят не позже этого срока, а может и раньше.
            return int(time.time()) - 30 * DAY + ttl, "not_after"

        if seen is None:
            return None

        if kind in {"online", "exact"}:
            return seen + ttl, "exact"

        return seen + ttl, "not_before"

    def _frees(self, holder: dict):
        """Фраза «когда освободится» либо None, если юз держит не человек."""
        verdict = self._deadline(holder)

        if verdict is None:
            return None

        left = verdict[0] - time.time()

        if left <= 0:
            return self.strings["free_overdue"]

        days = math.ceil(left / DAY)

        return self.strings[f"free_{verdict[1]}"].format(
            days=f"{days} {self._plural(days, 'p_days')}"
        )

    def _seen(self, holder: dict) -> str:
        kind = holder.get("seen_kind", "unknown")
        seen = holder.get("seen")

        if kind != "exact" or seen is None:
            return self.strings[f"seen_{kind}"]

        return self.strings["seen_exact"].format(
            date=self._date(seen, full=True),
            ago=self._span(time.time() - seen),
        )

    # ------------------------------------------------------------------ #
    #  Мелочи
    # ------------------------------------------------------------------ #
    def _queue(self) -> dict:
        """Своя секция в базе: правки сохраняются сами."""
        return self.pointer("queue", {})

    @staticmethod
    def _blank() -> dict:
        now = int(time.time())
        return {
            "added": now,
            "state": "wait",
            "checks": 0,
            "last_check": 0,
            "next_check": 0,
            "holder": None,
            "note": "",
            "told": False,
        }

    @staticmethod
    def _sorted(queue: dict) -> list:
        order = {"free": 0, "wait": 1, "taken": 2, "fragment": 3, "error": 4, "done": 5}
        return sorted(
            queue.items(),
            key=lambda item: (order.get(item[1].get("state"), 9), item[0]),
        )

    @property
    def _prefix(self) -> str:
        return self.client.dispatcher.prefixes[0]

    @staticmethod
    def _clean(raw: str) -> str:
        """Из «https://t.me/durov?start=1» и «@Durov» сделать «durov»."""
        raw = (raw or "").strip().lower()

        for junk in ("https://", "http://", "t.me/", "telegram.me/", "@"):
            if raw.startswith(junk):
                raw = raw[len(junk):]

        return raw.split("?")[0].split("/")[0].strip()

    def _delay(self, entry: dict) -> int:
        """Как долго не трогать юзернейм до следующей проверки."""
        base = self.config["interval"] * 60

        if not self.config["deep_sleep"]:
            return base

        verdict = self._deadline(entry.get("holder"))

        if verdict is None:
            return min(base * 6, DAY)

        days = (verdict[0] - time.time()) / DAY

        if days > 30:
            return min(base * 12, DAY)

        if days > 7:
            return min(base * 3, DAY)

        return base

    @staticmethod
    def _reason(error) -> str:
        return str(getattr(error, "message", "") or error) or type(error).__name__

    @staticmethod
    def _rpc_is(error, marker: str) -> bool:
        """Опознать ошибку и по коду, и по имени класса — телетоны разные."""
        text = (
            f"{getattr(error, 'message', '') or ''} {type(error).__name__}"
        ).upper().replace("_", "")
        return marker.replace("_", "") in text

    def _block(self, title: str, rows: list) -> str:
        """Заголовок и строки, связанные деревом."""
        lines = [title]

        for index, (label, value) in enumerate(rows):
            tie = "└" if index == len(rows) - 1 else "├"
            lines.append(f"{tie} <b>{label}:</b> {value}")

        return "\n".join(lines)

    def _plural(self, count: int, key: str) -> str:
        """Слово в нужном числе: три формы для русского, две для английского."""
        forms = self.strings[key].split("|")

        if len(forms) < 3:
            return forms[0] if abs(count) == 1 else forms[-1]

        tail = abs(count) % 100

        if 11 <= tail <= 14:
            return forms[2]

        tail %= 10

        if tail == 1:
            return forms[0]

        if 2 <= tail <= 4:
            return forms[1]

        return forms[2]

    @staticmethod
    def _date(stamp, full: bool = False) -> str:
        if not stamp:
            return "—"

        return time.strftime(
            "%d.%m.%Y %H:%M" if full else "%d.%m.%Y",
            time.localtime(stamp),
        )

    def _span(self, seconds) -> str:
        """«233 дн. 17 ч.», «4 ч. 20 мин.», «40 сек.»"""
        seconds = max(int(seconds), 0)
        days, rest = divmod(seconds, DAY)
        hours, rest = divmod(rest, HOUR)
        minutes = rest // 60
        parts = []

        if days:
            parts.append(self.strings["u_day"].format(days))

        if hours:
            parts.append(self.strings["u_hour"].format(hours))

        if minutes and not days:
            parts.append(self.strings["u_min"].format(minutes))

        if not parts:
            return (
                self.strings["u_sec"].format(seconds)
                if seconds
                else self.strings["u_less"]
            )

        return " ".join(parts)

    def _when(self, stamp) -> str:
        left = (stamp or 0) - time.time()
        return self.strings["now"] if left <= 0 else self.strings["in_span"].format(self._span(left))

    async def _answer(self, message, text: str, username: str = None, menu: bool = False):
        """Ответить с кнопками, а если инлайн-бота нет — обычным текстом."""
        markup = self._menu_markup() if menu else (self._card_markup(username) if username else None)

        if markup and self.inline is not None and self.inline.init_complete:
            if await self.inline.form(text, message=message, reply_markup=markup):
                return

        await utils.answer(message, text)

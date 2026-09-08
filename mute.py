"""Мут в группе, который не обходится ботами-помощниками.

Обычный мут закрывает только отправку сообщений, и его обходят через
инлайн-ботов: сообщение шлёт сам замученный, а бот лишь подставляет текст.
Здесь мут выставляет все права разом, включая инлайн, — эту дыру закрывает
сам Telegram.

Второй способ обхода — когда бот постит текст от своего имени. Просят его
об этом в личке, а личку двух посторонних не видит никто: ни юзербот, ни
админ, ни владелец группы. Поэтому ловим не переписку, а следы в самом
сообщении — упоминание, ссылку на профиль, пересылку.

Если следов нет, остаётся единственный надёжный рычаг: не давать боту
писать в чате. Курьер, которому нечем передать, бесполезен — .botmute
затыкает конкретного бота, а lockdown встречает новых на входе.
"""

# meta developer: @soika
# meta description: Мут со всеми правами разом и охота на обход через ботов

import inspect
import logging
import re
import time

from telethon import errors
from telethon.tl import functions, types

from .. import loader, utils

logger = logging.getLogger(__name__)

#: Все запреты, какие бывают. Лишние отсеются по версии телетона
LOCKS = (
    "send_messages",
    "send_media",
    "send_stickers",
    "send_gifs",
    "send_games",
    "send_inline",
    "embed_links",
    "send_polls",
    "send_photos",
    "send_videos",
    "send_roundvideos",
    "send_audios",
    "send_voices",
    "send_docs",
    "send_plain",
)

#: Сколько это в секундах
SPANS = {
    "s": 1, "с": 1,
    "m": 60, "м": 60,
    "h": 3600, "ч": 3600,
    "d": 86400, "д": 86400,
    "w": 604800, "н": 604800,
}

SPAN = re.compile(r"^(\d+)\s*([a-zа-я]*)$", re.IGNORECASE)


@loader.tds
class МутМод(loader.Module):
    """Мут, который не обойти через ботов"""

    strings = {
        "name": "Мут",
        # ── мут ─────────────────────────────────────────────────────────
        "usage": (
            "🔇 <b>Мут</b>\n\n"
            "<code>{0}mute</code> ответом — навсегда\n"
            "<code>{0}mute 30м шум</code> ответом — на полчаса с причиной\n"
            "<code>{0}mute @ник 2ч</code> — по нику\n"
            "<code>{0}unmute</code> ответом или по нику — снять\n"
            "<code>{0}mutes</code> — кто сейчас замучен"
        ),
        "no_target": (
            "🚫 <b>Кого мутить?</b>\n\n"
            "<i>Ответь на сообщение или назови</i> <code>@ник</code>"
        ),
        "not_group": "🚫 <b>Мут работает только в группах</b>",
        "not_admin": (
            "🚫 <b>Не хватает прав</b>\n\n"
            "<i>Нужны «Блокировка участников» и «Удаление сообщений».</i>"
        ),
        "self_mute": "🙃 <b>Себя мутить не будем</b>",
        "muted": "🔇 <b>{}</b> <b>замучен</b>\n{}",
        "unmuted": "🔊 <b>{}</b> <b>размучен</b>",
        "was_not": "🤷 <b>{}</b> <b>и не был замучен</b>",
        "lbl_until": "До",
        "lbl_why": "Причина",
        "forever": "навсегда",
        "failed": "⚠️ <b>Telegram отказал:</b> <code>{}</code>",
        # ── список ──────────────────────────────────────────────────────
        "list": "🔇 <b>Замучены здесь: {}</b>\n\n{}",
        "list_empty": "🔊 <b>Здесь никто не замучен</b>",
        "row": "▫️ {} · {}",
        "row_tries": "▫️ {} · {} · <b>обходов: {}</b>",
        # ── обход ───────────────────────────────────────────────────────
        "caught": (
            "🚫 <b>Обход мута</b>\n\n"
            "├ <b>Кто:</b> {}\n"
            "├ <b>Через:</b> {}\n"
            "└ <b>Как:</b> {}"
        ),
        "via_inline": "инлайн",
        "via_mention": "упоминание в сообщении бота",
        "via_forward": "пересылка",
        "via_self": "напрямую",
        "bots": "🤖 <b>Боты, замеченные на обходе</b>\n\n{}",
        "bots_empty": (
            "🤖 <b>Никто не попадался</b>\n\n"
            "<i>Либо обходить не пробовали, либо бот не оставляет следов —"
            " тогда его видно только глазами.</i>"
        ),
        "bot_row": "▫️ {} · <b>{}</b>",
        "bot_muted": (
            "🤖 <b>{}</b> <b>больше не пишет здесь</b>\n\n"
            "<i>Из чата не выгнан — просто лишён права слова. Вернуть:</i>"
            " <code>{}unmute</code> <i>ответом на него.</i>"
        ),
        "bot_free": "🤖 <b>{}</b> <b>снова может писать</b>",
        "not_a_bot": (
            "🚫 <b>Это не бот</b>\n\n"
            "<i>Для людей есть</i> <code>{}mute</code>"
        ),
        "bot_target": (
            "🚫 <b>Какого бота заткнуть?</b>\n\n"
            "<i>Ответь на его сообщение или назови</i> <code>@ник</code>"
        ),
        "locked": (
            "🔒 <b>Новый бот встречен на входе</b>\n\n"
            "├ <b>Кто:</b> {}\n"
            "└ <b>Что сделано:</b> лишён права писать\n\n"
            "<i>Разрешить:</i> <code>{}unmute</code> <i>ответом или добавь его"
            " в белый список.</i>"
        ),
        "bot_kicked": "🚪 <b>{}</b> <b>выставлен из чата</b>",
        "bot_failed": "⚠️ <b>{}</b> <b>не выставился:</b> <code>{}</code>",
        # ── настройки ───────────────────────────────────────────────────
        "cfg": "⚙️ <b>Мут</b>\n\n{}",
        "lbl_guard": "Ловлю обход",
        "lbl_marks": "По следам автора",
        "lbl_notice": "Пишу в чат",
        "lbl_lock": "Новые боты",
        "lock_on": "сразу без слова",
        "lock_off": "как есть",
        "lbl_allowed": "Белый список",
        "none": "<i>пуст</i>",
        "lbl_kick": "Выгонять бота",
        "lbl_after": "После попыток",
        "kick_off": "не выгонять",
        "on": "да",
        "off": "нет",
        # ── кнопки ──────────────────────────────────────────────────────
        "btn_guard": "🛡 Ловля: {}",
        "btn_marks": "🔍 По следам: {}",
        "btn_notice": "💬 Писать в чат: {}",
        "btn_lock": "🔒 Новые боты: {}",
        "btn_kick": "🚪 Выгонять бота: {}",
        "btn_unmute": "🔊 {}",
        "btn_close": "✖️ Закрыть",
        "cb_saved": "✅ Готово",
        "cb_unmuted": "🔊 Размучен",
        # ── единицы ─────────────────────────────────────────────────────
        "u_day": "{} дн.",
        "u_hour": "{} ч.",
        "u_min": "{} мин.",
        "u_sec": "{} сек.",
    }

    strings_en = {
        "usage": (
            "🔇 <b>Mute</b>\n\n"
            "<code>{0}mute</code> as a reply — forever\n"
            "<code>{0}mute 30m noise</code> as a reply — half an hour, with a reason\n"
            "<code>{0}mute @name 2h</code> — by username\n"
            "<code>{0}unmute</code> as a reply or by username — lift it\n"
            "<code>{0}mutes</code> — who is muted now"
        ),
        "no_target": (
            "🚫 <b>Mute whom?</b>\n\n"
            "<i>Reply to a message or name a</i> <code>@username</code>"
        ),
        "not_group": "🚫 <b>Mute only works in groups</b>",
        "not_admin": (
            "🚫 <b>Not enough rights</b>\n\n"
            "<i>“Ban users” and “Delete messages” are needed.</i>"
        ),
        "self_mute": "🙃 <b>Not muting yourself</b>",
        "muted": "🔇 <b>{}</b> <b>is muted</b>\n{}",
        "unmuted": "🔊 <b>{}</b> <b>is unmuted</b>",
        "was_not": "🤷 <b>{}</b> <b>was not muted</b>",
        "lbl_until": "Until",
        "lbl_why": "Reason",
        "forever": "forever",
        "failed": "⚠️ <b>Telegram refused:</b> <code>{}</code>",
        "list": "🔇 <b>Muted here: {}</b>\n\n{}",
        "list_empty": "🔊 <b>Nobody is muted here</b>",
        "row": "▫️ {} · {}",
        "row_tries": "▫️ {} · {} · <b>bypasses: {}</b>",
        "caught": (
            "🚫 <b>Mute bypass</b>\n\n"
            "├ <b>Who:</b> {}\n"
            "├ <b>Through:</b> {}\n"
            "└ <b>How:</b> {}"
        ),
        "via_inline": "inline",
        "via_mention": "mention in a bot message",
        "via_forward": "forward",
        "via_self": "directly",
        "bots": "🤖 <b>Bots caught helping</b>\n\n{}",
        "bots_empty": (
            "🤖 <b>Nobody was caught</b>\n\n"
            "<i>Either nobody tried, or the bot leaves no trace — then only"
            " your own eyes will spot it.</i>"
        ),
        "bot_row": "▫️ {} · <b>{}</b>",
        "bot_muted": (
            "🤖 <b>{}</b> <b>cannot post here any more</b>\n\n"
            "<i>Not removed — just denied the right to speak. To undo:</i>"
            " <code>{}unmute</code> <i>as a reply to it.</i>"
        ),
        "bot_free": "🤖 <b>{}</b> <b>can post again</b>",
        "not_a_bot": (
            "🚫 <b>That is not a bot</b>\n\n"
            "<i>For people there is</i> <code>{}mute</code>"
        ),
        "bot_target": (
            "🚫 <b>Which bot to silence?</b>\n\n"
            "<i>Reply to its message or name a</i> <code>@username</code>"
        ),
        "locked": (
            "🔒 <b>A new bot was met at the door</b>\n\n"
            "├ <b>Who:</b> {}\n"
            "└ <b>Done:</b> denied the right to post\n\n"
            "<i>To allow:</i> <code>{}unmute</code> <i>as a reply, or add it"
            " to the allow list.</i>"
        ),
        "bot_kicked": "🚪 <b>{}</b> <b>was removed from the chat</b>",
        "bot_failed": "⚠️ <b>{}</b> <b>was not removed:</b> <code>{}</code>",
        "cfg": "⚙️ <b>Mute</b>\n\n{}",
        "lbl_guard": "Catching bypass",
        "lbl_marks": "By author traces",
        "lbl_notice": "Posting in chat",
        "lbl_lock": "New bots",
        "lock_on": "silenced at once",
        "lock_off": "left alone",
        "lbl_allowed": "Allow list",
        "none": "<i>empty</i>",
        "lbl_kick": "Remove the bot",
        "lbl_after": "After tries",
        "kick_off": "do not remove",
        "on": "yes",
        "off": "no",
        "btn_guard": "🛡 Catching: {}",
        "btn_marks": "🔍 Traces: {}",
        "btn_notice": "💬 Post in chat: {}",
        "btn_lock": "🔒 New bots: {}",
        "btn_kick": "🚪 Remove bot: {}",
        "btn_unmute": "🔊 {}",
        "btn_close": "✖️ Close",
        "cb_saved": "✅ Done",
        "cb_unmuted": "🔊 Unmuted",
        "u_day": "{}d",
        "u_hour": "{}h",
        "u_min": "{}m",
        "u_sec": "{}s",
    }

    config = loader.ModuleConfig(
        loader.ConfigValue(
            "guard",
            True,
            "Убирать сообщения, которыми обходят мут",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "marks",
            True,
            (
                "Считать обходом сообщение бота, где замученный назван:"
                " упоминанием, ссылкой на профиль или пересылкой"
            ),
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "notice",
            True,
            "Писать в чат, что обход пойман",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "lockdown",
            False,
            (
                "Встречать новых ботов на входе: сразу лишать права писать."
                " Против ботов-курьеров это единственное, что работает наверняка"
            ),
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "allowed",
            [],
            "Боты, которых lockdown не трогает: @ники или id",
            validator=loader.validators.Series(),
        ),
        loader.ConfigValue(
            "kick_bots",
            False,
            "Выставлять бота из чата, если он раз за разом помогает обходить",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "patience",
            5,
            "Сколько попыток бота стерпеть перед тем, как выставить",
            validator=loader.validators.Integer(minimum=1, maximum=100),
        ),
    )

    # ------------------------------------------------------------------ #
    #  Команды
    # ------------------------------------------------------------------ #
    @loader.command(aliases=["мут"])
    async def mutecmd(self, message):
        """<реплай/@ник> [время] [причина] — замутить со всеми правами разом"""
        if not getattr(message, "is_group", False) and not getattr(message, "is_channel", False):
            await utils.answer(message, self.strings["not_group"])
            return

        user = await self._target(message)

        if user is None:
            # Пусто со всех сторон — человек просто не знает команду
            blank = not (utils.get_args_raw(message) or "").strip() and (
                await message.get_reply_message() is None
            )
            await utils.answer(
                message,
                self.strings["usage"].format(self._prefix)
                if blank
                else self.strings["no_target"],
            )
            return

        if user.id == self.tg_id:
            await utils.answer(message, self.strings["self_mute"])
            return

        span, why = self._parse(utils.get_args_raw(message) or "")
        until = int(time.time()) + span if span else 0

        try:
            await self.client(
                functions.channels.EditBannedRequest(
                    channel=message.peer_id,
                    participant=user,
                    banned_rights=self._rights(until),
                )
            )
        except errors.ChatAdminRequiredError:
            await utils.answer(message, self.strings["not_admin"])
            return
        except Exception as error:
            logger.exception("Мут не встал")
            await utils.answer(
                message, self.strings["failed"].format(utils.escape_html(str(error)))
            )
            return

        self._muted(message.chat_id)[str(user.id)] = {
            "until": until,
            "why": why,
            "name": self._name(user),
            "nick": (getattr(user, "username", None) or "").lower(),
            "at": int(time.time()),
            "tries": 0,
        }

        rows = [(self.strings["lbl_until"], self._until(until))]

        if why:
            rows.append((self.strings["lbl_why"], utils.escape_html(why)))

        await utils.answer(
            message,
            self.strings["muted"].format(self._link(user), self._block(rows)),
        )

    @loader.command(aliases=["размут", "анмут"])
    async def unmutecmd(self, message):
        """<реплай/@ник> — снять мут и вернуть все права"""
        user = await self._target(message)

        if user is None:
            await utils.answer(message, self.strings["no_target"])
            return

        known = self._muted(message.chat_id)
        had = known.pop(str(user.id), None)

        # Заткнутый бот живёт в своём списке — снимаем пометку и там
        note = self._bots(message.chat_id).get(str(user.id))
        was_bot = bool(note and note.get("silenced"))

        if note:
            note["silenced"] = False
            note["kicked"] = False

        try:
            await self.client(
                functions.channels.EditBannedRequest(
                    channel=message.peer_id,
                    participant=user,
                    banned_rights=types.ChatBannedRights(until_date=0),
                )
            )
        except Exception as error:
            logger.exception("Мут не снялся")
            await utils.answer(
                message, self.strings["failed"].format(utils.escape_html(str(error)))
            )
            return

        if was_bot:
            await utils.answer(message, self.strings["bot_free"].format(self._link(user)))
            return

        await utils.answer(
            message,
            self.strings["unmuted" if had else "was_not"].format(self._link(user)),
        )

    @loader.command(aliases=["муты"])
    async def mutescmd(self, message):
        """— кто замучен в этом чате и сколько раз пробовали обойти"""
        known = self._alive(message.chat_id)

        if not known:
            await utils.answer(message, self.strings["list_empty"])
            return

        lines, buttons = [], []

        for uid, note in known.items():
            shown = note.get("name") or uid
            key = "row_tries" if note.get("tries") else "row"
            lines.append(
                self.strings[key].format(
                    utils.escape_html(shown), self._until(note.get("until", 0)), note.get("tries")
                )
            )
            buttons.append({
                "text": self.strings["btn_unmute"].format(shown[:20]),
                "callback": self._unmute,
                "args": (message.chat_id, uid),
            })

        rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
        rows.append([{"text": self.strings["btn_close"], "callback": self._close}])
        await self._answer(
            message, self.strings["list"].format(len(known), "\n".join(lines)), rows
        )

    @loader.command(aliases=["ботмут"])
    async def botmutecmd(self, message):
        """<реплай/@бот> — лишить бота права писать в этом чате"""
        user = await self._target(message)

        if user is None:
            await utils.answer(message, self.strings["bot_target"].format(self._prefix))
            return

        if not getattr(user, "bot", False):
            await utils.answer(message, self.strings["not_a_bot"].format(self._prefix))
            return

        if not await self._silence(message.peer_id, user):
            await utils.answer(message, self.strings["not_admin"])
            return

        note = self._bots(message.chat_id).setdefault(
            str(user.id), {"tries": 0, "name": "", "kicked": False}
        )
        note["name"] = self._name(user)
        note["id"] = int(user.id)
        note["silenced"] = True

        await utils.answer(
            message,
            self.strings["bot_muted"].format(self._link(user), self._prefix),
        )

    @loader.command(aliases=["мутботы"])
    async def mutebotscmd(self, message):
        """— боты, через которых обходили мут в этом чате"""
        seen = self._bots(message.chat_id)

        if not seen:
            await utils.answer(message, self.strings["bots_empty"])
            return

        lines = [
            self.strings["bot_row"].format(utils.escape_html(note.get("name") or key), note.get("tries", 0))
            for key, note in sorted(seen.items(), key=lambda item: -item[1].get("tries", 0))
        ]
        await utils.answer(message, self.strings["bots"].format("\n".join(lines)))

    @loader.command(aliases=["мутнастройки"])
    async def mutecfgcmd(self, message):
        """— настройки: ловля обхода, следы автора, выгонять ли ботов"""
        await self._answer(message, self._card(), self._markup())

    # ------------------------------------------------------------------ #
    #  Охота на обход
    # ------------------------------------------------------------------ #
    @loader.watcher(only_groups=True, no_commands=True)
    async def watcher(self, message):
        """Сообщение в группе: не пришёл ли новый бот и не обходит ли кто мут."""
        if getattr(message, "action", None) is not None:
            if self.config["lockdown"]:
                utils.spawn(self._doorman(message))

            return

        if not self.config["guard"]:
            return

        known = self._muted(getattr(message, "chat_id", 0))

        if not known:
            return

        caught = self._suspect(message, known)

        if caught is None:
            return

        uid, why = caught
        note = known.get(uid) or {}

        if note.get("until") and note["until"] < time.time():
            known.pop(uid, None)
            return

        note["tries"] = note.get("tries", 0) + 1
        utils.spawn(self._punish(message, uid, note, why))

    def _suspect(self, message, known: dict):
        """Кто и как обходит мут. None — если всё чисто."""
        sender = str(getattr(message, "sender_id", "") or "")

        # Сам замученный: инлайн шлёт он же, только чужими руками
        if sender in known:
            return sender, "inline" if getattr(message, "via_bot_id", None) else "self"

        if not self.config["marks"]:
            return None

        # Дальше — сообщение бота, где замученный оставил след
        forward = getattr(message, "fwd_from", None)
        origin = getattr(getattr(forward, "from_id", None), "user_id", None)

        if origin and str(origin) in known:
            return str(origin), "forward"

        for entity in getattr(message, "entities", None) or []:
            marked = getattr(entity, "user_id", None)

            if marked and str(marked) in known:
                return str(marked), "mention"

        text = (getattr(message, "raw_text", None) or "").lower()

        for uid, note in known.items():
            nick = note.get("nick")

            if nick and f"@{nick}" in text:
                return uid, "mention"

            if f"tg://user?id={uid}" in text:
                return uid, "mention"

        return None

    async def _punish(self, message, uid: str, note: dict, why: str) -> None:
        """Убрать сообщение и, если просили, заняться ботом."""
        try:
            await message.delete()
        except Exception:
            logger.info("Сообщение обхода удалить не вышло — нет прав?")
            return

        helper = None

        if str(getattr(message, "sender_id", "")) != uid:
            helper = await self._count_bot(message)

        if self.config["notice"]:
            await self._shout(message, uid, note, why, helper)

        if helper and self.config["kick_bots"] and helper["tries"] >= self.config["patience"]:
            await self._kick(message, helper)

    async def _doorman(self, message) -> None:
        """Нового бота встречаем на входе: курьеру нечем передавать."""
        added = getattr(getattr(message, "action", None), "users", None) or []

        for uid in added:
            try:
                who = await self.client.get_entity(uid)
            except Exception:
                logger.info("Пришедшего %s опознать не вышло", uid)
                continue

            if not getattr(who, "bot", False) or self._welcome(who):
                continue

            if not await self._silence(message.peer_id, who):
                continue

            note = self._bots(message.chat_id).setdefault(
                str(who.id), {"tries": 0, "name": "", "kicked": False}
            )
            note.update({"name": self._name(who), "id": int(who.id), "silenced": True})

            try:
                await self.client.send_message(
                    message.peer_id,
                    self.strings["locked"].format(self._link(who), self._prefix),
                )
            except Exception:
                logger.info("Про встреченного бота написать не вышло")

    def _welcome(self, who) -> bool:
        """Есть ли бот в белом списке."""
        nick = (getattr(who, "username", None) or "").lower()

        for item in self.config["allowed"] or []:
            mark = str(item).lstrip("@").lower()

            if mark and mark in {nick, str(who.id)}:
                return True

        return False

    async def _silence(self, chat, who) -> bool:
        """Лишить права писать, не выгоняя. False — если не хватило прав."""
        try:
            await self.client(
                functions.channels.EditBannedRequest(
                    channel=chat,
                    participant=who,
                    banned_rights=self._rights(0),
                )
            )
        except errors.ChatAdminRequiredError:
            return False
        except Exception:
            logger.exception("Бота заткнуть не вышло")
            return False

        return True

    async def _count_bot(self, message):
        """Запомнить бота, через которого прошёл обход."""
        sender = getattr(message, "sender_id", None)

        if not sender:
            return None

        seen = self._bots(getattr(message, "chat_id", 0))
        note = seen.setdefault(str(sender), {"tries": 0, "name": "", "kicked": False})
        note["tries"] += 1

        if not note["name"]:
            try:
                bot = await message.get_sender()
                note["name"] = self._name(bot)
            except Exception:
                note["name"] = str(sender)

        note["id"] = int(sender)

        return note

    async def _shout(self, message, uid: str, note: dict, why: str, helper) -> None:
        try:
            await self.client.send_message(
                message.peer_id,
                self.strings["caught"].format(
                    utils.escape_html(note.get("name") or uid),
                    utils.escape_html((helper or {}).get("name") or "—"),
                    self.strings[f"via_{why}"],
                ),
            )
        except Exception:
            logger.info("Написать про обход не вышло")

    async def _kick(self, message, helper) -> None:
        if helper.get("kicked"):
            return

        helper["kicked"] = True

        try:
            await self.client(
                functions.channels.EditBannedRequest(
                    channel=message.peer_id,
                    participant=helper["id"],
                    banned_rights=types.ChatBannedRights(
                        until_date=0, view_messages=True
                    ),
                )
            )
            await self.client.send_message(
                message.peer_id,
                self.strings["bot_kicked"].format(utils.escape_html(helper.get("name") or "—")),
            )
        except Exception as error:
            helper["kicked"] = False
            logger.exception("Бота выставить не вышло")

            try:
                await self.client.send_message(
                    message.peer_id,
                    self.strings["bot_failed"].format(
                        utils.escape_html(helper.get("name") or "—"),
                        utils.escape_html(str(error)),
                    ),
                )
            except Exception:
                logger.info("И сказать об этом не вышло")

    # ------------------------------------------------------------------ #
    #  Права и время
    # ------------------------------------------------------------------ #
    @staticmethod
    def _rights(until: int):
        """Запреты разом — включая инлайн, через который и обходят.

        Набор полей у ChatBannedRights от версии к версии разный, поэтому
        отдаём только то, что телетон понимает: лишнее молча отсеется.
        """
        wanted = {"until_date": until}
        wanted.update({lock: True for lock in LOCKS})

        try:
            known = inspect.signature(types.ChatBannedRights.__init__).parameters
            wanted = {key: value for key, value in wanted.items() if key in known}
        except (TypeError, ValueError):
            logger.info("Состав прав не прочитался, шлём как есть")

        return types.ChatBannedRights(**wanted)

    @staticmethod
    def _parse(args: str):
        """Из «30м шум» сделать (1800, «шум»)."""
        words = args.split()
        span = 0

        for index, word in enumerate(words):
            found = SPAN.match(word)

            if not found:
                continue

            amount, unit = found.groups()
            step = SPANS.get((unit or "m")[:1].lower())

            if step is None:
                continue

            span = int(amount) * step
            words.pop(index)
            break

        return span, " ".join(words).strip()

    def _until(self, until: int) -> str:
        if not until:
            return self.strings["forever"]

        left = until - time.time()

        return self._span(left) if left > 0 else self.strings["forever"]

    def _span(self, seconds) -> str:
        seconds = max(int(seconds or 0), 0)
        days, rest = divmod(seconds, 86400)
        hours, rest = divmod(rest, 3600)
        minutes, secs = divmod(rest, 60)
        parts = []

        if days:
            parts.append(self.strings["u_day"].format(days))

        if hours:
            parts.append(self.strings["u_hour"].format(hours))

        if minutes and not days:
            parts.append(self.strings["u_min"].format(minutes))

        if secs and not (days or hours or minutes):
            parts.append(self.strings["u_sec"].format(secs))

        return " ".join(parts) or self.strings["u_sec"].format(0)

    # ------------------------------------------------------------------ #
    #  Настройки
    # ------------------------------------------------------------------ #
    def _card(self) -> str:
        rows = [
            (self.strings["lbl_guard"], self.strings["on" if self.config["guard"] else "off"]),
            (self.strings["lbl_marks"], self.strings["on" if self.config["marks"] else "off"]),
            (self.strings["lbl_notice"], self.strings["on" if self.config["notice"] else "off"]),
            (
                self.strings["lbl_lock"],
                self.strings["lock_on" if self.config["lockdown"] else "lock_off"],
            ),
            (
                self.strings["lbl_allowed"],
                utils.escape_html(", ".join(str(item) for item in self.config["allowed"]))
                or self.strings["none"],
            ),
            (
                self.strings["lbl_kick"],
                self.strings["on"] if self.config["kick_bots"] else self.strings["kick_off"],
            ),
            (self.strings["lbl_after"], self.config["patience"]),
        ]

        return self.strings["cfg"].format(self._block(rows))

    def _markup(self) -> list:
        return [
            [
                {
                    "text": self.strings["btn_guard"].format(
                        self.strings["on" if self.config["guard"] else "off"]
                    ),
                    "callback": self._toggle,
                    "args": ("guard",),
                },
                {
                    "text": self.strings["btn_marks"].format(
                        self.strings["on" if self.config["marks"] else "off"]
                    ),
                    "callback": self._toggle,
                    "args": ("marks",),
                },
            ],
            [
                {
                    "text": self.strings["btn_lock"].format(
                        self.strings["on" if self.config["lockdown"] else "off"]
                    ),
                    "callback": self._toggle,
                    "args": ("lockdown",),
                },
                {
                    "text": self.strings["btn_notice"].format(
                        self.strings["on" if self.config["notice"] else "off"]
                    ),
                    "callback": self._toggle,
                    "args": ("notice",),
                },
                {
                    "text": self.strings["btn_kick"].format(
                        self.strings["on" if self.config["kick_bots"] else "off"]
                    ),
                    "callback": self._toggle,
                    "args": ("kick_bots",),
                },
            ],
            [{"text": self.strings["btn_close"], "callback": self._close}],
        ]

    async def _toggle(self, call, key: str) -> None:
        self.config[key] = not self.config[key]
        await call.answer(self.strings["cb_saved"])
        await call.edit(self._card(), reply_markup=self._markup())

    async def _unmute(self, call, chat_id: int, uid: str) -> None:
        self._muted(chat_id).pop(uid, None)

        try:
            await self.client(
                functions.channels.EditBannedRequest(
                    channel=chat_id,
                    participant=int(uid),
                    banned_rights=types.ChatBannedRights(until_date=0),
                )
            )
        except Exception:
            logger.exception("Мут не снялся по кнопке")

        await call.answer(self.strings["cb_unmuted"])
        await call.delete()

    async def _close(self, call) -> None:
        await call.delete()

    # ------------------------------------------------------------------ #
    #  Мелочи
    # ------------------------------------------------------------------ #
    def _muted(self, chat_id) -> dict:
        """Замученные этого чата. Своя запись нужна вотчеру без запросов."""
        return self.pointer("muted", {}).setdefault(str(chat_id), {})

    def _alive(self, chat_id) -> dict:
        """То же, но без тех, у кого срок уже вышел."""
        known = self._muted(chat_id)
        now = time.time()

        for uid in [
            key
            for key, note in known.items()
            if note.get("until") and note["until"] < now
        ]:
            known.pop(uid)

        return known

    def _bots(self, chat_id) -> dict:
        return self.pointer("bots", {}).setdefault(str(chat_id), {})

    async def _target(self, message):
        try:
            return await utils.get_target_user(message)
        except Exception:
            logger.info("Кого мутить — не разобрались")
            return None

    @staticmethod
    def _name(user) -> str:
        return (
            getattr(user, "title", None)
            or " ".join(
                part
                for part in (
                    getattr(user, "first_name", None),
                    getattr(user, "last_name", None),
                )
                if part
            )
            or str(getattr(user, "id", "—"))
        )

    def _link(self, user) -> str:
        return (
            f'<a href="tg://user?id={user.id}">'
            f"{utils.escape_html(self._name(user))}</a>"
        )

    def _block(self, rows: list) -> str:
        lines = []

        for index, (label, value) in enumerate(rows):
            tie = "└" if index == len(rows) - 1 else "├"
            lines.append(f"{tie} <b>{label}:</b> {value}")

        return "\n".join(lines)

    async def _answer(self, message, text: str, markup: list):
        if self.inline is not None and self.inline.init_complete:
            if await self.inline.form(text, message=message, reply_markup=markup):
                return

        await utils.answer(message, text)

    @property
    def _prefix(self) -> str:
        return self.client.dispatcher.prefixes[0]

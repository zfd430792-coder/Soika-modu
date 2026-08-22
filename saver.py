"""Ловит одноразовые медиа и записывает удалённые сообщения.

Одноразовое фото, голосовое или кружок уходит в отдельный канал вместе с
карточкой: от кого, откуда, когда. Удалённые сообщения — во второй канал:
кто написал, что там было и сколько оно прожило.
"""

# meta developer: @soika
# meta description: Одноразовые медиа и удалённые сообщения складываются в два личных канала с карточкой отправителя

import asyncio
import io
import logging
import re
import time

from telethon import errors
from telethon.tl import functions, types

from .. import loader, utils

logger = logging.getLogger(__name__)

#: Одноразовое медиа помечено таким ttl — «до первого просмотра»
ONE_VIEW = 0x7FFFFFFF

#: Как называются файлы, которые мы перезаливаем к себе
FILENAMES = {
    "photo": "photo.jpg",
    "voice": "voice.ogg",
    "round": "round.mp4",
    "video": "video.mp4",
}


@loader.tds
class ХранительМод(loader.Module):
    """Одноразовые медиа и удалённые сообщения в личных каналах"""

    strings = {
        "name": "Хранитель",
        # ── каналы-хранилища ────────────────────────────────────────────
        "box_media": "🔥 Одноразовое",
        "box_trash": "🗑 Удалённое",
        "about_media": "Одноразовые медиа, пойманные Сойкой",
        "about_trash": "Удалённые сообщения, записанные Сойкой",
        "hello_media": (
            "🔥 <b>Одноразовое</b>\n\n"
            "Сюда падают фото, голосовые и кружки, присланные «на один"
            " просмотр». Под каждым — карточка: от кого, откуда и когда.\n\n"
            "<i>Канал личный: кроме тебя его никто не видит.</i>"
        ),
        "hello_trash": (
            "🗑 <b>Удалённое</b>\n\n"
            "Сюда попадают сообщения, которые удалили из отслеживаемых чатов:"
            " автор, время, текст и медиа, если его удалось вытащить.\n\n"
            "<i>Канал личный: кроме тебя его никто не видит.</i>"
        ),
        # ── карточка одноразового ───────────────────────────────────────
        "caught": "🔥 <b>Одноразовое</b> · {}",
        "caught_lost": (
            "🔥 <b>Одноразовое</b> · {}\n\n"
            "<i>Файл забрать не вышло — Telegram успел его стереть.</i>\n\n{}"
        ),
        "kind_photo": "🖼 фото",
        "kind_voice": "🎤 голосовое",
        "kind_round": "⭕️ кружок",
        "kind_video": "🎬 видео",
        # ── карточка удалённого ─────────────────────────────────────────
        "erased": "🗑 <b>Удалено</b>",
        "erased_media": "🗑 <b>Удалено</b> · {}",
        "lbl_from": "От",
        "lbl_author": "Автор",
        "lbl_chat": "Чат",
        "lbl_sent": "Прислано",
        "lbl_gone": "Удалено",
        "lbl_lived": "Прожило",
        "lbl_dur": "Длительность",
        "lbl_size": "Размер",
        "lbl_ttl": "Срок",
        "ttl_once": "один просмотр",
        "ttl_secs": "{} сек. после просмотра",
        "text_title": "💬 <b>Текст</b>",
        "text_none": "<i>Без текста</i>",
        "was_media": "📎 <b>Было вложено:</b> {}",
        "was_media_lost": "📎 <b>Было вложено:</b> {} — файл уже недоступен",
        "who_erased": (
            "<i>Кто именно нажал «удалить», Telegram не сообщает. В личке это"
            " ты или собеседник, в группе — ещё и админы.</i>"
        ),
        "who_erased_pm": (
            "<i>Кто именно нажал «удалить», Telegram не сообщает — в этой"
            " личке это либо ты, либо собеседник.</i>"
        ),
        "chat_pm": "личка",
        "chat_saved": "избранное",
        "unknown": "неизвестно",
        "me": "ты",
        # ── меню ────────────────────────────────────────────────────────
        "menu": "🗄 <b>Хранитель</b>\n\n{}",
        "block_media": "🔥 <b>Одноразовые медиа</b> — {}",
        "block_trash": "🗑 <b>Удалённые сообщения</b> — {}",
        "on": "включено",
        "off": "выключено",
        "lbl_catch": "Ловлю",
        "lbl_watch": "Смотрю",
        "lbl_box": "Канал",
        "lbl_count": "Поймано",
        "lbl_logged": "Записано",
        "box_none": "<i>ещё не заведён</i>",
        "watch_pm": "личку",
        "watch_groups": "группы",
        "watch_both": "личку и группы",
        "watch_none": "<i>ничего — включи хоть что-то</i>",
        "cache_line": "📦 <b>В памяти:</b> {} из {} сообщений",
        "ignored_line": "🙈 <b>Не трогаю чатов:</b> {}",
        "menu_note": (
            "<i>Одноразовое медиа ловится в момент прихода, до просмотра."
            " Удалённое берётся из памяти, поэтому переживает только то, что"
            " пришло после загрузки модуля.</i>"
        ),
        # ── команды ─────────────────────────────────────────────────────
        "made": "📦 <b>Каналы готовы</b>\n\n├ {}\n└ {}",
        "make_failed": "⚠️ <b>Не вышло завести каналы:</b> <code>{}</code>",
        "ignored": "🙈 <b>Этот чат больше не трогаю</b>",
        "unignored": "👀 <b>Снова слежу за этим чатом</b>",
        "ignore_pm_only": "🚫 <b>Тут нечего игнорировать</b>",
        # ── кнопки ──────────────────────────────────────────────────────
        "btn_media_on": "🔥 Медиа: вкл",
        "btn_media_off": "🔥 Медиа: выкл",
        "btn_trash_on": "🗑 Удаления: вкл",
        "btn_trash_off": "🗑 Удаления: выкл",
        "btn_box_media": "↗️ Одноразовое",
        "btn_box_trash": "↗️ Удалённое",
        "btn_make": "🧰 Завести каналы",
        "btn_refresh": "🔄 Обновить",
        "btn_close": "✖️ Закрыть",
        "cb_saved": "✅ Готово",
        "cb_working": "⏳ Секунду…",
        # ── единицы ─────────────────────────────────────────────────────
        "u_day": "{} дн.",
        "u_hour": "{} ч.",
        "u_min": "{} мин.",
        "u_sec": "{} сек.",
        "u_less": "меньше секунды",
    }

    strings_en = {
        "box_media": "🔥 One-time",
        "box_trash": "🗑 Deleted",
        "about_media": "One-time media caught by Soika",
        "about_trash": "Deleted messages logged by Soika",
        "hello_media": (
            "🔥 <b>One-time</b>\n\n"
            "Photos, voice messages and video notes sent as view-once land"
            " here, each with a card: who sent it, where and when.\n\n"
            "<i>The channel is private: nobody else sees it.</i>"
        ),
        "hello_trash": (
            "🗑 <b>Deleted</b>\n\n"
            "Messages deleted from watched chats land here: author, time,"
            " text, and the media if it could still be fetched.\n\n"
            "<i>The channel is private: nobody else sees it.</i>"
        ),
        "caught": "🔥 <b>One-time</b> · {}",
        "caught_lost": (
            "🔥 <b>One-time</b> · {}\n\n"
            "<i>Could not fetch the file — Telegram wiped it first.</i>\n\n{}"
        ),
        "kind_photo": "🖼 photo",
        "kind_voice": "🎤 voice",
        "kind_round": "⭕️ video note",
        "kind_video": "🎬 video",
        "erased": "🗑 <b>Deleted</b>",
        "erased_media": "🗑 <b>Deleted</b> · {}",
        "lbl_from": "From",
        "lbl_author": "Author",
        "lbl_chat": "Chat",
        "lbl_sent": "Sent",
        "lbl_gone": "Deleted",
        "lbl_lived": "Lived",
        "lbl_dur": "Length",
        "lbl_size": "Size",
        "lbl_ttl": "Expiry",
        "ttl_once": "one view",
        "ttl_secs": "{}s after opening",
        "text_title": "💬 <b>Text</b>",
        "text_none": "<i>No text</i>",
        "was_media": "📎 <b>Attached:</b> {}",
        "was_media_lost": "📎 <b>Attached:</b> {} — file no longer available",
        "who_erased": (
            "<i>Telegram does not say who pressed delete. In a DM it is you or"
            " the other person; in a group, admins too.</i>"
        ),
        "who_erased_pm": (
            "<i>Telegram does not say who pressed delete — in this DM it is"
            " either you or the other person.</i>"
        ),
        "chat_pm": "DM",
        "chat_saved": "Saved Messages",
        "unknown": "unknown",
        "me": "you",
        "menu": "🗄 <b>Keeper</b>\n\n{}",
        "block_media": "🔥 <b>One-time media</b> — {}",
        "block_trash": "🗑 <b>Deleted messages</b> — {}",
        "on": "on",
        "off": "off",
        "lbl_catch": "Catching",
        "lbl_watch": "Watching",
        "lbl_box": "Channel",
        "lbl_count": "Caught",
        "lbl_logged": "Logged",
        "box_none": "<i>not created yet</i>",
        "watch_pm": "DMs",
        "watch_groups": "groups",
        "watch_both": "DMs and groups",
        "watch_none": "<i>nothing — turn something on</i>",
        "cache_line": "📦 <b>In memory:</b> {} of {} messages",
        "ignored_line": "🙈 <b>Chats skipped:</b> {}",
        "menu_note": (
            "<i>One-time media is caught on arrival, before opening. Deleted"
            " messages come from memory, so only what arrived after the module"
            " loaded can be recovered.</i>"
        ),
        "made": "📦 <b>Channels ready</b>\n\n├ {}\n└ {}",
        "make_failed": "⚠️ <b>Could not create the channels:</b> <code>{}</code>",
        "ignored": "🙈 <b>Skipping this chat from now on</b>",
        "unignored": "👀 <b>Watching this chat again</b>",
        "ignore_pm_only": "🚫 <b>Nothing to skip here</b>",
        "btn_media_on": "🔥 Media: on",
        "btn_media_off": "🔥 Media: off",
        "btn_trash_on": "🗑 Deletes: on",
        "btn_trash_off": "🗑 Deletes: off",
        "btn_box_media": "↗️ One-time",
        "btn_box_trash": "↗️ Deleted",
        "btn_make": "🧰 Create channels",
        "btn_refresh": "🔄 Refresh",
        "btn_close": "✖️ Close",
        "cb_saved": "✅ Done",
        "cb_working": "⏳ One moment…",
        "u_day": "{}d",
        "u_hour": "{}h",
        "u_min": "{}m",
        "u_sec": "{}s",
        "u_less": "under a second",
    }

    config = loader.ModuleConfig(
        loader.ConfigValue(
            "catch_media",
            True,
            "Ловить одноразовые медиа",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "catch",
            ["photo", "voice", "round", "video"],
            "Какие одноразовые ловить",
            validator=loader.validators.MultiChoice(
                ["photo", "voice", "round", "video"]
            ),
        ),
        loader.ConfigValue(
            "log_deleted",
            True,
            "Записывать удалённые сообщения",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "watch_pm",
            True,
            "Следить за удалениями в личке",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "watch_groups",
            False,
            "Следить за удалениями в группах",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "log_own",
            False,
            "Записывать и свои удалённые сообщения",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "deleted_media",
            True,
            "Пытаться вытащить медиа из удалённых сообщений",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "max_mb",
            50,
            "Файлы тяжелее скольки мегабайт не забирать",
            validator=loader.validators.Integer(minimum=1, maximum=2000),
        ),
        loader.ConfigValue(
            "cache_size",
            2000,
            "Сколько последних сообщений держать в памяти для отлова удалений",
            validator=loader.validators.Integer(minimum=100, maximum=20000),
        ),
        loader.ConfigValue(
            "ignore",
            [],
            "id чатов, которые не трогать вовсе",
            validator=loader.validators.Series(validator=loader.validators.TelegramID()),
        ),
        loader.ConfigValue(
            "hide_boxes",
            True,
            "Прятать каналы-хранилища в архив и глушить уведомления",
            validator=loader.validators.Boolean(),
        ),
    )

    # ------------------------------------------------------------------ #
    #  Жизненный цикл
    # ------------------------------------------------------------------ #
    def __init__(self):
        # Кэш сообщений — именно кэш: тысячи записей в базе не нужны никому,
        # а после перезагрузки модуля восстанавливать нечего.
        self._cache = {}
        self._ignored = set()
        self._lock = asyncio.Lock()

    async def client_ready(self, client, db):
        """Подтянуть чёрный список из конфига."""
        self.config_complete()

    def config_complete(self):
        """Чёрный список считаем один раз, а не на каждое сообщение."""
        self._ignored = set()

        for raw in self.config["ignore"] or []:
            try:
                self._ignored.add(int(raw))
            except (TypeError, ValueError):
                logger.warning("В списке игнора мусор: %r", raw)

    def on_unload(self):
        """Отпустить память."""
        self._cache.clear()

    # ------------------------------------------------------------------ #
    #  Команды
    # ------------------------------------------------------------------ #
    @loader.command(aliases=["хранитель"])
    async def savercmd(self, message):
        """— что ловится, куда складывается и сколько поймано"""
        await self._answer(message, self._menu())

    @loader.owner
    @loader.command()
    async def savermakecmd(self, message):
        """— завести каналы-хранилища заново"""
        sent = await utils.answer(message, self.strings["cb_working"])

        try:
            self.set("media_box", None)
            self.set("trash_box", None)
            media = await self._box("media")
            trash = await self._box("trash")
        except Exception as error:
            logger.exception("Каналы-хранилища не завелись")
            await utils.answer(
                sent,
                self.strings["make_failed"].format(utils.escape_html(str(error))),
            )
            return

        await self._answer(
            sent,
            self.strings["made"].format(
                self._box_name(media, "media"), self._box_name(trash, "trash")
            ),
        )

    @loader.owner
    @loader.command(aliases=["хранитьне"])
    async def saverignorecmd(self, message):
        """— перестать следить за этим чатом (или снова начать)"""
        chat_id = getattr(message, "chat_id", None)

        if not chat_id:
            await utils.answer(message, self.strings["ignore_pm_only"])
            return

        listed = [str(item) for item in (self.config["ignore"] or [])]
        mark = str(chat_id)

        if mark in listed:
            listed.remove(mark)
            answer = self.strings["unignored"]
        else:
            listed.append(mark)
            answer = self.strings["ignored"]

        self.config["ignore"] = listed
        self.config_complete()
        await utils.answer(message, answer)

    # ------------------------------------------------------------------ #
    #  Кнопки
    # ------------------------------------------------------------------ #
    async def _toggle(self, call, key: str) -> None:
        self.config[key] = not self.config[key]
        await call.answer(self.strings["cb_saved"])
        await call.edit(self._menu(), reply_markup=self._markup())

    async def _refresh(self, call) -> None:
        await call.answer(self.strings["cb_working"])
        await call.edit(self._menu(), reply_markup=self._markup())

    async def _make(self, call) -> None:
        await call.answer(self.strings["cb_working"])

        try:
            await self._box("media")
            await self._box("trash")
        except Exception:
            logger.exception("Каналы-хранилища не завелись")

        await call.edit(self._menu(), reply_markup=self._markup())

    async def _close(self, call) -> None:
        await call.delete()

    # ------------------------------------------------------------------ #
    #  Слежка
    # ------------------------------------------------------------------ #
    @loader.watcher(no_commands=True)
    async def watcher(self, message):
        """Каждое сообщение: одноразовое — забрать, обычное — запомнить."""
        if self.config["catch_media"] and getattr(message, "media", None) is not None:
            if getattr(message.media, "ttl_seconds", None) is not None:
                utils.spawn(self._catch(message))
                return

        if self.config["log_deleted"]:
            self._remember(message)

    @loader.raw_handler(types.UpdateDeleteMessages, types.UpdateDeleteChannelMessages)
    async def _erased(self, update):
        """Сообщение удалили: если оно у нас в памяти — записать, что было."""
        if not self.config["log_deleted"]:
            return

        box = int(getattr(update, "channel_id", 0) or 0)

        for message_id in update.messages:
            note = self._cache.pop((box, message_id), None)

            if note is not None:
                utils.spawn(self._log(note))

    def _remember(self, message) -> None:
        """Дёшево запомнить, что пришло: разбираться будем, если удалят."""
        chat_id = getattr(message, "chat_id", None)

        if chat_id is None or not self._watched(message, chat_id):
            return

        peer = getattr(message, "peer_id", None)
        box = peer.channel_id if isinstance(peer, types.PeerChannel) else 0
        media = getattr(message, "media", None)

        self._cache[(int(box), message.id)] = {
            "chat_id": chat_id,
            "msg_id": message.id,
            "sender_id": getattr(message, "sender_id", None),
            "out": bool(getattr(message, "out", False)),
            "text": (getattr(message, "raw_text", None) or "")[:3000],
            "sent": int(message.date.timestamp()) if message.date else int(time.time()),
            "kind": self._kind(message),
            "media": media if isinstance(media, (types.MessageMediaPhoto, types.MessageMediaDocument)) else None,
            "private": box == 0 and chat_id > 0,
        }

        limit = self.config["cache_size"]

        while len(self._cache) > limit:
            self._cache.pop(next(iter(self._cache)))

    def _watched(self, message, chat_id: int) -> bool:
        """Стоит ли вообще держать это сообщение в памяти."""
        if chat_id in self._ignored:
            return False

        if getattr(message, "out", False) and not self.config["log_own"]:
            return False

        if getattr(message, "is_private", False):
            return self.config["watch_pm"]

        return self.config["watch_groups"]

    # ------------------------------------------------------------------ #
    #  Одноразовое медиа
    # ------------------------------------------------------------------ #
    async def _catch(self, message) -> None:
        """Забрать одноразовое медиа до того, как Telegram его сотрёт."""
        kind = self._kind(message)

        if kind is None or kind not in (self.config["catch"] or []):
            return

        chat_id = getattr(message, "chat_id", None)

        if chat_id is not None and chat_id in self._ignored:
            return

        if getattr(message, "out", False):
            return

        card = await self._card_caught(message, kind)
        grabbed = await self._grab(message, kind)

        head = self.strings["caught"].format(self.strings[f"kind_{kind}"])

        if grabbed is None:
            await self._deliver(
                "media",
                self.strings["caught_lost"].format(self.strings[f"kind_{kind}"], card),
            )
            return

        await self._post("media", f"{head}\n\n{card}", grabbed, kind, head)

        stats = self.pointer("stats", {})
        stats["caught"] = stats.get("caught", 0) + 1

    async def _card_caught(self, message, kind: str) -> str:
        ttl = getattr(message.media, "ttl_seconds", 0) or 0
        rows = [
            (self.strings["lbl_from"], await self._who(message.sender_id)),
            (self.strings["lbl_chat"], await self._where(message.chat_id)),
            (self.strings["lbl_sent"], self._date(message.date)),
        ]

        duration = getattr(getattr(message, "file", None), "duration", None)

        if duration:
            rows.append((self.strings["lbl_dur"], self._clock(duration)))

        size = getattr(getattr(message, "file", None), "size", None)

        if size:
            rows.append((self.strings["lbl_size"], self._weight(size)))

        rows.append((
            self.strings["lbl_ttl"],
            self.strings["ttl_once"]
            if ttl >= ONE_VIEW
            else self.strings["ttl_secs"].format(ttl),
        ))

        return self._block(rows)

    # ------------------------------------------------------------------ #
    #  Удалённые сообщения
    # ------------------------------------------------------------------ #
    async def _log(self, note: dict) -> None:
        """Записать, что именно пропало."""
        kind = note.get("kind")
        rows = [
            (self.strings["lbl_author"], await self._who(note["sender_id"], note["out"])),
            (self.strings["lbl_chat"], await self._where(note["chat_id"])),
            (self.strings["lbl_sent"], self._date(note["sent"])),
            (self.strings["lbl_gone"], self._date(time.time())),
            (self.strings["lbl_lived"], self._span(time.time() - note["sent"])),
        ]

        parts = [self._block(rows)]
        grabbed = None

        if kind is not None:
            if self.config["deleted_media"]:
                grabbed = await self._grab(note["media"], kind)

            parts.append(
                self.strings["was_media" if grabbed else "was_media_lost"].format(
                    self.strings[f"kind_{kind}"]
                )
            )

        text = (note.get("text") or "").strip()

        if text:
            body = self._cut(utils.escape_html(text), 3000)
            parts.append(f"{self.strings['text_title']}\n{body}")
        elif kind is None:
            parts.append(self.strings["text_none"])

        parts.append(
            self.strings["who_erased_pm" if note.get("private") else "who_erased"]
        )

        head = (
            self.strings["erased_media"].format(self.strings[f"kind_{kind}"])
            if kind
            else self.strings["erased"]
        )
        card = f"{head}\n\n" + "\n\n".join(parts)

        await self._post("trash", card, grabbed, kind, head)

        stats = self.pointer("stats", {})
        stats["logged"] = stats.get("logged", 0) + 1

    # ------------------------------------------------------------------ #
    #  Работа с файлами и каналами
    # ------------------------------------------------------------------ #
    async def _post(self, box: str, card: str, grabbed, kind: str, head: str) -> None:
        """Отправить карточку с файлом. Подпись Telegram режет на 1024 —
        длинную карточку отправляем отдельным сообщением, а не кусками."""
        if grabbed is None:
            await self._deliver(box, card)
            return

        flags = {
            "voice_note": kind == "voice",
            "video_note": kind == "round",
            "attributes": grabbed["attributes"],
        }
        file = grabbed["file"]

        if len(card) <= 1024:
            await self._deliver(box, card, file=file, **flags)
            return

        await self._deliver(box, head, file=file, **flags)
        await self._deliver(box, card)

    async def _grab(self, source, kind: str):
        """Файл в память вместе с родными атрибутами. None — если не отдался."""
        if source is None:
            return None

        size = self._size(source)

        if size and size > self.config["max_mb"] * 1024 * 1024:
            logger.info("Файл на %s пропущен: тяжелее лимита", self._weight(size))
            return None

        try:
            data = await self.client.download_media(source, bytes)
        except Exception:
            logger.info("Файл забрать не вышло — Telegram его уже не отдаёт")
            return None

        if not data:
            return None

        buffer = io.BytesIO(data)
        buffer.name = self._filename(source, kind)

        return {"file": buffer, "attributes": self._carry(source)}

    @staticmethod
    def _document(source):
        """Документ — и у сообщения, и у голого медиа из кэша."""
        direct = getattr(source, "document", None)

        if direct is not None:
            return direct

        return getattr(getattr(source, "media", None), "document", None)

    def _size(self, source):
        """Вес файла — и у сообщения, и у голого медиа из кэша."""
        direct = getattr(getattr(source, "file", None), "size", None)

        return direct or getattr(self._document(source), "size", None)

    def _carry(self, source) -> list:
        """Родные атрибуты файла.

        Без них Telethon подставляет свои: если в системе нет hachoir, он
        пишет видео w=1, h=1, duration=0 — клиент считает картинку
        квадратной и растягивает её. Плюс у голосовых так сохраняется
        дорожка громкости, а у кружков — признак круглого сообщения.
        """
        document = self._document(source)

        return [
            attribute
            for attribute in (getattr(document, "attributes", None) or [])
            if not isinstance(attribute, types.DocumentAttributeFilename)
        ]

    def _filename(self, source, kind: str) -> str:
        """Родное имя файла, если оно было: по нему Telegram узнаёт формат."""
        document = self._document(source)

        for attribute in getattr(document, "attributes", None) or []:
            name = getattr(attribute, "file_name", None)

            if name and "." in name:
                return name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

        return FILENAMES.get(kind, "file.bin")

    async def _box(self, kind: str):
        """Канал-хранилище: взять из базы, а нет — завести."""
        async with self._lock:
            stored = self.get(f"{kind}_box")

            if stored:
                try:
                    return await self.client.get_entity(int(stored))
                except Exception:
                    logger.warning("Канал %s пропал, завожу заново", kind)

            created = await self.client(
                functions.channels.CreateChannelRequest(
                    title=self.strings[f"box_{kind}"],
                    about=self.strings[f"about_{kind}"],
                    broadcast=True,
                    megagroup=False,
                )
            )
            chat = created.chats[0]
            self.set(f"{kind}_box", int(f"-100{chat.id}"))

            try:
                hello = await self.client.send_message(
                    chat, self.strings[f"hello_{kind}"], link_preview=False
                )
                await self.client.pin_message(chat, hello, notify=False)
            except Exception:
                logger.exception("Приветствие в %s не ушло", kind)

            if self.config["hide_boxes"]:
                await self._hide(chat)

            return chat

    async def _hide(self, chat) -> None:
        """Убрать канал в архив и заглушить — чтобы не мозолил глаза."""
        try:
            await self.client(
                functions.account.UpdateNotifySettingsRequest(
                    peer=chat,
                    settings=types.InputPeerNotifySettings(
                        mute_until=ONE_VIEW, silent=True
                    ),
                )
            )
        except Exception:
            logger.info("Заглушить канал не вышло")

        try:
            await self.client(
                functions.folders.EditPeerFoldersRequest(
                    folder_peers=[
                        types.InputFolderPeer(
                            peer=await self.client.get_input_entity(chat), folder_id=1
                        )
                    ]
                )
            )
        except Exception:
            logger.info("Убрать канал в архив не вышло")

    async def _deliver(self, kind: str, text: str, file=None, **kwargs):
        """Отправить в хранилище, а если канала не стало — завести новый."""
        for attempt in (1, 2):
            try:
                box = await self._box(kind)
            except Exception:
                logger.exception("Канал %s не отвечает", kind)
                return None

            try:
                if file is not None:
                    return await self.client.send_file(
                        box, file, caption=text, **kwargs
                    )

                return await self.client.send_message(box, text, link_preview=False)
            except errors.FloodWaitError as error:
                logger.warning("Telegram просит подождать %s сек.", error.seconds)
                return None
            except Exception:
                logger.exception("В канал %s отправить не вышло", kind)

                if attempt == 1:
                    self.set(f"{kind}_box", None)

                    if file is not None and hasattr(file, "seek"):
                        file.seek(0)

                    continue

                return None

    # ------------------------------------------------------------------ #
    #  Меню
    # ------------------------------------------------------------------ #
    def _menu(self) -> str:
        stats = self.pointer("stats", {})
        caught = ", ".join(
            self.strings[f"kind_{kind}"] for kind in (self.config["catch"] or [])
        )

        media = self._block(
            [
                (self.strings["lbl_catch"], caught or "—"),
                (self.strings["lbl_box"], self._box_name(self.get("media_box"), "media")),
                (self.strings["lbl_count"], stats.get("caught", 0)),
            ],
            self.strings["block_media"].format(
                self.strings["on" if self.config["catch_media"] else "off"]
            ),
        )
        trash = self._block(
            [
                (self.strings["lbl_watch"], self._scope()),
                (self.strings["lbl_box"], self._box_name(self.get("trash_box"), "trash")),
                (self.strings["lbl_logged"], stats.get("logged", 0)),
            ],
            self.strings["block_trash"].format(
                self.strings["on" if self.config["log_deleted"] else "off"]
            ),
        )

        parts = [media, trash, self.strings["cache_line"].format(
            len(self._cache), self.config["cache_size"]
        )]

        if self._ignored:
            parts.append(self.strings["ignored_line"].format(len(self._ignored)))

        parts.append(self.strings["menu_note"])

        return self.strings["menu"].format("\n\n".join(parts))

    def _scope(self) -> str:
        pm, groups = self.config["watch_pm"], self.config["watch_groups"]

        if pm and groups:
            return self.strings["watch_both"]

        if pm:
            return self.strings["watch_pm"]

        if groups:
            return self.strings["watch_groups"]

        return self.strings["watch_none"]

    def _markup(self) -> list:
        rows = [
            [
                {
                    "text": self.strings[
                        "btn_media_on" if self.config["catch_media"] else "btn_media_off"
                    ],
                    "callback": self._toggle,
                    "args": ("catch_media",),
                },
                {
                    "text": self.strings[
                        "btn_trash_on" if self.config["log_deleted"] else "btn_trash_off"
                    ],
                    "callback": self._toggle,
                    "args": ("log_deleted",),
                },
            ]
        ]

        links = [
            {"text": self.strings[f"btn_box_{kind}"], "url": link}
            for kind in ("media", "trash")
            if (link := self._box_link(self.get(f"{kind}_box")))
        ]

        if links:
            rows.append(links)

        rows.append(
            [
                {"text": self.strings["btn_make"], "callback": self._make},
                {"text": self.strings["btn_refresh"], "callback": self._refresh},
            ]
        )
        rows.append([{"text": self.strings["btn_close"], "callback": self._close}])

        return rows

    # ------------------------------------------------------------------ #
    #  Мелочи
    # ------------------------------------------------------------------ #
    @staticmethod
    def _kind(message) -> str:
        """Что за медиа: только то, что мы обещали ловить."""
        if getattr(message, "photo", None):
            return "photo"

        if getattr(message, "voice", None):
            return "voice"

        if getattr(message, "video_note", None):
            return "round"

        if getattr(message, "video", None):
            return "video"

        return None

    async def _who(self, user_id, mine: bool = False) -> str:
        """Ссылка на человека, а если не опознан — просто id."""
        if mine:
            return self.strings["me"]

        if not user_id:
            return self.strings["unknown"]

        try:
            entity = await self.client.get_entity(user_id)
        except Exception:
            return f"<code>{user_id}</code>"

        name = getattr(entity, "title", None) or " ".join(
            part
            for part in (
                getattr(entity, "first_name", None),
                getattr(entity, "last_name", None),
            )
            if part
        )
        link = (
            f'<a href="tg://user?id={user_id}">'
            f"{utils.escape_html(name or user_id)}</a>"
        )
        username = getattr(entity, "username", None)

        return f"{link} · @{username}" if username else link

    async def _where(self, chat_id) -> str:
        """Человеческое название чата."""
        if chat_id is None:
            return self.strings["unknown"]

        if chat_id == self.tg_id:
            return self.strings["chat_saved"]

        try:
            entity = await self.client.get_entity(chat_id)
        except Exception:
            return f"<code>{chat_id}</code>"

        if isinstance(entity, types.User):
            return f"{self.strings['chat_pm']} · {await self._who(chat_id)}"

        title = utils.escape_html(getattr(entity, "title", None) or chat_id)
        username = getattr(entity, "username", None)

        return f"{title} · @{username}" if username else title

    def _box_name(self, box, kind: str) -> str:
        if not box:
            return self.strings["box_none"]

        return utils.escape_html(getattr(box, "title", None) or self.strings[f"box_{kind}"])

    @staticmethod
    def _box_link(box):
        try:
            raw = str(int(box)).removeprefix("-100")
        except (TypeError, ValueError):
            return None

        return f"https://t.me/c/{raw}/1" if raw.isdigit() else None

    @staticmethod
    def _cut(text: str, limit: int) -> str:
        """Обрезать, не оставив половины &amp;-сущности."""
        if len(text) <= limit:
            return text

        return re.sub(r"&[a-z]{0,6};?$", "", text[:limit]) + "…"

    def _block(self, rows: list, title: str = None) -> str:
        """Строки, связанные деревом; заголовок — если он есть."""
        lines = [title] if title else []

        for index, (label, value) in enumerate(rows):
            tie = "└" if index == len(rows) - 1 else "├"
            lines.append(f"{tie} <b>{label}:</b> {value}")

        return "\n".join(lines)

    @staticmethod
    def _date(stamp) -> str:
        if hasattr(stamp, "timestamp"):
            stamp = stamp.timestamp()

        if not stamp:
            return "—"

        return time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(stamp))

    @staticmethod
    def _clock(seconds) -> str:
        minutes, seconds = divmod(int(seconds), 60)
        return f"{minutes}:{seconds:02d}"

    @staticmethod
    def _weight(size) -> str:
        step = float(size)

        for unit in ("Б", "КБ", "МБ", "ГБ"):
            if step < 1024 or unit == "ГБ":
                return f"{step:.0f} {unit}" if unit == "Б" else f"{step:.1f} {unit}"

            step /= 1024

    def _span(self, seconds) -> str:
        seconds = max(int(seconds), 0)
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

        if secs and not (days or hours):
            parts.append(self.strings["u_sec"].format(secs))

        return " ".join(parts) or self.strings["u_less"]

    async def _answer(self, message, text: str):
        """Ответить с кнопками, а без инлайн-бота — обычным текстом."""
        if self.inline is not None and self.inline.init_complete:
            if await self.inline.form(text, message=message, reply_markup=self._markup()):
                return

        await utils.answer(message, text)

"""Забирает медиа из постов, которые запрещено сохранять.

Кидаешь ссылку на пост — модуль открывает его, скачивает вложение и
перекладывает в свой канал вместе с подписью и указанием, откуда взято.
Работает и с закрытыми каналами: достаточно быть их участником.

Запрет сохранения в Telegram — это пожелание автора, а не замок: файл
всё равно приходит клиенту. Чужое медиа остаётся чужим.
"""

# meta developer: @soika
# meta description: .gm <ссылка> — забрать медиа из поста с запретом сохранения в свой канал

import asyncio
import io
import logging
import re
import time

from telethon import errors
from telethon.tl import functions, types

from .. import loader, utils

logger = logging.getLogger(__name__)

#: Ссылка на пост: приватная t.me/c/<id>/<пост>, публичная t.me/<ник>/<пост>,
#: с темой форума посередине и с диапазоном через дефис в конце
LINK = re.compile(
    r"(?:https?://)?t\.me/(?:s/)?"
    r"(?:c/(?P<raw>\d+)|(?P<name>[A-Za-z][\w\d_]{3,31}))"
    r"/(?:(?P<topic>\d+)/)?(?P<post>\d+)(?:-(?P<last>\d+))?"
    r"(?:\?(?P<query>[^\s]*))?",
    re.IGNORECASE,
)

#: Что за вложение в посте. Порядок важен: кружок — тоже видео, а
#: голосовое — тоже аудио, поэтому частное проверяем раньше общего
KINDS = (
    ("photo", "photo"),
    ("round", "video_note"),
    ("voice", "voice"),
    ("gif", "gif"),
    ("video", "video"),
    ("audio", "audio"),
    ("sticker", "sticker"),
    ("document", "document"),
)

#: Сколько соседних постов просматривать в поисках остальных частей альбома
AROUND = 9


def _filters():
    """Фильтры Telegram под наш выбор типов: канал отдаёт сразу нужное.

    Собираем через getattr — в разных версиях телетона набор чуть разный,
    а отсутствие фильтра всего лишь делает обход медленнее.
    """
    pairs = {
        ("photo",): "InputMessagesFilterPhotos",
        ("video",): "InputMessagesFilterVideo",
        ("photo", "video"): "InputMessagesFilterPhotoVideo",
        ("voice",): "InputMessagesFilterVoice",
        ("round",): "InputMessagesFilterRoundVideo",
        ("round", "voice"): "InputMessagesFilterRoundVoice",
        ("audio",): "InputMessagesFilterMusic",
        ("gif",): "InputMessagesFilterGif",
        ("document",): "InputMessagesFilterDocument",
    }

    return {
        frozenset(kinds): getattr(types, name)()
        for kinds, name in pairs.items()
        if getattr(types, name, None)
    }


FILTERS = _filters()

#: Как часто перерисовывать ход обхода
EVERY = 25


@loader.tds
class ДобычаМод(loader.Module):
    """Медиа из постов с запретом сохранения — в свой канал"""

    strings = {
        "name": "Добыча",
        # ── канал-хранилище ─────────────────────────────────────────────
        "box": "📥 Добыча",
        "box_named": "📥 {}",
        "about_named": "Медиа канала, забранные Сойкой",
        "about": "Медиа, забранные Сойкой из закрытых постов",
        "hello": (
            "📥 <b>Добыча</b>\n\n"
            "Сюда складывается медиа из постов, которые нельзя сохранить"
            " обычным способом. Под каждым — откуда взято и когда.\n\n"
            "<i>Канал личный: кроме тебя его никто не видит.</i>"
        ),
        # ── работа ──────────────────────────────────────────────────────
        "usage": (
            "📥 <b>Забрать медиа из поста</b>\n\n"
            "<code>{0}gm ссылка</code> — в хранилище\n"
            "<code>{0}gm ссылка 20</code> — двадцать постов подряд\n"
            "<code>{0}gmhere ссылка</code> — сюда, а не в хранилище\n\n"
            "<i>Годится и</i> <code>t.me/ник/123</code><i>, и</i>"
            " <code>t.me/c/1234567890/123</code><i>. Для закрытых каналов"
            " надо быть их участником.</i>"
        ),
        "all_usage": (
            "📡 <b>Забрать медиа со всего канала</b>\n\n"
            "<code>{0}gmall @ник</code>\n"
            "<code>{0}gmall t.me/c/1234567890</code>\n\n"
            "<i>Перед стартом спросит, что именно забирать, и заведёт под"
            " этот канал отдельное зеркало.</i>"
        ),
        "ready": (
            "📡 <b>Обойти «{}»</b>\n\n{}\n\n"
            "<i>Отметь, что забирать, и жми «Начать». Остановить —</i>"
            " <code>{}gmstop</code>"
        ),
        "lbl_target": "Куда",
        "lbl_known": "Уже забрано",
        "mirror_new": "новый канал «{}»",
        "mirror_old": "«{}» — уже заведён",
        "walk": (
            "📡 <b>«{}»</b>\n\n"
            "├ <b>Просмотрено:</b> {}\n"
            "├ <b>Забрано:</b> {}\n"
            "├ <b>Пропущено:</b> {}\n"
            "└ <b>Идёт:</b> {}\n\n"
            "<i>Остановить —</i> <code>{}gmstop</code>"
        ),
        "walked": (
            "✅ <b>Обход «{}» закончен</b>\n\n"
            "├ <b>Просмотрено:</b> {}\n"
            "├ <b>Забрано:</b> {}\n"
            "├ <b>Пропущено:</b> {}\n"
            "├ <b>Заняло:</b> {}\n"
            "└ <b>Куда:</b> {}"
        ),
        "halted": "⏹ <b>Обход «{}» остановлен</b>\n\n{}",
        "busy": (
            "⏳ <b>Один обход уже идёт</b>\n\n"
            "<i>Дождись его или останови:</i> <code>{}gmstop</code>"
        ),
        "idle": "🤷 <b>Сейчас ничего не обходится</b>",
        "halting": "⏹ <b>Останавливаю…</b>",
        "waiting": "⏳ <b>Пауза {}</b> — так просит Telegram",
        "no_link": (
            "🚫 <b>Ссылки на пост тут нет</b>\n\n"
            "<i>Нужна ссылка вида</i> <code>t.me/ник/123</code>"
            " <i>или</i> <code>t.me/c/1234567890/123</code>"
        ),
        "working": "📥 <b>Забираю…</b>",
        "walking": "📥 <b>Забираю</b> · {} из {}",
        "no_chat": (
            "🚫 <b>Канал не открылся</b>\n\n"
            "<i>Если он закрытый — подпишись на него сначала, иначе Telegram"
            " не отдаст ни пост, ни файл.</i>"
        ),
        "no_post": (
            "🚫 <b>Поста</b> <code>{}</code> <b>в этом канале нет</b>\n\n"
            "<i>Удалён или ссылка ведёт не туда.</i>"
        ),
        "no_media": "🚫 <b>В посте нет вложений</b>",
        "no_kind": (
            "🚫 <b>В посте нет того, что мы качаем</b>\n\n"
            "<i>Сейчас забираются:</i> {}\n"
            "<i>Поменять:</i> <code>{}gmcfg</code> <i>→ 🎞</i>"
        ),
        "already": (
            "📦 <b>Этот пост уже забран</b>\n\n└ {}\n\n"
            "<i>Забирать заново — выключи память в</i> <code>{}gmcfg</code>"
        ),
        "in_box": "<a href=\"{}\">посмотреть</a>",
        "in_box_plain": "лежит в хранилище",
        "empty": "🤷 <b>Ничего не забрано</b>",
        "done_one": "✅ <b>Забрано</b> · {}",
        "done_many": "✅ <b>Забрано постов: {}</b> · {}\n{}",
        "skipped": "└ <i>пропущено: {} — {}</i>",
        "why_gone": "не нашлось: {}",
        "why_done": "уже было: {}",
        "why_kind": "не тот тип: {}",
        "why_empty": "без вложений: {}",
        "why_heavy": "тяжелее лимита: {}",
        "to_box": "<a href=\"{}\">в хранилище</a>",
        "to_here": "сюда",
        "too_big": (
            "🐘 <b>Вложение тяжелее лимита</b> · {}\n\n"
            "<i>Лимит поднимается в</i> <code>{}cfg</code>"
        ),
        "flood": "⏳ <b>Telegram просит подождать {}</b>\n\n<i>Забрано до этого: {}</i>",
        "broke": "⚠️ <b>Не вышло:</b> <code>{}</code>",
        # ── карточка над файлом ─────────────────────────────────────────
        "from_named": "📥 <b>Из «{}»</b>",
        "from_plain": "📥 <b>Из закрытого канала</b>",
        "lbl_chat": "Канал",
        "lbl_post": "Пост",
        "lbl_date": "Дата",
        "lbl_locked": "Пометка",
        "locked": "🔒 сохранение запрещено",
        "open_post": "<a href=\"{}\">открыть</a>",
        # ── хранилище ───────────────────────────────────────────────────
        "made": "📦 <b>Хранилище готово</b>\n\n└ {}",
        "make_failed": "⚠️ <b>Не вышло завести канал:</b> <code>{}</code>",
        "box_where": "📦 <b>Хранилище</b>\n└ <b>{}:</b> {}",
        "box_open": "<a href=\"{}\">открыть</a> · <code>{}</code>",
        "box_none": "<i>ещё не заведено</i>",
        # ── настройки ───────────────────────────────────────────────────
        "cfg": "⚙️ <b>Добыча</b>\n\n{}",
        "lbl_where": "Куда складываю",
        "lbl_album": "Альбомы",
        "lbl_caption": "Подпись",
        "lbl_limit": "За раз не больше",
        "lbl_max": "Вес файла до",
        "lbl_kinds": "Качаю",
        "lbl_done": "Помню забранных",
        "kinds_all": "всё подряд",
        "kinds_none": "<i>ничего — отметь хоть что-то</i>",
        "kind_photo": "🖼 фото",
        "kind_video": "🎬 видео",
        "kind_round": "⭕️ кружки",
        "kind_voice": "🎤 голосовые",
        "kind_audio": "🎵 музыку",
        "kind_gif": "🎞 гифки",
        "kind_sticker": "🃏 стикеры",
        "kind_document": "📄 файлы",
        "kinds_title": (
            "🎞 <b>Что забирать</b>\n\n"
            "<i>Отмеченное качается, остальное пропускается.</i>"
        ),
        "where_box": "в хранилище",
        "where_here": "в текущий чат",
        "album_all": "забираю целиком",
        "album_one": "только указанный пост",
        "cap_full": "полная",
        "cap_short": "короткая",
        "cap_none": "без неё",
        "p_posts": "пост|поста|постов",
        "megabytes": "{} МБ",
        # ── кнопки ──────────────────────────────────────────────────────
        "btn_where": "📦 Куда: {}",
        "btn_album": "🖼 Альбомы: {}",
        "btn_caption": "📝 Подпись: {}",
        "btn_box": "↗️ Хранилище",
        "btn_all": "📡 Парсить канал",
        "btn_go": "▶️ Начать",
        "btn_kinds": "🎞 Что качать",
        "btn_memory": "🧠 Память: {}",
        "btn_forget": "🧹 Забыть ({})",
        "btn_back": "⬅️ Назад",
        "btn_make": "🧰 Завести заново",
        "btn_close": "✖️ Закрыть",
        "cb_saved": "✅ Готово",
        "forgot": "🧹 Память очищена",
        "on": "вкл",
        "off": "выкл",
        "cb_working": "⏳ Секунду…",
    }

    strings_en = {
        "box": "📥 Hauled",
        "box_named": "📥 {}",
        "about_named": "Channel media hauled by Soika",
        "about": "Media Soika hauled out of restricted posts",
        "hello": (
            "📥 <b>Hauled</b>\n\n"
            "Media from posts that cannot be saved the usual way lands here,"
            " each with a note on where it came from.\n\n"
            "<i>The channel is private: nobody else sees it.</i>"
        ),
        "usage": (
            "📥 <b>Haul media out of a post</b>\n\n"
            "<code>{0}gm link</code> — into the storage channel\n"
            "<code>{0}gm link 20</code> — twenty posts in a row\n"
            "<code>{0}gmhere link</code> — here instead of the storage\n\n"
            "<i>Both</i> <code>t.me/name/123</code> <i>and</i>"
            " <code>t.me/c/1234567890/123</code> <i>work. For private"
            " channels you must be a member.</i>"
        ),
        "all_usage": (
            "📡 <b>Haul all media from a channel</b>\n\n"
            "<code>{0}gmall @name</code>\n"
            "<code>{0}gmall t.me/c/1234567890</code>\n\n"
            "<i>It asks what to take first, then creates a mirror channel"
            " for this source.</i>"
        ),
        "ready": (
            "📡 <b>Walk “{}”</b>\n\n{}\n\n"
            "<i>Tick what to take and hit Start. To stop —</i>"
            " <code>{}gmstop</code>"
        ),
        "lbl_target": "Into",
        "lbl_known": "Already hauled",
        "mirror_new": "a new channel “{}”",
        "mirror_old": "“{}” — already there",
        "walk": (
            "📡 <b>“{}”</b>\n\n"
            "├ <b>Looked at:</b> {}\n"
            "├ <b>Hauled:</b> {}\n"
            "├ <b>Skipped:</b> {}\n"
            "└ <b>Running:</b> {}\n\n"
            "<i>To stop —</i> <code>{}gmstop</code>"
        ),
        "walked": (
            "✅ <b>Walk of “{}” finished</b>\n\n"
            "├ <b>Looked at:</b> {}\n"
            "├ <b>Hauled:</b> {}\n"
            "├ <b>Skipped:</b> {}\n"
            "├ <b>Took:</b> {}\n"
            "└ <b>Into:</b> {}"
        ),
        "halted": "⏹ <b>Walk of “{}” stopped</b>\n\n{}",
        "busy": (
            "⏳ <b>A walk is already running</b>\n\n"
            "<i>Wait for it or stop it:</i> <code>{}gmstop</code>"
        ),
        "idle": "🤷 <b>Nothing is being walked right now</b>",
        "halting": "⏹ <b>Stopping…</b>",
        "waiting": "⏳ <b>Pausing {}</b> — Telegram asks so",
        "no_link": (
            "🚫 <b>No post link here</b>\n\n"
            "<i>Need a link like</i> <code>t.me/name/123</code>"
            " <i>or</i> <code>t.me/c/1234567890/123</code>"
        ),
        "working": "📥 <b>Hauling…</b>",
        "walking": "📥 <b>Hauling</b> · {} of {}",
        "no_chat": (
            "🚫 <b>The channel did not open</b>\n\n"
            "<i>If it is private, join it first — otherwise Telegram gives"
            " neither the post nor the file.</i>"
        ),
        "no_post": (
            "🚫 <b>No post</b> <code>{}</code> <b>in that channel</b>\n\n"
            "<i>Deleted, or the link points elsewhere.</i>"
        ),
        "no_media": "🚫 <b>No attachment in the post</b>",
        "no_kind": (
            "🚫 <b>The post has nothing of the kinds we take</b>\n\n"
            "<i>Currently taking:</i> {}\n"
            "<i>Change:</i> <code>{}gmcfg</code> <i>→ 🎞</i>"
        ),
        "already": (
            "📦 <b>This post was hauled already</b>\n\n└ {}\n\n"
            "<i>To haul it again, switch the memory off in</i> <code>{}gmcfg</code>"
        ),
        "in_box": "<a href=\"{}\">have a look</a>",
        "in_box_plain": "it is in the storage",
        "empty": "🤷 <b>Nothing hauled</b>",
        "done_one": "✅ <b>Hauled</b> · {}",
        "done_many": "✅ <b>Posts hauled: {}</b> · {}\n{}",
        "skipped": "└ <i>skipped: {} — {}</i>",
        "why_gone": "not found: {}",
        "why_done": "already had: {}",
        "why_kind": "wrong type: {}",
        "why_empty": "no attachment: {}",
        "why_heavy": "over the limit: {}",
        "to_box": "<a href=\"{}\">to storage</a>",
        "to_here": "here",
        "too_big": (
            "🐘 <b>The attachment is over the limit</b> · {}\n\n"
            "<i>Raise it in</i> <code>{}cfg</code>"
        ),
        "flood": "⏳ <b>Telegram asks to wait {}</b>\n\n<i>Hauled before that: {}</i>",
        "broke": "⚠️ <b>Failed:</b> <code>{}</code>",
        "from_named": "📥 <b>From “{}”</b>",
        "from_plain": "📥 <b>From a private channel</b>",
        "lbl_chat": "Channel",
        "lbl_post": "Post",
        "lbl_date": "Date",
        "lbl_locked": "Flag",
        "locked": "🔒 saving restricted",
        "open_post": "<a href=\"{}\">open</a>",
        "made": "📦 <b>Storage ready</b>\n\n└ {}",
        "make_failed": "⚠️ <b>Could not create the channel:</b> <code>{}</code>",
        "box_where": "📦 <b>Storage</b>\n└ <b>{}:</b> {}",
        "box_open": "<a href=\"{}\">open</a> · <code>{}</code>",
        "box_none": "<i>not created yet</i>",
        "cfg": "⚙️ <b>Hauler</b>\n\n{}",
        "lbl_where": "Sending to",
        "lbl_album": "Albums",
        "lbl_caption": "Caption",
        "lbl_limit": "At most per run",
        "lbl_max": "File size up to",
        "lbl_kinds": "Taking",
        "lbl_done": "Remembered",
        "kinds_all": "everything",
        "kinds_none": "<i>nothing — tick at least one</i>",
        "kind_photo": "🖼 photos",
        "kind_video": "🎬 videos",
        "kind_round": "⭕️ video notes",
        "kind_voice": "🎤 voice",
        "kind_audio": "🎵 music",
        "kind_gif": "🎞 gifs",
        "kind_sticker": "🃏 stickers",
        "kind_document": "📄 files",
        "kinds_title": (
            "🎞 <b>What to take</b>\n\n"
            "<i>Ticked kinds are hauled, the rest are skipped.</i>"
        ),
        "where_box": "storage channel",
        "where_here": "current chat",
        "album_all": "whole album",
        "album_one": "only the linked post",
        "cap_full": "full",
        "cap_short": "short",
        "cap_none": "none",
        "p_posts": "post|posts",
        "megabytes": "{} MB",
        "btn_where": "📦 To: {}",
        "btn_album": "🖼 Albums: {}",
        "btn_caption": "📝 Caption: {}",
        "btn_box": "↗️ Storage",
        "btn_all": "📡 Walk a channel",
        "btn_go": "▶️ Start",
        "btn_kinds": "🎞 What to take",
        "btn_memory": "🧠 Memory: {}",
        "btn_forget": "🧹 Forget ({})",
        "btn_back": "⬅️ Back",
        "btn_make": "🧰 Create anew",
        "btn_close": "✖️ Close",
        "cb_saved": "✅ Done",
        "forgot": "🧹 Memory cleared",
        "on": "on",
        "off": "off",
        "cb_working": "⏳ One moment…",
    }

    config = loader.ModuleConfig(
        loader.ConfigValue(
            "to_box",
            True,
            "Складывать в свой канал-хранилище, а не в текущий чат",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "album",
            True,
            "Если пост — часть альбома, забирать альбом целиком",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "caption",
            "full",
            "Подпись: полная с исходным текстом, короткая или без неё",
            validator=loader.validators.Choice(["full", "short", "none"]),
        ),
        loader.ConfigValue(
            "kinds",
            ["photo", "video", "round", "voice", "audio", "gif", "sticker", "document"],
            "Что именно забирать из поста, остальное пропускать",
            validator=loader.validators.MultiChoice(
                ["photo", "video", "round", "voice", "audio", "gif", "sticker", "document"]
            ),
        ),
        loader.ConfigValue(
            "skip_done",
            True,
            "Не качать пост повторно, если он уже лежит в хранилище",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "limit",
            50,
            "Сколько постов забирать за одну команду",
            validator=loader.validators.Integer(minimum=1, maximum=500),
        ),
        loader.ConfigValue(
            "max_mb",
            500,
            "Файлы тяжелее скольки мегабайт пропускать",
            validator=loader.validators.Integer(minimum=1, maximum=4000),
        ),
        loader.ConfigValue(
            "pause",
            2,
            "Пауза между постами в секундах, чтобы не поймать лимит",
            validator=loader.validators.Integer(minimum=0, maximum=60),
        ),
        loader.ConfigValue(
            "hide_box",
            True,
            "Прятать хранилище в архив и глушить уведомления",
            validator=loader.validators.Boolean(),
        ),
    )

    def __init__(self):
        self._lock = asyncio.Lock()
        self._job = None
        self._halt = False

    def on_unload(self):
        """Оборвать обход канала, если модуль выгружают на ходу."""
        self._halt = True

        if self._job is not None and not self._job.done():
            self._job.cancel()

    # ------------------------------------------------------------------ #
    #  Команды
    # ------------------------------------------------------------------ #
    @loader.owner
    @loader.command(aliases=["добудь"])
    async def gmcmd(self, message):
        """<ссылка> [сколько] — забрать медиа из поста в хранилище"""
        await self._haul(message, here=not self.config["to_box"])

    @loader.owner
    @loader.command(aliases=["добудьсюда"])
    async def gmherecmd(self, message):
        """<ссылка> [сколько] — то же, но прислать в текущий чат"""
        await self._haul(message, here=True)

    @loader.owner
    @loader.command(aliases=["парси"])
    async def gmallcmd(self, message):
        """<ссылка или @ник> — забрать всё медиа канала в отдельный канал"""
        args = utils.get_args_raw(message) or ""

        if not args.strip():
            await utils.answer(message, self.strings["all_usage"].format(self._prefix))
            return

        if self._job is not None and not self._job.done():
            await utils.answer(message, self.strings["busy"].format(self._prefix))
            return

        sent = await utils.answer(message, self.strings["cb_working"])

        try:
            chat = await self._source(args)
        except Exception:
            logger.exception("Канал для обхода не открылся")
            chat = None

        if chat is None:
            await utils.answer(sent, self.strings["no_chat"])
            return

        self.set("aim", int(getattr(chat, "id", 0)))
        await self._answer(sent, self._ready(chat), self._kinds_markup(ready=True))

    @loader.owner
    @loader.command(aliases=["стоп"])
    async def gmstopcmd(self, message):
        """— остановить обход канала"""
        if self._job is None or self._job.done():
            await utils.answer(message, self.strings["idle"])
            return

        self._halt = True
        await utils.answer(message, self.strings["halting"])

    @loader.command(aliases=["хранилище"])
    async def gmboxcmd(self, message):
        """— ссылка на канал-хранилище: он в архиве, там его легко потерять"""
        stored = self.get("box")
        link = self._box_link(stored)
        await utils.answer(
            message,
            self.strings["box_where"].format(
                self.strings["box"],
                self.strings["box_open"].format(link, stored)
                if link
                else self.strings["box_none"],
            ),
        )

    @loader.owner
    @loader.command()
    async def gmmakecmd(self, message):
        """— завести канал-хранилище, если его ещё нет"""
        sent = await utils.answer(message, self.strings["cb_working"])

        try:
            await self._box()
        except Exception as error:
            logger.exception("Хранилище не завелось")
            await utils.answer(
                sent, self.strings["make_failed"].format(utils.escape_html(str(error)))
            )
            return

        stored = self.get("box")
        await utils.answer(
            sent,
            self.strings["made"].format(
                self.strings["box_open"].format(self._box_link(stored), stored)
            ),
        )

    @loader.command(aliases=["добычанастройки"])
    async def gmcfgcmd(self, message):
        """— настройки: куда складывать, альбомы, подпись, лимиты"""
        await self._answer(message, self._card(), self._markup())

    # ------------------------------------------------------------------ #
    #  Сама добыча
    # ------------------------------------------------------------------ #
    async def _haul(self, message, here: bool) -> None:
        args = utils.get_args_raw(message) or ""
        found = LINK.search(args)

        if not found:
            reply = await message.get_reply_message()
            found = LINK.search(getattr(reply, "raw_text", None) or "") if reply else None

        if not found:
            await utils.answer(
                message,
                self.strings["no_link"] if args else self.strings["usage"].format(self._prefix),
            )
            return

        wanted = self._wanted(found, args)
        # ?single в ссылке Telegram ставит, когда указано одно вложение
        # альбома, а не весь пост — уважаем это
        single = "single" in (found.group("query") or "").lower()
        sent = await utils.answer(message, self.strings["working"])

        try:
            chat = await self._chat(found)
        except Exception:
            logger.exception("Канал по ссылке не открылся")
            await utils.answer(sent, self.strings["no_chat"])
            return

        if chat is None:
            await utils.answer(sent, self.strings["no_chat"])
            return

        await self._walk(sent, chat, wanted, here, single)

    def _wanted(self, found, args: str) -> list:
        """Какие посты забирать: один, диапазон из ссылки или счёт из аргумента."""
        first = int(found.group("post"))
        last = found.group("last")
        tail = args[found.end():].strip().split()
        count = int(tail[0]) if tail and tail[0].isdigit() else None

        if last:
            stop = int(last)
        elif count:
            stop = first + count - 1
        else:
            stop = first

        stop = max(stop, first)
        stop = min(stop, first + self.config["limit"] - 1)

        return list(range(first, stop + 1))

    async def _walk(self, sent, chat, wanted: list, here: bool, single: bool = False) -> None:
        """Пройти по постам и переложить всё, что нашлось."""
        target = sent.peer_id if here else await self._box()
        taken, seen = 0, set()
        tally = {"gone": 0, "empty": 0, "heavy": 0, "kind": 0, "done": 0}
        heaviest = 0
        trouble = ""
        known = self._done()
        older = 0

        for number, post in enumerate(wanted, 1):
            if post in seen:
                continue

            mark = self._mark(chat, post)

            # Уже лежит в хранилище — второй раз не тащим
            if self.config["skip_done"] and mark in known:
                older = known[mark]
                tally["done"] += 1
                continue

            if len(wanted) > 1 and number % 5 == 1:
                await self._tick(sent, number, len(wanted))

            try:
                group, why = await self._group(chat, post, single)
            except errors.FloodWaitError as error:
                await utils.answer(
                    sent,
                    self.strings["flood"].format(
                        self._span(error.seconds), self._posts(taken)
                    ),
                )
                return
            except Exception as error:
                logger.exception("Пост %s не открылся", post)
                trouble = str(error)
                tally["gone"] += 1
                continue

            if not group:
                tally[why or "empty"] += 1
                continue

            seen.update(item.id for item in group)

            try:
                outcome = await self._move(target, chat, group)
            except errors.FloodWaitError as error:
                await utils.answer(
                    sent,
                    self.strings["flood"].format(
                        self._span(error.seconds), self._posts(taken)
                    ),
                )
                return
            except Exception as error:
                logger.exception("Пост %s не переложился", post)
                trouble = str(error)
                tally["empty"] += 1
                continue

            if outcome["moved"]:
                taken += 1

                if not here:
                    for item in group:
                        self._remember(known, self._mark(chat, item.id), outcome["saved"])
            elif outcome["heavy"]:
                tally["heavy"] += 1
                heaviest = max(heaviest, outcome["heavy"])
            elif outcome["wrong"]:
                tally["kind"] += 1
            else:
                tally["empty"] += 1

            if self.config["pause"] and number < len(wanted):
                await asyncio.sleep(self.config["pause"])

        await self._report(sent, taken, tally, here, wanted, heaviest, trouble, older)

    async def _group(self, chat, post: int, single: bool = False):
        """Пост (а если он часть альбома — весь альбом) и причина, если пусто."""
        found = await self.client.get_messages(chat, ids=post)

        if not found:
            return [], "gone"

        if single or not getattr(found, "grouped_id", None) or not self.config["album"]:
            return ([found], None) if getattr(found, "media", None) else ([], "empty")

        around = await self.client.get_messages(
            chat, ids=list(range(max(post - AROUND, 1), post + AROUND + 1))
        )
        parts = [
            item
            for item in around
            if item is not None
            and getattr(item, "grouped_id", None) == found.grouped_id
            and getattr(item, "media", None)
        ]

        return (parts, None) if parts else ([], "empty")

    async def _move(self, target, chat, group: list):
        """Скачать вложения поста и отправить их к себе."""
        limit = self.config["max_mb"] * 1024 * 1024
        files, attributes = [], []
        heavy = wrong = 0

        for item in group:
            if not self._mine(item):
                logger.info("Пост %s пропущен: тип %s не наш", item.id, self._kind(item))
                wrong += 1
                continue

            size = getattr(getattr(item, "file", None), "size", None)

            if size and size > limit:
                logger.info("Пост %s пропущен: %s", item.id, self._weight(size))
                heavy = max(heavy, size)
                continue

            data = await self.client.download_media(item, bytes)

            if not data:
                continue

            keeper = io.BytesIO(data)
            keeper.name = self._filename(item)
            files.append(keeper)
            attributes.append(self._carry(item))

        if not files:
            return {"moved": False, "heavy": heavy, "wrong": wrong, "saved": 0}

        caption = self._caption(chat, group[0])

        if len(files) == 1:
            saved = await self.client.send_file(
                target,
                files[0],
                caption=caption[:1024],
                attributes=attributes[0] or None,
                force_document=False,
            )
        else:
            saved = await self.client.send_file(target, files, caption=caption[:1024])

        if len(caption) > 1024:
            await self.client.send_message(target, caption, link_preview=False)

        first = saved[0] if isinstance(saved, list) and saved else saved

        return {
            "moved": True,
            "heavy": heavy,
            "wrong": wrong,
            "saved": int(getattr(first, "id", 0) or 0),
        }

    # ------------------------------------------------------------------ #
    #  Кто и откуда
    # ------------------------------------------------------------------ #
    async def _chat(self, found):
        """Канал по ссылке: публичный по нику, закрытый по внутреннему id."""
        name = found.group("name")

        if name:
            return await self.client.get_entity(name)

        marked = int(f"-100{found.group('raw')}")

        try:
            return await self.client.get_entity(marked)
        except Exception:
            logger.info("Канала %s нет в кэше, смотрю диалоги", marked)

        async for dialog in self.client.iter_dialogs():
            if dialog.id == marked:
                return dialog.entity

        return None

    def _caption(self, chat, post) -> str:
        """Откуда взято плюс исходный текст поста."""
        mode = self.config["caption"]

        if mode == "none":
            return ""

        title = getattr(chat, "title", None)
        head = (
            self.strings["from_named"].format(utils.escape_html(title))
            if title
            else self.strings["from_plain"]
        )

        if mode == "short":
            return head

        username = getattr(chat, "username", None)
        link = (
            f"https://t.me/{username}/{post.id}"
            if username
            else self._post_link(chat, post.id)
        )
        rows = [
            (
                self.strings["lbl_chat"],
                f"@{username}" if username else f"<code>{getattr(chat, 'id', '—')}</code>",
            ),
            (
                self.strings["lbl_post"],
                self.strings["open_post"].format(link) if link else f"<code>{post.id}</code>",
            ),
            (self.strings["lbl_date"], self._date(getattr(post, "date", None))),
        ]

        if getattr(post, "noforwards", False) or getattr(chat, "noforwards", False):
            rows.append((self.strings["lbl_locked"], self.strings["locked"]))

        text = (getattr(post, "raw_text", None) or "").strip()
        card = head + "\n" + self._block(rows)

        return f"{card}\n\n{utils.escape_html(text)}" if text else card

    @staticmethod
    def _post_link(chat, post: int):
        try:
            raw = str(int(getattr(chat, "id", 0)))
        except (TypeError, ValueError):
            return None

        return f"https://t.me/c/{raw.removeprefix('-100')}/{post}"

    @staticmethod
    def _kind(post) -> str:
        """Тип вложения поста: фото, кружок, голосовое и так далее."""
        for name, attribute in KINDS:
            if getattr(post, attribute, None):
                return name

        return "document"

    def _mine(self, post) -> bool:
        """Забираем ли мы такое вложение."""
        return self._kind(post) in (self.config["kinds"] or [])

    @staticmethod
    def _carry(post) -> list:
        """Родные атрибуты файла: без них Telegram перевирает размер видео."""
        document = getattr(post, "document", None)

        return [
            attribute
            for attribute in (getattr(document, "attributes", None) or [])
            if not isinstance(attribute, types.DocumentAttributeFilename)
        ]

    @staticmethod
    def _filename(post) -> str:
        document = getattr(post, "document", None)

        for attribute in getattr(document, "attributes", None) or []:
            name = getattr(attribute, "file_name", None)

            if name and "." in name:
                return name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

        if getattr(post, "photo", None):
            return "photo.jpg"

        if getattr(post, "voice", None):
            return "voice.ogg"

        if getattr(post, "video_note", None):
            return "round.mp4"

        if getattr(post, "video", None):
            return "video.mp4"

        return "file.bin"

    # ------------------------------------------------------------------ #
    #  Обход целого канала
    # ------------------------------------------------------------------ #
    async def _source(self, args: str):
        """Канал по @нику, по ссылке на него или по ссылке на любой пост."""
        found = LINK.search(args)

        if found:
            return await self._chat(found)

        raw = args.strip().split()[0]

        for junk in ("https://", "http://", "t.me/", "telegram.me/", "@"):
            if raw.startswith(junk):
                raw = raw[len(junk):]

        raw = raw.split("?")[0].strip("/")

        if raw.startswith("c/"):
            raw = raw[2:].split("/")[0]

            return await self._find(int(f"-100{raw}"))

        return await self.client.get_entity(raw.split("/")[0])

    async def _aim(self):
        """Канал, который выбрали для обхода."""
        aim = self.get("aim")

        return await self._find(int(f"-100{aim}")) if aim else None

    def _ready(self, chat) -> str:
        """Что будет, если нажать «Начать»."""
        mirrors = self.pointer("mirrors", {})
        known = mirrors.get(str(getattr(chat, "id", 0)))
        title = self.strings["box_named"].format(
            utils.escape_html(getattr(chat, "title", None) or "—")
        )
        rows = [
            (self.strings["lbl_kinds"], self._kinds_line()),
            (
                self.strings["lbl_target"],
                self.strings["mirror_old" if known else "mirror_new"].format(title),
            ),
            (
                self.strings["lbl_known"],
                self._posts(
                    sum(
                        1
                        for mark in self._done()
                        if mark.startswith(f"{getattr(chat, 'id', 0)}:")
                    )
                ),
            ),
        ]

        return self.strings["ready"].format(
            utils.escape_html(getattr(chat, "title", None) or "—"),
            self._block(rows),
            self._prefix,
        )

    async def _start(self, call) -> None:
        """Завести зеркало и запустить обход в фоне."""
        if self._job is not None and not self._job.done():
            await call.answer(self.strings["busy"].format(self._prefix), show_alert=True)
            return

        chat = await self._aim()

        if chat is None:
            await call.edit(self.strings["no_chat"])
            return

        await call.answer(self.strings["cb_working"])

        try:
            mirror = await self._mirror(chat)
        except Exception as error:
            logger.exception("Зеркало не завелось")
            await call.edit(self.strings["make_failed"].format(utils.escape_html(str(error))))
            return

        self._halt = False
        self._job = utils.spawn(self._parse(call, chat, mirror))

    async def _mirror(self, chat):
        """Отдельный канал под этот источник: заводим один раз."""
        async with self._lock:
            key = str(getattr(chat, "id", 0))
            mirrors = self.pointer("mirrors", {})
            known = mirrors.get(key)

            if known and known.get("id") and known.get("hash"):
                return types.InputPeerChannel(
                    channel_id=int(str(known["id"]).removeprefix("-100")),
                    access_hash=int(known["hash"]),
                )

            title = self.strings["box_named"].format(
                getattr(chat, "title", None) or key
            )[:128]
            made = await self._create(title, self.strings["about_named"])
            mirrors[key] = {
                "id": int(f"-100{made.id}"),
                "hash": int(getattr(made, "access_hash", 0) or 0),
                "title": title,
            }

            return made

    async def _parse(self, call, chat, mirror) -> None:
        """Пройти канал целиком и переложить всё подходящее."""
        title = utils.escape_html(getattr(chat, "title", None) or "—")
        started = time.monotonic()
        known = self._done()
        seen = taken = skipped = 0
        drawn = 0
        sieve = FILTERS.get(frozenset(self.config["kinds"] or []))

        try:
            async for post in self.client.iter_messages(chat, filter=sieve):
                if self._halt:
                    break

                seen += 1

                if seen - drawn >= EVERY:
                    drawn = seen
                    await self._draw(call, title, seen, taken, skipped, started)

                if not getattr(post, "media", None):
                    continue

                if not self._mine(post):
                    skipped += 1
                    continue

                mark = self._mark(chat, post.id)

                if self.config["skip_done"] and mark in known:
                    skipped += 1
                    continue

                outcome = None

                for _ in range(3):
                    try:
                        outcome = await self._move(mirror, chat, [post])
                        break
                    except errors.FloodWaitError as error:
                        await self._draw(
                            call, title, seen, taken, skipped, started, error.seconds
                        )
                        await asyncio.sleep(error.seconds + 5)

                        if self._halt:
                            break
                    except Exception:
                        logger.exception("Пост %s не переложился", post.id)
                        break

                if outcome is None:
                    skipped += 1
                    continue

                if outcome["moved"]:
                    taken += 1
                    self._remember(known, mark, outcome["saved"])
                else:
                    skipped += 1

                if self.config["pause"]:
                    await asyncio.sleep(self.config["pause"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Обход канала оборвался")

        where = self._mirror_link(chat)
        card = self.strings["walked" if not self._halt else "halted"]
        text = (
            card.format(
                title,
                seen,
                taken,
                skipped,
                self._span(time.monotonic() - started),
                where,
            )
            if not self._halt
            else card.format(
                title,
                self._block([
                    (self.strings["lbl_known"], self._posts(taken)),
                    (self.strings["lbl_target"], where),
                ]),
            )
        )

        try:
            await call.edit(text)
        except Exception:
            logger.info("Итог обхода дорисовать не вышло")

    async def _draw(self, call, title, seen, taken, skipped, started, wait=0) -> None:
        """Перерисовать ход обхода, не роняя обход из-за неудачи."""
        try:
            body = self.strings["walk"].format(
                title, seen, taken, skipped,
                self._span(time.monotonic() - started), self._prefix,
            )

            if wait:
                body = f"{body}\n\n{self.strings['waiting'].format(self._span(wait))}"

            await call.edit(body)
        except Exception:
            logger.info("Ход обхода дорисовать не вышло")

    def _mirror_link(self, chat) -> str:
        known = self.pointer("mirrors", {}).get(str(getattr(chat, "id", 0))) or {}
        link = self._box_link(known.get("id"))

        return (
            self.strings["in_box"].format(link)
            if link
            else self.strings["in_box_plain"]
        )

    def _done(self) -> dict:
        """Что уже забрано: ключ поста → номер сообщения в хранилище."""
        return self.pointer("done", {})

    @staticmethod
    def _mark(chat, post: int) -> str:
        return f"{getattr(chat, 'id', 0)}:{post}"

    def _remember(self, known: dict, mark: str, saved: int) -> None:
        """Запомнить забранное, не давая памяти разрастись без края."""
        known[mark] = saved

        while len(known) > 5000:
            known.pop(next(iter(known)))

    # ------------------------------------------------------------------ #
    #  Хранилище
    # ------------------------------------------------------------------ #
    def _peer(self):
        """Хранилище из базы: id вместе с access_hash, минуя кэш телетона."""
        raw, access = self.get("box"), self.get("box_hash")

        if not raw or not access:
            return None

        return types.InputPeerChannel(
            channel_id=int(str(raw).removeprefix("-100")),
            access_hash=int(access),
        )

    def _keep(self, chat) -> None:
        self.set("box", int(f"-100{chat.id}"))
        self.set("box_hash", int(getattr(chat, "access_hash", 0) or 0))

    async def _box(self):
        """Канал-хранилище. Новый заводим, только если старого правда нет."""
        async with self._lock:
            peer = self._peer()

            if peer is not None:
                return peer

            raw = self.get("box")

            if raw:
                found = await self._find(int(raw))

                if found is not None:
                    self._keep(found)
                    return self._peer()

                logger.warning("Хранилище не нашлось ни в кэше, ни в диалогах")

            return await self._create()

    async def _find(self, raw: int):
        try:
            return await self.client.get_entity(raw)
        except Exception:
            logger.info("Хранилища %s нет в кэше, смотрю диалоги", raw)

        async for dialog in self.client.iter_dialogs():
            if dialog.id == raw:
                return dialog.entity

        return None

    async def _create(self, title: str = None, about: str = None):
        created = await self.client(
            functions.channels.CreateChannelRequest(
                title=title or self.strings["box"],
                about=about or self.strings["about"],
                broadcast=True,
                megagroup=False,
            )
        )
        chat = created.chats[0]

        if title is None:
            self._keep(chat)

        try:
            hello = await self.client.send_message(
                chat, self.strings["hello"], link_preview=False
            )
            await self.client.pin_message(chat, hello, notify=False)
        except Exception:
            logger.exception("Приветствие в хранилище не ушло")

        if self.config["hide_box"]:
            await self._hide(chat)

        return chat

    async def _hide(self, chat) -> None:
        try:
            await self.client(
                functions.account.UpdateNotifySettingsRequest(
                    peer=chat,
                    settings=types.InputPeerNotifySettings(
                        mute_until=0x7FFFFFFF, silent=True
                    ),
                )
            )
        except Exception:
            logger.info("Заглушить хранилище не вышло")

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
            logger.info("Убрать хранилище в архив не вышло")

    # ------------------------------------------------------------------ #
    #  Отчёты и настройки
    # ------------------------------------------------------------------ #
    async def _tick(self, sent, number: int, total: int) -> None:
        try:
            await utils.answer(sent, self.strings["walking"].format(number, total))
        except Exception:
            logger.info("Строку хода править не вышло")

    async def _report(
        self,
        sent,
        taken: int,
        tally: dict,
        here: bool,
        wanted: list,
        heaviest: int,
        trouble: str,
        older: int = 0,
    ) -> None:
        skipped = sum(tally.values())
        asked = len(wanted)

        if not taken and asked == 1:
            # По одному посту говорим ровно то, что случилось
            if tally["done"]:
                link = self._box_link(self.get("box"), older)
                await utils.answer(
                    sent,
                    self.strings["already"].format(
                        self.strings["in_box"].format(link)
                        if link
                        else self.strings["in_box_plain"],
                        self._prefix,
                    ),
                )
            elif tally["kind"]:
                await utils.answer(
                    sent, self.strings["no_kind"].format(self._kinds_line(), self._prefix)
                )
            elif tally["heavy"]:
                await utils.answer(
                    sent,
                    self.strings["too_big"].format(self._weight(heaviest), self._prefix),
                )
            elif tally["gone"]:
                await utils.answer(sent, self.strings["no_post"].format(wanted[0]))
            elif trouble:
                await utils.answer(
                    sent, self.strings["broke"].format(utils.escape_html(trouble[:120]))
                )
            else:
                await utils.answer(sent, self.strings["no_media"])

            return

        if not taken:
            why = self._why(tally)
            await utils.answer(
                sent, f"{self.strings['empty']}\n\n{why}" if why else self.strings["empty"]
            )
            return

        link = self._box_link(self.get("box"))
        where = (
            self.strings["to_here"]
            if here or not link
            else self.strings["to_box"].format(link)
        )

        if asked == 1:
            await utils.answer(sent, self.strings["done_one"].format(where))
            return

        await utils.answer(
            sent,
            self.strings["done_many"].format(
                taken,
                where,
                self.strings["skipped"].format(skipped, self._why(tally))
                if skipped
                else "",
            ).strip(),
        )

    def _posts(self, count: int) -> str:
        """«1 пост», «2 поста», «5 постов» — и «posts» по-английски."""
        forms = self.strings["p_posts"].split("|")

        if len(forms) < 3:
            return f"{count} {forms[0] if abs(count) == 1 else forms[-1]}"

        tail = abs(count) % 100

        if 11 <= tail <= 14:
            return f"{count} {forms[2]}"

        tail %= 10

        if tail == 1:
            return f"{count} {forms[0]}"

        if 2 <= tail <= 4:
            return f"{count} {forms[1]}"

        return f"{count} {forms[2]}"

    def _why(self, tally: dict) -> str:
        """Причины пропуска одной строкой."""
        return ", ".join(
            self.strings[f"why_{name}"].format(count)
            for name, count in tally.items()
            if count
        )

    def _card(self) -> str:
        rows = [
            (
                self.strings["lbl_where"],
                self.strings["where_box" if self.config["to_box"] else "where_here"],
            ),
            (
                self.strings["lbl_album"],
                self.strings["album_all" if self.config["album"] else "album_one"],
            ),
            (self.strings["lbl_kinds"], self._kinds_line()),
            (self.strings["lbl_caption"], self.strings[f"cap_{self.config['caption']}"]),
            (self.strings["lbl_limit"], self._posts(self.config["limit"])),
            (self.strings["lbl_max"], self.strings["megabytes"].format(self.config["max_mb"])),
            (
                self.strings["lbl_done"],
                self._posts(len(self._done()))
                if self.config["skip_done"]
                else self.strings["off"],
            ),
        ]

        return self.strings["cfg"].format(self._block(rows))

    def _markup(self) -> list:
        rows = [
            [
                {
                    "text": self.strings["btn_where"].format(
                        self.strings["where_box" if self.config["to_box"] else "where_here"]
                    ),
                    "callback": self._toggle,
                    "args": ("to_box",),
                },
                {
                    "text": self.strings["btn_album"].format(
                        self.strings["album_all" if self.config["album"] else "album_one"]
                    ),
                    "callback": self._toggle,
                    "args": ("album",),
                },
            ],
            [
                {"text": self.strings["btn_kinds"], "callback": self._to_kinds},
                {
                    "text": self.strings["btn_caption"].format(
                        self.strings[f"cap_{self.config['caption']}"]
                    ),
                    "callback": self._cycle,
                },
            ],
            [
                {
                    "text": self.strings["btn_memory"].format(
                        self.strings["on" if self.config["skip_done"] else "off"]
                    ),
                    "callback": self._toggle,
                    "args": ("skip_done",),
                },
                {
                    "text": self.strings["btn_forget"].format(len(self._done())),
                    "callback": self._forget,
                },
            ],
            [
                {"text": self.strings["btn_all"], "callback": self._how_all},
                {"text": self.strings["btn_make"], "callback": self._make},
            ],
        ]
        link = self._box_link(self.get("box"))

        if link:
            rows.append([{"text": self.strings["btn_box"], "url": link}])

        rows.append([{"text": self.strings["btn_close"], "callback": self._close}])

        return rows

    def _kinds_line(self) -> str:
        """Что качаем — одной строкой."""
        chosen = [name for name, _ in KINDS if name in (self.config["kinds"] or [])]

        if not chosen:
            return self.strings["kinds_none"]

        if len(chosen) == len(KINDS):
            return self.strings["kinds_all"]

        return ", ".join(self.strings[f"kind_{name}"] for name in chosen)

    def _kinds_markup(self, ready: bool = False) -> list:
        """Отметки типов. Перед обходом внизу «Начать», иначе «Назад»."""
        chosen = set(self.config["kinds"] or [])
        rows, pair = [], []

        for name, _ in KINDS:
            pair.append({
                "text": ("✅ " if name in chosen else "▫️ ")
                + self.strings[f"kind_{name}"],
                "callback": self._flip,
                "args": (name, ready),
            })

            if len(pair) == 2:
                rows.append(pair)
                pair = []

        if pair:
            rows.append(pair)

        if ready:
            rows.append([{"text": self.strings["btn_go"], "callback": self._start}])
            rows.append([{"text": self.strings["btn_close"], "callback": self._close}])
        else:
            rows.append([
                {"text": self.strings["btn_back"], "callback": self._back},
                {"text": self.strings["btn_close"], "callback": self._close},
            ])

        return rows

    async def _to_kinds(self, call) -> None:
        await call.edit(self.strings["kinds_title"], reply_markup=self._kinds_markup())

    async def _flip(self, call, name: str, ready: bool = False) -> None:
        chosen = [item for item in (self.config["kinds"] or []) if item != name]

        if len(chosen) == len(self.config["kinds"] or []):
            chosen.append(name)

        self.config["kinds"] = chosen
        await call.answer(self.strings["cb_saved"])

        if not ready:
            await call.edit(self.strings["kinds_title"], reply_markup=self._kinds_markup())
            return

        chat = await self._aim()
        await call.edit(
            self._ready(chat) if chat else self.strings["no_chat"],
            reply_markup=self._kinds_markup(ready=True),
        )

    async def _back(self, call) -> None:
        await call.edit(self._card(), reply_markup=self._markup())

    async def _forget(self, call) -> None:
        self._done().clear()
        await call.answer(self.strings["forgot"])
        await call.edit(self._card(), reply_markup=self._markup())

    async def _toggle(self, call, key: str) -> None:
        self.config[key] = not self.config[key]
        await call.answer(self.strings["cb_saved"])
        await call.edit(self._card(), reply_markup=self._markup())

    async def _cycle(self, call) -> None:
        order = ["full", "short", "none"]
        now = order.index(self.config["caption"]) if self.config["caption"] in order else 0
        self.config["caption"] = order[(now + 1) % len(order)]
        await call.answer(self.strings["cb_saved"])
        await call.edit(self._card(), reply_markup=self._markup())

    async def _how_all(self, call) -> None:
        """Обходу нужна ссылка, а кнопка её не спросит — подсказываем команду."""
        await call.answer(
            re.sub(r"<[^>]+>", "", self.strings["all_usage"].format(self._prefix)),
            show_alert=True,
        )

    async def _make(self, call) -> None:
        await call.answer(self.strings["cb_working"])

        try:
            await self._box()
        except Exception:
            logger.exception("Хранилище не завелось")

        await call.edit(self._card(), reply_markup=self._markup())

    async def _close(self, call) -> None:
        await call.delete()

    # ------------------------------------------------------------------ #
    #  Мелочи
    # ------------------------------------------------------------------ #
    @staticmethod
    def _box_link(box, post: int = 1):
        try:
            raw = str(int(box)).removeprefix("-100")
        except (TypeError, ValueError):
            return None

        return f"https://t.me/c/{raw}/{post or 1}" if raw.isdigit() else None

    def _block(self, rows: list) -> str:
        lines = []

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

        return time.strftime("%d.%m.%Y %H:%M", time.localtime(stamp))

    @staticmethod
    def _weight(size) -> str:
        step = float(size or 0)

        for unit in ("Б", "КБ", "МБ", "ГБ"):
            if step < 1024 or unit == "ГБ":
                return f"{step:.0f} {unit}" if unit == "Б" else f"{step:.1f} {unit}"

            step /= 1024

    @staticmethod
    def _span(seconds) -> str:
        seconds = max(int(seconds or 0), 0)
        minutes, seconds = divmod(seconds, 60)

        return f"{minutes} мин. {seconds} сек." if minutes else f"{seconds} сек."

    async def _answer(self, message, text: str, markup: list):
        if self.inline is not None and self.inline.init_complete:
            if await self.inline.form(text, message=message, reply_markup=markup):
                return

        await utils.answer(message, text)

    @property
    def _prefix(self) -> str:
        return self.client.dispatcher.prefixes[0]

"""Аудит номеров участников чата: кто светит телефон.

Проходит по всем участникам чата и собирает тех, чей номер виден твоему
аккаунту. Итог — файлом: id, юзернейм, номер.

Скрытый настройками приватности номер не увидит никто — ни этот модуль,
ни кто-либо ещё. Telegram отдаёт телефон лишь у тех, кто и так открыл его
тебе: взаимный контакт или приватность «все». Модуль показывает, кто
именно светит номер, а не вскрывает спрятанный.
"""

# meta developer: @soika
# meta description: .leak — кто из участников чата светит номер телефона, итог файлом

import csv
import io
import json
import logging
import time

from telethon import errors

from .. import loader, utils

logger = logging.getLogger(__name__)

#: Как часто перерисовывать ход проверки
EVERY = 200


@loader.tds
class УтечкиМод(loader.Module):
    """Кто из участников чата светит номер телефона"""

    strings = {
        "name": "Утечки",
        "usage": (
            "📵 <b>Кто светит номер</b>\n\n"
            "<code>{0}leak</code> — проверить этот чат\n"
            "<code>{0}leak @чат</code> — проверить другой чат\n\n"
            "<i>Виден только номер, который человек и так открыл: взаимный"
            " контакт или приватность «все». Скрытый не покажет никто.</i>"
        ),
        "no_chat": (
            "🚫 <b>Чат не открылся</b>\n\n"
            "<i>Проверь ссылку. В закрытый нужно сперва вступить.</i>"
        ),
        "scanning": "📵 <b>Читаю участников…</b>",
        "walking": "📵 <b>Проверено: {}</b> · с номером: {}",
        "no_rights": (
            "🚫 <b>Список участников не отдаётся</b>\n\n"
            "<i>На канале для этого нужны права админа.</i>"
        ),
        "clean": (
            "✅ <b>Открытых номеров нет</b>\n\n"
            "├ <b>Проверено:</b> {}\n"
            "└ <i>Ни у кого номер не виден — приватность закрыта.</i>"
        ),
        "found": (
            "📵 <b>Найдены открытые номера</b>\n\n"
            "├ <b>Проверено:</b> {}\n"
            "├ <b>Светят номер:</b> {}\n"
            "└ <b>Файл:</b> {}\n\n"
            "<i>Это те, кто сам открыл номер тебе. Скрытых тут нет.</i>"
        ),
        "to_saved": "в избранном",
        "to_here": "ниже",
        "caption": "📵 Открытые номера · {}",
        "flood": (
            "⏳ <b>Telegram просит подождать {}</b>\n\n"
            "<i>Успел проверить: {}, с номером: {}.</i>"
        ),
        "broke": "⚠️ <b>Не вышло:</b> <code>{}</code>",
        # ── настройки ───────────────────────────────────────────────────
        "cfg": "⚙️ <b>Утечки</b>\n\n{}",
        "lbl_deliver": "Файл шлю",
        "lbl_format": "Формат",
        "lbl_bots": "Ботов",
        "lbl_deleted": "Удалённые аккаунты",
        "lbl_limit": "Предел",
        "deliver_me": "в избранное",
        "deliver_here": "в этот чат",
        "skip": "пропускаю",
        "keep": "оставляю",
        "no_limit": "без предела",
        "limited": "{} чел.",
        # ── кнопки ──────────────────────────────────────────────────────
        "btn_deliver": "📤 Файл: {}",
        "btn_format": "📄 Формат: {}",
        "btn_bots": "🤖 Боты: {}",
        "btn_close": "✖️ Закрыть",
        "cb_saved": "✅ Готово",
    }

    strings_en = {
        "usage": (
            "📵 <b>Who exposes a phone number</b>\n\n"
            "<code>{0}leak</code> — check this chat\n"
            "<code>{0}leak @chat</code> — check another chat\n\n"
            "<i>Only a number the person already opened is visible: a mutual"
            " contact or “everybody” privacy. A hidden one nobody can show.</i>"
        ),
        "no_chat": (
            "🚫 <b>The chat did not open</b>\n\n"
            "<i>Check the link. For a private one you must join first.</i>"
        ),
        "scanning": "📵 <b>Reading members…</b>",
        "walking": "📵 <b>Checked: {}</b> · with a number: {}",
        "no_rights": (
            "🚫 <b>The member list is not given out</b>\n\n"
            "<i>A channel needs admin rights for that.</i>"
        ),
        "clean": (
            "✅ <b>No exposed numbers</b>\n\n"
            "├ <b>Checked:</b> {}\n"
            "└ <i>Nobody's number is visible — privacy is closed.</i>"
        ),
        "found": (
            "📵 <b>Exposed numbers found</b>\n\n"
            "├ <b>Checked:</b> {}\n"
            "├ <b>Expose a number:</b> {}\n"
            "└ <b>File:</b> {}\n\n"
            "<i>These opened the number to you themselves. No hidden ones here.</i>"
        ),
        "to_saved": "in Saved Messages",
        "to_here": "below",
        "caption": "📵 Exposed numbers · {}",
        "flood": (
            "⏳ <b>Telegram asks to wait {}</b>\n\n"
            "<i>Managed to check: {}, with a number: {}.</i>"
        ),
        "broke": "⚠️ <b>Failed:</b> <code>{}</code>",
        "cfg": "⚙️ <b>Leaks</b>\n\n{}",
        "lbl_deliver": "Send file",
        "lbl_format": "Format",
        "lbl_bots": "Bots",
        "lbl_deleted": "Deleted accounts",
        "lbl_limit": "Cap",
        "deliver_me": "to Saved Messages",
        "deliver_here": "to this chat",
        "skip": "skip",
        "keep": "keep",
        "no_limit": "no cap",
        "limited": "{} people",
        "btn_deliver": "📤 File: {}",
        "btn_format": "📄 Format: {}",
        "btn_bots": "🤖 Bots: {}",
        "btn_close": "✖️ Close",
        "cb_saved": "✅ Done",
    }

    config = loader.ModuleConfig(
        loader.ConfigValue(
            "deliver",
            "me",
            "Куда слать файл: в избранное (безопаснее) или в текущий чат",
            validator=loader.validators.Choice(["me", "here"]),
        ),
        loader.ConfigValue(
            "format",
            "csv",
            "Формат файла: таблица csv, текст txt или json",
            validator=loader.validators.Choice(["csv", "txt", "json"]),
        ),
        loader.ConfigValue(
            "skip_bots",
            True,
            "Пропускать ботов — у них номеров не бывает",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "skip_deleted",
            True,
            "Пропускать удалённые аккаунты",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "limit",
            0,
            "Скольких участников проверить максимум, 0 — всех",
            validator=loader.validators.Integer(minimum=0, maximum=200000),
        ),
    )

    # ------------------------------------------------------------------ #
    #  Команды
    # ------------------------------------------------------------------ #
    @loader.owner
    @loader.command(aliases=["утечки", "номера"])
    async def leakcmd(self, message):
        """[@чат] — кто из участников светит номер телефона, итог файлом"""
        args = (utils.get_args_raw(message) or "").strip()
        here = getattr(message, "is_group", False) or getattr(message, "is_channel", False)

        if not args and not here:
            await utils.answer(message, self.strings["usage"].format(self._prefix))
            return

        chat = await self._where(message)

        if chat is None:
            await utils.answer(message, self.strings["no_chat"])
            return

        sent = await utils.answer(message, self.strings["scanning"])
        await self._scan(sent, chat)

    @loader.command(aliases=["утечкинастройки"])
    async def leakcfgcmd(self, message):
        """— настройки: куда слать файл, формат, кого пропускать"""
        await self._answer(message, self._card(), self._markup())

    # ------------------------------------------------------------------ #
    #  Проверка
    # ------------------------------------------------------------------ #
    async def _scan(self, sent, chat) -> None:
        limit = self.config["limit"] or None
        rows, seen, drawn = [], 0, 0

        try:
            async for user in self.client.iter_participants(chat, limit=limit):
                seen += 1

                if seen - drawn >= EVERY:
                    drawn = seen
                    await self._tick(sent, seen, len(rows))

                if self._skip(user):
                    continue

                phone = getattr(user, "phone", None)

                if phone:
                    rows.append(self._row(user, phone))
        except errors.FloodWaitError as error:
            await utils.answer(
                sent,
                self.strings["flood"].format(
                    self._span(error.seconds), seen, len(rows)
                ),
            )
            return
        except errors.ChatAdminRequiredError:
            await utils.answer(sent, self.strings["no_rights"])
            return
        except Exception as error:
            logger.exception("Список участников не прочитался")
            await utils.answer(
                sent, self.strings["broke"].format(utils.escape_html(str(error)))
            )
            return

        if not rows:
            await utils.answer(sent, self.strings["clean"].format(seen))
            return

        await self._deliver(sent, chat, rows, seen)

    def _skip(self, user) -> bool:
        if self.config["skip_bots"] and getattr(user, "bot", False):
            return True

        if self.config["skip_deleted"] and getattr(user, "deleted", False):
            return True

        return False

    def _row(self, user, phone: str) -> dict:
        return {
            "id": getattr(user, "id", 0),
            "username": getattr(user, "username", None) or "",
            "phone": self._phone(phone),
            "name": self._name(user),
        }

    async def _deliver(self, sent, chat, rows: list, seen: int) -> None:
        blob = self._build(rows, chat)
        title = getattr(chat, "title", None) or getattr(chat, "id", "—")
        to_here = self.config["deliver"] == "here"
        target = sent.peer_id if to_here else "me"

        try:
            await self.client.send_file(
                target,
                blob,
                caption=self.strings["caption"].format(utils.escape_html(str(title))),
                force_document=True,
            )
        except Exception:
            logger.exception("Файл с номерами не ушёл")
            await self.client.send_file("me", blob, force_document=True)
            to_here = False

        where = self.strings["to_here" if to_here else "to_saved"]
        await utils.answer(
            sent, self.strings["found"].format(seen, len(rows), where)
        )

    def _build(self, rows: list, chat):
        """Собрать файл выбранного формата в память."""
        fmt = self.config["format"]
        buffer = io.BytesIO()

        if fmt == "json":
            body = json.dumps(rows, ensure_ascii=False, indent=2)
        elif fmt == "txt":
            body = "\n".join(
                f"{item['id']}\t"
                f"{('@' + item['username']) if item['username'] else '—'}\t"
                f"{item['phone']}\t{item['name']}"
                for item in rows
            )
        else:
            sink = io.StringIO()
            writer = csv.writer(sink)
            writer.writerow(["id", "username", "phone", "name"])

            for item in rows:
                writer.writerow(
                    [item["id"], item["username"], item["phone"], item["name"]]
                )

            body = sink.getvalue()

        buffer.write(body.encode("utf-8"))
        buffer.seek(0)
        buffer.name = f"phones_{getattr(chat, 'id', 0)}_{int(time.time())}.{fmt}"

        return buffer

    # ------------------------------------------------------------------ #
    #  Настройки
    # ------------------------------------------------------------------ #
    def _card(self) -> str:
        rows = [
            (
                self.strings["lbl_deliver"],
                self.strings["deliver_me" if self.config["deliver"] == "me" else "deliver_here"],
            ),
            (self.strings["lbl_format"], self.config["format"].upper()),
            (
                self.strings["lbl_bots"],
                self.strings["skip" if self.config["skip_bots"] else "keep"],
            ),
            (
                self.strings["lbl_deleted"],
                self.strings["skip" if self.config["skip_deleted"] else "keep"],
            ),
            (
                self.strings["lbl_limit"],
                self.strings["no_limit"]
                if not self.config["limit"]
                else self.strings["limited"].format(self.config["limit"]),
            ),
        ]

        return self.strings["cfg"].format(self._block(rows))

    def _markup(self) -> list:
        return [
            [
                {
                    "text": self.strings["btn_deliver"].format(
                        self.strings["deliver_me" if self.config["deliver"] == "me" else "deliver_here"]
                    ),
                    "callback": self._flip_deliver,
                },
                {
                    "text": self.strings["btn_format"].format(self.config["format"].upper()),
                    "callback": self._flip_format,
                },
            ],
            [
                {
                    "text": self.strings["btn_bots"].format(
                        self.strings["skip" if self.config["skip_bots"] else "keep"]
                    ),
                    "callback": self._toggle,
                    "args": ("skip_bots",),
                },
                {"text": self.strings["btn_close"], "callback": self._close},
            ],
        ]

    async def _flip_deliver(self, call) -> None:
        self.config["deliver"] = "here" if self.config["deliver"] == "me" else "me"
        await call.answer(self.strings["cb_saved"])
        await call.edit(self._card(), reply_markup=self._markup())

    async def _flip_format(self, call) -> None:
        order = ["csv", "txt", "json"]
        now = order.index(self.config["format"]) if self.config["format"] in order else 0
        self.config["format"] = order[(now + 1) % len(order)]
        await call.answer(self.strings["cb_saved"])
        await call.edit(self._card(), reply_markup=self._markup())

    async def _toggle(self, call, key: str) -> None:
        self.config[key] = not self.config[key]
        await call.answer(self.strings["cb_saved"])
        await call.edit(self._card(), reply_markup=self._markup())

    async def _close(self, call) -> None:
        await call.delete()

    # ------------------------------------------------------------------ #
    #  Мелочи
    # ------------------------------------------------------------------ #
    async def _where(self, message):
        """Чат из аргумента, а нет — тот, где вызвали."""
        args = (utils.get_args_raw(message) or "").strip()

        if args:
            try:
                return await self.client.get_entity(args.split()[0])
            except Exception:
                logger.info("Чат по ссылке не открылся")
                return None

        if not (getattr(message, "is_group", False) or getattr(message, "is_channel", False)):
            return None

        try:
            return await message.get_chat()
        except Exception:
            logger.info("Текущий чат не отдался")
            return None

    async def _tick(self, sent, seen: int, found: int) -> None:
        try:
            await utils.answer(sent, self.strings["walking"].format(seen, found))
        except Exception:
            logger.info("Ход проверки дорисовать не вышло")

    @staticmethod
    def _phone(phone: str) -> str:
        digits = str(phone).lstrip("+")
        return f"+{digits}" if digits else "—"

    @staticmethod
    def _name(user) -> str:
        return " ".join(
            part
            for part in (
                getattr(user, "first_name", None),
                getattr(user, "last_name", None),
            )
            if part
        ) or str(getattr(user, "id", "—"))

    def _block(self, rows: list) -> str:
        lines = []

        for index, (label, value) in enumerate(rows):
            tie = "└" if index == len(rows) - 1 else "├"
            lines.append(f"{tie} <b>{label}:</b> {value}")

        return "\n".join(lines)

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

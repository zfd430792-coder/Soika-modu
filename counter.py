"""Счётчики: показывает почти всё, что умеет модуль Сойки.

Здесь есть команды с аргументами, своя секция в базе, настройки, кнопки,
права и фоновая задача — можно подсматривать как в живой пример.
"""

# meta developer: @soika
# meta description: Именованные счётчики: считай что угодно прямо в чате

import time

from .. import loader, utils


@loader.tds
class СчётчикиМод(loader.Module):
    """Именованные счётчики с кнопками"""

    strings = {
        "name": "Счётчики",
        "card": "🔢 <b>{name}</b>\n<b>Значение:</b> <code>{value}</code>\n<i>Изменён: {when}</i>",
        "no_name": "🚫 <b>Скажи, как называется счётчик:</b> <code>{}count работа</code>",
        "listing": "🔢 <b>Счётчиков: {}</b>\n\n{}",
        "empty": "🔢 <b>Счётчиков пока нет</b>",
        "line": "▫️ <b>{}</b> — <code>{}</code>",
        "dropped": "🗑 <b>Счётчик</b> <code>{}</code> <b>удалён</b>",
        "not_found": "🔎 <b>Счётчика</b> <code>{}</code> <b>нет</b>",
        "btn_plus": "➕",
        "btn_minus": "➖",
        "btn_reset": "🔄 Сброс",
        "never": "никогда",
    }

    strings_en = {
        "card": "🔢 <b>{name}</b>\n<b>Value:</b> <code>{value}</code>\n<i>Changed: {when}</i>",
        "no_name": "🚫 <b>Name the counter:</b> <code>{}count work</code>",
        "listing": "🔢 <b>Counters: {}</b>\n\n{}",
        "empty": "🔢 <b>No counters yet</b>",
        "line": "▫️ <b>{}</b> — <code>{}</code>",
        "dropped": "🗑 <b>Counter</b> <code>{}</code> <b>removed</b>",
        "not_found": "🔎 <b>No counter</b> <code>{}</code>",
        "btn_plus": "➕",
        "btn_minus": "➖",
        "btn_reset": "🔄 Reset",
        "never": "never",
    }

    config = loader.ModuleConfig(
        loader.ConfigValue(
            "step",
            1,
            "На сколько менять счётчик за одно нажатие",
            validator=loader.validators.Integer(minimum=1, maximum=1000),
        ),
    )

    # ------------------------------------------------------------------ #
    #  Команды
    # ------------------------------------------------------------------ #
    @loader.command(alias="cnt")
    async def countcmd(self, message):
        """<имя> — открыть счётчик с кнопками"""
        name = utils.get_args_raw(message).strip()

        if not name:
            prefix = self.client.dispatcher.prefixes[0]
            await utils.answer(message, self.strings["no_name"].format(prefix))
            return

        await self._show(message, name)

    @loader.command()
    async def countscmd(self, message):
        """— все счётчики списком"""
        counters = self._counters()

        if not counters:
            await utils.answer(message, self.strings["empty"])
            return

        lines = "\n".join(
            self.strings["line"].format(utils.escape_html(name), data["value"])
            for name, data in sorted(counters.items())
        )
        await utils.answer(message, self.strings["listing"].format(len(counters), lines))

    @loader.command()
    async def delcountcmd(self, message):
        """<имя> — удалить счётчик"""
        name = utils.get_args_raw(message).strip()
        counters = self._counters()

        if name not in counters:
            await utils.answer(message, self.strings["not_found"].format(utils.escape_html(name)))
            return

        counters.pop(name)
        await utils.answer(message, self.strings["dropped"].format(utils.escape_html(name)))

    # ------------------------------------------------------------------ #
    #  Внутренности
    # ------------------------------------------------------------------ #
    def _counters(self) -> dict:
        """Своя секция в базе. pointer отдаёт живой словарь — правки сохранятся."""
        return self.pointer("counters", {})

    def _card(self, name: str) -> str:
        data = self._counters().get(name, {"value": 0, "changed": 0})
        when = (
            time.strftime("%d.%m.%Y %H:%M", time.localtime(data["changed"]))
            if data["changed"]
            else self.strings["never"]
        )

        return self.strings["card"].format(
            name=utils.escape_html(name),
            value=data["value"],
            when=when,
        )

    def _markup(self, name: str) -> list[list[dict]]:
        return [
            [
                {"text": self.strings["btn_minus"], "callback": self._change, "args": (name, -1)},
                {"text": self.strings["btn_plus"], "callback": self._change, "args": (name, 1)},
            ],
            [{"text": self.strings["btn_reset"], "callback": self._reset, "args": (name,)}],
        ]

    async def _show(self, message, name: str) -> None:
        counters = self._counters()
        counters.setdefault(name, {"value": 0, "changed": 0})

        # Кнопки живут через инлайн-бота. Если он не поднялся — отвечаем текстом
        if self.inline is not None and self.inline.init_complete:
            sent = await self.inline.form(
                self._card(name),
                message=message,
                reply_markup=self._markup(name),
            )

            if sent:
                return

        await utils.answer(message, self._card(name))

    async def _change(self, call, name: str, sign: int) -> None:
        counters = self._counters()
        data = counters.setdefault(name, {"value": 0, "changed": 0})
        data["value"] += sign * self.config["step"]
        data["changed"] = int(time.time())

        await call.answer(str(data["value"]))
        await call.edit(self._card(name), reply_markup=self._markup(name))

    async def _reset(self, call, name: str) -> None:
        counters = self._counters()
        counters[name] = {"value": 0, "changed": int(time.time())}

        await call.answer("0")
        await call.edit(self._card(name), reply_markup=self._markup(name))

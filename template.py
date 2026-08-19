"""Шаблон модуля для Сойки — скопируй, переименуй и пиши своё."""

# Шапка модуля. Всё это необязательно, но принято заполнять.
# meta developer: @username
# meta description: Короткое описание для каталога
# meta banner: https://example.com/banner.png
# requires: aiohttp

from .. import loader, utils


@loader.tds
class ШаблонМод(loader.Module):
    """Что делает модуль — эта строка видна в .help"""

    #: Тексты модуля. Ключ "name" — как модуль называется в .help
    strings = {
        "name": "Шаблон",
        "hello": "👋 <b>Привет, {}!</b>",
        "no_args": "🚫 <b>Нужен аргумент</b>",
    }

    #: Английские тексты. Чего здесь нет — возьмётся из strings
    strings_en = {
        "hello": "👋 <b>Hi, {}!</b>",
        "no_args": "🚫 <b>Argument required</b>",
    }

    #: Настройки модуля — их правят через .cfg
    config = loader.ModuleConfig(
        loader.ConfigValue(
            "greeting",
            "👋",
            "Каким смайликом здороваться",
            validator=loader.validators.String(max_len=8),
        ),
    )

    async def client_ready(self, client, db):
        """Вызывается один раз, когда модуль загружен и клиент на связи."""

    @loader.command(alias="шаб")
    async def templatecmd(self, message):
        """<имя> — поздороваться"""
        name = utils.get_args_raw(message)

        if not name:
            await utils.answer(message, self.strings["no_args"])
            return

        await utils.answer(
            message,
            self.strings["hello"].format(utils.escape_html(name)),
        )

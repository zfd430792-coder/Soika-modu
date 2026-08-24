"""Вопрос к нейросети прямо из чата — через Groq.

Спрашивает командой или ответом на сообщение. Модель выбирается из живого
списка, который отдаёт сам Groq, а не из зашитого в код — так он не
устаревает. Имена моделей показываются по-человечески.

Ключ бесплатно берётся на https://console.groq.com/keys
"""

# meta developer: @soika
# meta description: .ai <вопрос> — ответ нейросети через Groq, с выбором модели из живого списка
# requires: aiohttp

import html
import logging
import re
import time

import aiohttp

from .. import loader, utils

logger = logging.getLogger(__name__)

BASE = "https://api.groq.com/openai/v1"
KEYS = "https://console.groq.com/keys"

#: Куски имён, которые сами по себе не угадываются
NAMES = {
    "ai": "AI",
    "allam": "ALLaM",
    "arabic": "Arabic",
    "audio": "Audio",
    "beta": "Beta",
    "chat": "Chat",
    "coder": "Coder",
    "compound": "Compound",
    "deepseek": "DeepSeek",
    "distill": "Distill",
    "en": "EN",
    "gemma": "Gemma",
    "gpt": "GPT",
    "guard": "Guard",
    "hindi": "Hindi",
    "instant": "Instant",
    "instruct": "Instruct",
    "it": "Instruct",
    "kimi": "Kimi",
    "large": "Large",
    "llama": "Llama",
    "math": "Math",
    "maverick": "Maverick",
    "medium": "Medium",
    "mini": "Mini",
    "mixtral": "Mixtral",
    "moonshotai": "Moonshot AI",
    "nano": "Nano",
    "openai": "OpenAI",
    "oss": "OSS",
    "playai": "PlayAI",
    "preview": "Preview",
    "prompt": "Prompt",
    "qwen": "Qwen",
    "safety": "Safety",
    "saba": "Saba",
    "scout": "Scout",
    "small": "Small",
    "specdec": "SpecDec",
    "tool": "Tool",
    "tts": "TTS",
    "turbo": "Turbo",
    "use": "Use",
    "versatile": "Versatile",
    "vision": "Vision",
    "whisper": "Whisper",
}

#: Что склеивается обратно после разбора по кусочкам
GLUE = (("GPT OSS", "GPT-OSS"), ("Tool Use", "Tool-Use"), ("Moonshot AI", "Moonshot"))

#: Эти модели не для разговора — их не предлагаем как собеседника
NOT_CHAT = ("whisper", "tts", "guard", "prompt-guard", "embed")


@loader.tds
class ИИМод(loader.Module):
    """Вопрос к нейросети через Groq"""

    strings = {
        "name": "ИИ",
        # ── спросить ────────────────────────────────────────────────────
        "usage": (
            "🤖 <b>Спросить нейросеть</b>\n\n"
            "<code>{0}ai вопрос</code> — спросить\n"
            "<code>{0}ai</code> ответом на сообщение — про это сообщение\n"
            "<code>{0}aimodels</code> — список моделей и выбор\n"
            "<code>{0}airole текст</code> — задать характер\n\n"
            "<i>Ключ бесплатно:</i> {1}"
        ),
        "no_key": (
            "🔑 <b>Нужен ключ Groq</b>\n\n"
            "├ Забери бесплатный: {0}\n"
            "└ Пропиши: <code>{1}aikey ключ</code>\n\n"
            "<i>Ключ хранится скрытым и в чате не остаётся.</i>"
        ),
        "thinking": "💭 <b>{}</b> думает…",
        "answer": "🤖 <b>{}</b>\n\n{}",
        "answer_bare": "{}",
        "spent": "\n\n<i>{} · {}</i>",
        "tokens": "{} токенов",
        "seconds": "{} с",
        # ── ключ и роль ─────────────────────────────────────────────────
        "key_saved": (
            "🔑 <b>Ключ записан</b>\n\n"
            "<i>Сообщение с ключом заменено — в переписке его не осталось.</i>"
        ),
        "key_gone": "🔑 <b>Ключ убран</b>",
        "key_short": "🚫 <b>На ключ не похоже</b>\n\n<i>Ключи Groq начинаются с</i> <code>gsk_</code>",
        "role_saved": "🎭 <b>Характер задан</b>\n\n<code>{}</code>",
        "role_gone": "🎭 <b>Характер убран</b> — модель отвечает как обычно",
        "role_how": "🎭 <b>Задать характер:</b>\n<code>{0}airole отвечай коротко и по делу</code>\n\n<i>Убрать:</i> <code>{0}airole</code>",
        # ── модели ──────────────────────────────────────────────────────
        "models": "🧠 <b>Модели Groq</b> · {}\n\n{}",
        "models_chat": "💬 <b>Для разговора</b>",
        "models_other": "🎧 <b>Прочее</b>",
        "model_row": "▫️ <b>{}</b>\n     <code>{}</code>{}",
        "model_tail": " · {}",
        "model_now": "✅ <b>{}</b>\n     <code>{}</code>{}",
        "models_foot": "<i>Выбрать — кнопкой ниже или</i> <code>{}cfg</code>",
        "model_set": "🧠 <b>Теперь отвечает {}</b>",
        "model_empty": "🧠 <b>Groq не отдал ни одной модели</b>",
        # ── настройки ───────────────────────────────────────────────────
        "cfg": "⚙️ <b>ИИ</b>\n\n{}",
        "lbl_model": "Модель",
        "lbl_key": "Ключ",
        "lbl_role": "Характер",
        "lbl_heat": "Разброс",
        "lbl_length": "Длина ответа",
        "lbl_reply": "Контекст из реплая",
        "key_yes": "записан",
        "key_no": "<i>нет</i>",
        "role_no": "<i>не задан</i>",
        "reply_yes": "беру",
        "reply_no": "не беру",
        "heat_low": "{} — сухо",
        "heat_mid": "{} — обычно",
        "heat_high": "{} — вольно",
        # ── беды ────────────────────────────────────────────────────────
        "bad_key": (
            "🔑 <b>Groq не принял ключ</b>\n\n"
            "<i>Проверь его или заведи новый:</i> {}"
        ),
        "no_model": (
            "🧠 <b>Модели</b> <code>{}</code> <b>у Groq нет</b>\n\n"
            "<i>Живой список:</i> <code>{}aimodels</code>"
        ),
        "too_often": "⏳ <b>Слишком часто</b>\n\n<i>Groq просит подождать {}.</i>",
        "too_long": (
            "📏 <b>Не влезло в модель</b>\n\n"
            "<i>Сократи вопрос или возьми модель с большим контекстом:</i>"
            " <code>{}aimodels</code>"
        ),
        "no_answer": "🤷 <b>Модель ответила пустотой</b>",
        "broke": "⚠️ <b>Groq ответил {}</b>\n<code>{}</code>",
        "timeout": "⏳ <b>Groq не ответил вовремя</b>",
        # ── кнопки ──────────────────────────────────────────────────────
        "btn_models": "🧠 Модели",
        "btn_role": "🎭 Характер",
        "btn_heat": "🌡 Разброс: {}",
        "btn_length": "📏 Длина: {}",
        "btn_reply": "↩️ Реплай: {}",
        "btn_sign": "🏷 Подпись: {}",
        "btn_back": "⬅️ Назад",
        "btn_close": "✖️ Закрыть",
        "on": "вкл",
        "off": "выкл",
        "cb_saved": "✅ Готово",
        "cb_working": "⏳ Секунду…",
    }

    strings_en = {
        "usage": (
            "🤖 <b>Ask a neural network</b>\n\n"
            "<code>{0}ai question</code> — ask\n"
            "<code>{0}ai</code> replying to a message — about that message\n"
            "<code>{0}aimodels</code> — list and pick a model\n"
            "<code>{0}airole text</code> — set the persona\n\n"
            "<i>Free key:</i> {1}"
        ),
        "no_key": (
            "🔑 <b>A Groq key is needed</b>\n\n"
            "├ Grab a free one: {0}\n"
            "└ Set it: <code>{1}aikey key</code>\n\n"
            "<i>The key is stored hidden and does not stay in the chat.</i>"
        ),
        "thinking": "💭 <b>{}</b> is thinking…",
        "answer": "🤖 <b>{}</b>\n\n{}",
        "answer_bare": "{}",
        "spent": "\n\n<i>{} · {}</i>",
        "tokens": "{} tokens",
        "seconds": "{}s",
        "key_saved": (
            "🔑 <b>Key saved</b>\n\n"
            "<i>The message with the key was replaced — it is not in the chat.</i>"
        ),
        "key_gone": "🔑 <b>Key removed</b>",
        "key_short": "🚫 <b>Does not look like a key</b>\n\n<i>Groq keys start with</i> <code>gsk_</code>",
        "role_saved": "🎭 <b>Persona set</b>\n\n<code>{}</code>",
        "role_gone": "🎭 <b>Persona cleared</b> — the model answers as usual",
        "role_how": "🎭 <b>Set a persona:</b>\n<code>{0}airole answer short and to the point</code>\n\n<i>Clear:</i> <code>{0}airole</code>",
        "models": "🧠 <b>Groq models</b> · {}\n\n{}",
        "models_chat": "💬 <b>For chatting</b>",
        "models_other": "🎧 <b>Other</b>",
        "model_row": "▫️ <b>{}</b>\n     <code>{}</code>{}",
        "model_tail": " · {}",
        "model_now": "✅ <b>{}</b>\n     <code>{}</code>{}",
        "models_foot": "<i>Pick one with a button below or via</i> <code>{}cfg</code>",
        "model_set": "🧠 <b>Now answering: {}</b>",
        "model_empty": "🧠 <b>Groq returned no models</b>",
        "cfg": "⚙️ <b>AI</b>\n\n{}",
        "lbl_model": "Model",
        "lbl_key": "Key",
        "lbl_role": "Persona",
        "lbl_heat": "Spread",
        "lbl_length": "Answer length",
        "lbl_reply": "Context from reply",
        "key_yes": "saved",
        "key_no": "<i>none</i>",
        "role_no": "<i>none</i>",
        "reply_yes": "used",
        "reply_no": "ignored",
        "heat_low": "{} — dry",
        "heat_mid": "{} — normal",
        "heat_high": "{} — loose",
        "bad_key": (
            "🔑 <b>Groq rejected the key</b>\n\n"
            "<i>Check it or make a new one:</i> {}"
        ),
        "no_model": (
            "🧠 <b>Groq has no model</b> <code>{}</code>\n\n"
            "<i>Live list:</i> <code>{}aimodels</code>"
        ),
        "too_often": "⏳ <b>Too often</b>\n\n<i>Groq asks to wait {}.</i>",
        "too_long": (
            "📏 <b>Did not fit the model</b>\n\n"
            "<i>Shorten the question or pick a bigger context:</i>"
            " <code>{}aimodels</code>"
        ),
        "no_answer": "🤷 <b>The model answered with nothing</b>",
        "broke": "⚠️ <b>Groq answered {}</b>\n<code>{}</code>",
        "timeout": "⏳ <b>Groq did not answer in time</b>",
        "btn_models": "🧠 Models",
        "btn_role": "🎭 Persona",
        "btn_heat": "🌡 Spread: {}",
        "btn_length": "📏 Length: {}",
        "btn_reply": "↩️ Reply: {}",
        "btn_sign": "🏷 Caption: {}",
        "btn_back": "⬅️ Back",
        "btn_close": "✖️ Close",
        "on": "on",
        "off": "off",
        "cb_saved": "✅ Done",
        "cb_working": "⏳ One moment…",
    }

    config = loader.ModuleConfig(
        loader.ConfigValue(
            "key",
            "",
            f"Ключ Groq. Бесплатно берётся тут: {KEYS}",
            validator=loader.validators.Hidden(),
        ),
        loader.ConfigValue(
            "model",
            "llama-3.3-70b-versatile",
            (
                "Какая модель отвечает. Живой список — .aimodels,"
                " там же выбор кнопкой"
            ),
            validator=loader.validators.String(max_len=120),
        ),
        loader.ConfigValue(
            "role",
            "",
            "Характер: что модель должна делать всегда. Пусто — как обычно",
            validator=loader.validators.String(max_len=1000),
        ),
        loader.ConfigValue(
            "heat",
            0.6,
            "Разброс ответов: 0 — сухо и предсказуемо, 2 — вольно",
            validator=loader.validators.Float(minimum=0.0, maximum=2.0),
        ),
        loader.ConfigValue(
            "length",
            1024,
            "Сколько токенов модель может потратить на ответ",
            validator=loader.validators.Integer(minimum=64, maximum=32768),
        ),
        loader.ConfigValue(
            "use_reply",
            True,
            "Брать сообщение, на которое отвечаешь, как контекст",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "sign",
            True,
            "Подписывать ответ именем модели и временем",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "timeout",
            120,
            "Сколько секунд ждать ответа",
            validator=loader.validators.Integer(minimum=10, maximum=600),
        ),
    )

    # ------------------------------------------------------------------ #
    #  Команды
    # ------------------------------------------------------------------ #
    @loader.command(aliases=["ии", "спроси"])
    async def aicmd(self, message):
        """<вопрос> — спросить нейросеть. Можно ответом на сообщение"""
        if not self.config["key"]:
            await utils.answer(message, self.strings["no_key"].format(KEYS, self._prefix))
            return

        question, context = await self._question(message)

        if not question:
            await utils.answer(
                message, self.strings["usage"].format(self._prefix, KEYS)
            )
            return

        title = self._pretty(self.config["model"])
        sent = await utils.answer(message, self.strings["thinking"].format(title))
        started = time.monotonic()
        answer = await self._ask(question, context)

        if isinstance(answer, str):
            await utils.answer(sent, answer)
            return

        body = self._to_html(answer["text"])
        spent = ""

        if self.config["sign"]:
            spent = self.strings["spent"].format(
                self.strings["seconds"].format(round(time.monotonic() - started, 1)),
                self.strings["tokens"].format(answer["tokens"]),
            )

        head = "answer" if self.config["sign"] else "answer_bare"
        await utils.answer(
            sent,
            (
                self.strings[head].format(title, body)
                if self.config["sign"]
                else self.strings[head].format(body)
            )
            + spent,
        )

    @loader.command(aliases=["иимодели"])
    async def aimodelscmd(self, message):
        """— живой список моделей Groq с человеческими именами и выбором"""
        if not self.config["key"]:
            await utils.answer(message, self.strings["no_key"].format(KEYS, self._prefix))
            return

        sent = await utils.answer(message, self.strings["cb_working"])
        found = await self._models()

        if isinstance(found, str):
            await utils.answer(sent, found)
            return

        if not found:
            await utils.answer(sent, self.strings["model_empty"])
            return

        await self._show(sent, found)

    @loader.owner
    @loader.command(aliases=["ииключ"])
    async def aikeycmd(self, message):
        """<ключ> — записать ключ Groq; без ключа — убрать"""
        key = utils.get_args_raw(message).strip()

        if not key:
            self.config["key"] = ""
            await utils.answer(message, self.strings["key_gone"])
            return

        if not key.startswith("gsk_") or len(key) < 20:
            await utils.answer(message, self.strings["key_short"])
            return

        self.config["key"] = key
        # Ответ затирает исходный текст, так что ключ в переписке не остаётся
        await utils.answer(message, self.strings["key_saved"])

    @loader.command(aliases=["иироль"])
    async def airolecmd(self, message):
        """<текст> — задать характер модели; без текста — показать и убрать"""
        role = utils.get_args_raw(message).strip()

        if not role:
            if self.config["role"]:
                self.config["role"] = ""
                await utils.answer(message, self.strings["role_gone"])
                return

            await utils.answer(message, self.strings["role_how"].format(self._prefix))
            return

        self.config["role"] = role
        await utils.answer(
            message, self.strings["role_saved"].format(utils.escape_html(role))
        )

    @loader.command(aliases=["иинастройки"])
    async def aicfgcmd(self, message):
        """— настройки: модель, характер, разброс, длина. Всё кнопками"""
        await self._answer(message, self._card(), self._markup())

    # ------------------------------------------------------------------ #
    #  Разговор с Groq
    # ------------------------------------------------------------------ #
    async def _ask(self, question: str, context: str):
        """Ответ модели либо строка с бедой."""
        messages = []

        if self.config["role"]:
            messages.append({"role": "system", "content": self.config["role"]})

        if context:
            question = f"{context}\n\n{question}"

        messages.append({"role": "user", "content": question})
        payload = {
            "model": self.config["model"],
            "messages": messages,
            "temperature": float(self.config["heat"]),
            "max_tokens": int(self.config["length"]),
            "stream": False,
        }
        answer = await self._call("POST", "/chat/completions", payload)

        if isinstance(answer, str):
            return answer

        choices = answer.get("choices") or []
        text = ((choices[0] if choices else {}).get("message") or {}).get("content") or ""

        if not text.strip():
            return self.strings["no_answer"]

        return {
            "text": text.strip(),
            "tokens": (answer.get("usage") or {}).get("total_tokens") or 0,
        }

    async def _models(self):
        """Список моделей либо строка с бедой."""
        answer = await self._call("GET", "/models")

        if isinstance(answer, str):
            return answer

        found = []

        for item in answer.get("data") or []:
            name = item.get("id")

            if not name:
                continue

            found.append({
                "id": name,
                "name": self._pretty(name),
                "window": item.get("context_window") or 0,
                "owner": item.get("owned_by") or "",
                "chat": not any(mark in name.lower() for mark in NOT_CHAT),
            })

        return sorted(found, key=lambda item: (not item["chat"], item["name"]))

    async def _call(self, method: str, path: str, payload: dict = None):
        """Один запрос к Groq. Словарь — удача, строка — беда для показа."""
        headers = {
            "Authorization": f"Bearer {self.config['key']}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self.config["timeout"])

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method, f"{BASE}{path}", json=payload, headers=headers
                ) as response:
                    if response.status == 200:
                        return await response.json(content_type=None)

                    return await self._trouble(response)
        except (aiohttp.ServerTimeoutError, TimeoutError):
            return self.strings["timeout"]
        except Exception:
            logger.exception("Groq не ответил")
            return self.strings["broke"].format("—", "нет связи")

    async def _trouble(self, response) -> str:
        """Понятный текст вместо голого кода ответа."""
        try:
            body = await response.json(content_type=None)
            note = ((body or {}).get("error") or {}).get("message") or ""
            code = ((body or {}).get("error") or {}).get("code") or ""
        except Exception:
            note, code = "", ""

        if response.status == 401:
            return self.strings["bad_key"].format(KEYS)

        if response.status == 404 or "model" in str(code):
            return self.strings["no_model"].format(
                utils.escape_html(self.config["model"]), self._prefix
            )

        if response.status == 429:
            wait = response.headers.get("retry-after") or "?"
            return self.strings["too_often"].format(
                self.strings["seconds"].format(wait)
            )

        if response.status == 413 or "context" in str(code) or "length" in str(code):
            return self.strings["too_long"].format(self._prefix)

        return self.strings["broke"].format(
            response.status, utils.escape_html(note[:150] or "—")
        )

    # ------------------------------------------------------------------ #
    #  Имена моделей
    # ------------------------------------------------------------------ #
    @staticmethod
    def _pretty(name: str) -> str:
        """Из «meta-llama/llama-4-scout-17b» сделать «Llama 4 Scout 17B».

        Разбираем любой id, а не только знакомый: новые модели у Groq
        появляются постоянно, и зашитая таблица устарела бы через месяц.
        """
        if not name:
            return "—"

        words = []

        for piece in str(name).rsplit("/", 1)[-1].replace("_", "-").split("-"):
            piece = piece.strip().lower()

            if not piece:
                continue

            # Голое большое число — это размер окна, в имени оно лишнее
            if piece.isdigit() and int(piece) >= 1000:
                continue

            if re.fullmatch(r"\d+(\.\d+)?", piece):
                words.append(piece)
            elif re.fullmatch(r"v\d+", piece):
                words.append(piece)
            elif re.fullmatch(r"\d+x\d+b", piece):
                words.append(piece.upper().replace("X", "×"))
            elif re.fullmatch(r"\d+[bmke]", piece):
                words.append(piece.upper())
            elif piece in NAMES:
                words.append(NAMES[piece])
            elif re.fullmatch(r"[a-z]+\d+(\.\d+)?", piece):
                letters = re.match(r"[a-z]+", piece).group(0)
                digits = piece[len(letters):]

                if letters in NAMES:
                    # llama3 → Llama 3, gemma2 → Gemma 2
                    words.append(NAMES[letters])
                    words.append(digits)
                elif len(letters) <= 2:
                    # r1, k2, o3 — это цельное имя, разрывать нельзя
                    words.append(letters.upper() + digits)
                else:
                    words.append(letters.capitalize())
                    words.append(digits)
            else:
                words.append(piece.capitalize())

        pretty = " ".join(words)

        for was, now in GLUE:
            pretty = pretty.replace(was, now)

        return pretty or str(name)

    # ------------------------------------------------------------------ #
    #  Ответ модели в разметку Telegram
    # ------------------------------------------------------------------ #
    def _to_html(self, text: str) -> str:
        """Markdown от модели в HTML, который Telegram понимает."""
        blocks = []

        def stash(found):
            body = found.group(2).strip("\n")
            language = (found.group(1) or "").strip()
            blocks.append(
                f'<pre><code class="language-{html.escape(language)}">'
                f"{html.escape(body)}</code></pre>"
                if language
                else f"<pre>{html.escape(body)}</pre>"
            )
            return f"\x00{len(blocks) - 1}\x00"

        text = re.sub(r"```(\w*)\n?(.*?)```", stash, text, flags=re.DOTALL)
        text = html.escape(text)
        text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
        text = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<i>\1</i>", text)
        text = re.sub(r"(?<![\w_])_([^_\n]+)_(?![\w_])", r"<i>\1</i>", text)
        text = re.sub(r"~~([^~\n]+)~~", r"<s>\1</s>", text)
        text = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*[-*+]\s+", "• ", text, flags=re.MULTILINE)

        return re.sub(r"\x00(\d+)\x00", lambda m: blocks[int(m.group(1))], text)

    # ------------------------------------------------------------------ #
    #  Список моделей
    # ------------------------------------------------------------------ #
    async def _show(self, message, found: list) -> None:
        text = self._list(found)
        picks = [item for item in found if item["chat"]][:10]
        rows, pair = [], []

        for item in picks:
            pair.append({
                "text": item["name"],
                "callback": self._pick,
                "args": (item["id"],),
            })

            if len(pair) == 2:
                rows.append(pair)
                pair = []

        if pair:
            rows.append(pair)

        rows.append([{"text": self.strings["btn_close"], "callback": self._close}])
        await self._answer(message, text, rows)

    def _list(self, found: list) -> str:
        chat = [item for item in found if item["chat"]]
        other = [item for item in found if not item["chat"]]
        parts = []

        for title, group in (
            (self.strings["models_chat"], chat),
            (self.strings["models_other"], other),
        ):
            if not group:
                continue

            parts.append(
                title + "\n" + "\n".join(self._row(item) for item in group)
            )

        parts.append(self.strings["models_foot"].format(self._prefix))

        return self.strings["models"].format(len(found), "\n\n".join(parts))

    def _row(self, item: dict) -> str:
        tail = []

        if item["window"]:
            tail.append(self._window(item["window"]))

        if item["owner"]:
            tail.append(utils.escape_html(item["owner"]))

        key = "model_now" if item["id"] == self.config["model"] else "model_row"

        return self.strings[key].format(
            utils.escape_html(item["name"]),
            utils.escape_html(item["id"]),
            self.strings["model_tail"].format(" · ".join(tail)) if tail else "",
        )

    @staticmethod
    def _window(size: int) -> str:
        if size >= 1000:
            return f"{round(size / 1024)}K"

        return str(size)

    # ------------------------------------------------------------------ #
    #  Настройки
    # ------------------------------------------------------------------ #
    def _card(self) -> str:
        role = self.config["role"]
        rows = [
            (self.strings["lbl_model"], utils.escape_html(self._pretty(self.config["model"]))),
            (self.strings["lbl_key"], self.strings["key_yes" if self.config["key"] else "key_no"]),
            (
                self.strings["lbl_role"],
                utils.escape_html(role[:60] + "…" if len(role) > 60 else role)
                if role
                else self.strings["role_no"],
            ),
            (self.strings["lbl_heat"], self._heat()),
            (self.strings["lbl_length"], self.config["length"]),
            (
                self.strings["lbl_reply"],
                self.strings["reply_yes" if self.config["use_reply"] else "reply_no"],
            ),
        ]
        lines = []

        for index, (label, value) in enumerate(rows):
            tie = "└" if index == len(rows) - 1 else "├"
            lines.append(f"{tie} <b>{label}:</b> {value}")

        return self.strings["cfg"].format("\n".join(lines))

    def _heat(self) -> str:
        heat = round(float(self.config["heat"]), 1)

        if heat <= 0.4:
            return self.strings["heat_low"].format(heat)

        if heat <= 1.0:
            return self.strings["heat_mid"].format(heat)

        return self.strings["heat_high"].format(heat)

    def _markup(self) -> list:
        return [
            [
                {"text": self.strings["btn_models"], "callback": self._to_models},
                {"text": self.strings["btn_role"], "callback": self._role_how},
            ],
            [
                {
                    "text": self.strings["btn_heat"].format(
                        round(float(self.config["heat"]), 1)
                    ),
                    "callback": self._cycle,
                    "args": ("heat",),
                },
                {
                    "text": self.strings["btn_length"].format(self.config["length"]),
                    "callback": self._cycle,
                    "args": ("length",),
                },
            ],
            [
                {
                    "text": self.strings["btn_reply"].format(
                        self.strings["on" if self.config["use_reply"] else "off"]
                    ),
                    "callback": self._toggle,
                    "args": ("use_reply",),
                },
                {
                    "text": self.strings["btn_sign"].format(
                        self.strings["on" if self.config["sign"] else "off"]
                    ),
                    "callback": self._toggle,
                    "args": ("sign",),
                },
            ],
            [{"text": self.strings["btn_close"], "callback": self._close}],
        ]

    # ------------------------------------------------------------------ #
    #  Кнопки
    # ------------------------------------------------------------------ #
    async def _pick(self, call, name: str) -> None:
        self.config["model"] = name
        await call.answer(self.strings["model_set"].format(self._pretty(name)))
        await call.edit(self._card(), reply_markup=self._markup())

    async def _to_models(self, call) -> None:
        await call.answer(self.strings["cb_working"])
        found = await self._models()

        if isinstance(found, str):
            await call.edit(found)
            return

        if not found:
            await call.edit(self.strings["model_empty"])
            return

        picks = [item for item in found if item["chat"]][:10]
        rows, pair = [], []

        for item in picks:
            pair.append({"text": item["name"], "callback": self._pick, "args": (item["id"],)})

            if len(pair) == 2:
                rows.append(pair)
                pair = []

        if pair:
            rows.append(pair)

        rows.append([
            {"text": self.strings["btn_back"], "callback": self._back},
            {"text": self.strings["btn_close"], "callback": self._close},
        ])
        await call.edit(self._list(found), reply_markup=rows)

    async def _back(self, call) -> None:
        await call.edit(self._card(), reply_markup=self._markup())

    async def _role_how(self, call) -> None:
        await call.answer(
            self.strings["role_how"].format(self._prefix).replace("<b>", "").replace("</b>", "")
            .replace("<code>", "").replace("</code>", "").replace("<i>", "").replace("</i>", ""),
            show_alert=True,
        )

    async def _cycle(self, call, key: str) -> None:
        steps = {"heat": [0.2, 0.6, 1.0, 1.4], "length": [512, 1024, 2048, 4096]}[key]
        now = float(self.config[key]) if key == "heat" else int(self.config[key])
        following = [step for step in steps if step > now]
        self.config[key] = following[0] if following else steps[0]
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
    async def _question(self, message):
        """Вопрос и контекст: из аргументов, из реплая или из обоих."""
        asked = (utils.get_args_raw(message) or "").strip()
        reply = await message.get_reply_message()
        quoted = (getattr(reply, "raw_text", None) or "").strip() if reply else ""

        if quoted and not self.config["use_reply"]:
            quoted = ""

        if asked and quoted:
            return asked, quoted

        if asked:
            return asked, ""

        return quoted, ""

    async def _answer(self, message, text: str, markup: list):
        """Ответить с кнопками, а без инлайн-бота — обычным текстом."""
        if self.inline is not None and self.inline.init_complete:
            if await self.inline.form(text, message=message, reply_markup=markup):
                return

        await utils.answer(message, text)

    @property
    def _prefix(self) -> str:
        return self.client.dispatcher.prefixes[0]

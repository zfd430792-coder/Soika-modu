"""Скачивает видео из TikTok без водяного знака.

Берёт ссылку, достаёт файл и присылает его обратно вместе с автором,
описанием и счётчиками. Источника два: сначала API самого TikTok, а если
он молчит — сторонний tikwm. Порядок настраивается.
"""

# meta developer: @soika
# meta description: .tt <ссылка> — видео из TikTok без водяного знака, с автором и описанием
# requires: aiohttp

import io
import logging
import random
import re
import struct

import aiohttp
from telethon.tl import types

from .. import loader, utils

logger = logging.getLogger(__name__)

#: Ссылка на TikTok в любом из ходовых видов
LINK = re.compile(
    r"https?://(?:www\.|m\.|vm\.|vt\.)?tiktok\.com/[^\s<>\"']+",
    re.IGNORECASE,
)

#: Номер ролика внутри полной ссылки
AWEME = re.compile(r"/(?:video|photo|v)/(\d+)|[?&]item_id=(\d+)")

#: Короткие ссылки, которые сначала надо развернуть
SHORT = re.compile(r"//(?:vm|vt)\.tiktok\.com/|/t/", re.IGNORECASE)

#: Зеркала одного и того же API. Одно отвечает 429 — идём к следующему
HOSTS = (
    "api16-normal-c-useast1a.tiktokv.com",
    "api19-normal-c-useast1a.tiktokv.com",
    "api22-normal-c-useast2a.tiktokv.com",
    "api16-normal-c-alisg.tiktokv.com",
    "api-h2.tiktokv.com",
)

TIKWM = "https://www.tikwm.com/api/"

ANDROID = (
    "com.ss.android.ugc.trill/2613 (Linux; U; Android 12; ru_RU; SM-G991B;"
    " Build/SP1A.210812.016; Cronet/58.0.2991.0)"
)
BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


@loader.tds
class ТиктокМод(loader.Module):
    """Видео из TikTok без водяного знака"""

    strings = {
        "name": "Тикток",
        "usage": (
            "🎬 <b>Скачать из TikTok</b>\n\n"
            "<code>{0}tt ссылка</code> — прислать видео без водяного знака\n"
            "<code>{0}tt</code> ответом на сообщение со ссылкой — то же самое"
        ),
        "no_link": (
            "🚫 <b>Ссылки на TikTok тут нет</b>\n\n"
            "<i>Годится и короткая</i> <code>vm.tiktok.com/…</code><i>,"
            " и полная</i> <code>tiktok.com/@ник/video/…</code>"
        ),
        "working": "⏳ <b>Тащу видео…</b>",
        "failed": "⚠️ <b>Не вышло достать видео</b>\n\n{}\n\n{}",
        "foot_order": (
            "<i>Если это повторяется, поменяй порядок источников в</i>"
            " <code>{}cfg</code>"
        ),
        "foot_blocked": (
            "🚧 <b>Похоже, TikTok режет твой сервер</b>\n"
            "<i>Пропиши прокси:</i> <code>{}cfg</code> <i>→ Тикток → proxy</i>"
        ),
        "line_failed": "├ <b>{}:</b> {}",
        "line_last": "└ <b>{}:</b> {}",
        "reason_empty": "ничего не вернул",
        "reason_http": "ответил {}",
        "reason_http_body": "ответил {} · <code>{}</code>",
        "reason_hosts": "все зеркала · <code>{}</code>",
        "reason_said": "отказал · <code>{}</code>",
        "reason_timeout": "не ответил вовремя",
        "reason_broken": "прислал непонятное",
        "too_big": (
            "🐘 <b>Ролик тяжелее лимита</b>\n\n"
            "├ <b>Весит:</b> {}\n"
            "└ <b>Разрешено:</b> {}\n\n"
            "<i>Лимит поднимается в</i> <code>{}cfg</code>"
        ),
        "no_video": (
            "🚫 <b>В посте нет видео</b>\n\n"
            "<i>Это фотопост, а показ фотопостов выключен в</i>"
            " <code>{}cfg</code>"
        ),
        # ── подпись ─────────────────────────────────────────────────────
        "by": "🎵 <b>{}</b>",
        "by_nick": "🎵 <b>{}</b> · <code>@{}</code>",
        "stats": "├ ❤️ {} · 💬 {} · 🔁 {}",
        "views": "├ 👁 {}",
        "music": "├ 🎧 {}",
        "meta": "└ ⏱ {} · {} · <i>{}</i>",
        "meta_photos": "└ 🖼 {} · {} · <i>{}</i>",
        "photos_count": "{} фото",
        # ── единицы ─────────────────────────────────────────────────────
        "u_k": "{} тыс.",
        "u_m": "{} млн",
    }

    strings_en = {
        "usage": (
            "🎬 <b>Download from TikTok</b>\n\n"
            "<code>{0}tt link</code> — send the video without a watermark\n"
            "<code>{0}tt</code> replying to a message with a link — same thing"
        ),
        "no_link": (
            "🚫 <b>No TikTok link here</b>\n\n"
            "<i>Both short</i> <code>vm.tiktok.com/…</code> <i>and full</i>"
            " <code>tiktok.com/@name/video/…</code> <i>work</i>"
        ),
        "working": "⏳ <b>Fetching the video…</b>",
        "failed": "⚠️ <b>Could not get the video</b>\n\n{}\n\n{}",
        "foot_order": (
            "<i>If this keeps happening, reorder the sources in</i>"
            " <code>{}cfg</code>"
        ),
        "foot_blocked": (
            "🚧 <b>Looks like TikTok is throttling your server</b>\n"
            "<i>Set a proxy:</i> <code>{}cfg</code> <i>→ TikTok → proxy</i>"
        ),
        "line_failed": "├ <b>{}:</b> {}",
        "line_last": "└ <b>{}:</b> {}",
        "reason_empty": "returned nothing",
        "reason_http": "answered {}",
        "reason_http_body": "answered {} · <code>{}</code>",
        "reason_hosts": "every mirror refused · <code>{}</code>",
        "reason_said": "refused · <code>{}</code>",
        "reason_timeout": "timed out",
        "reason_broken": "sent something unreadable",
        "too_big": (
            "🐘 <b>The clip is over the limit</b>\n\n"
            "├ <b>Weighs:</b> {}\n"
            "└ <b>Allowed:</b> {}\n\n"
            "<i>Raise the limit in</i> <code>{}cfg</code>"
        ),
        "no_video": (
            "🚫 <b>No video in this post</b>\n\n"
            "<i>It is a photo post, and photo posts are off in</i>"
            " <code>{}cfg</code>"
        ),
        "by": "🎵 <b>{}</b>",
        "by_nick": "🎵 <b>{}</b> · <code>@{}</code>",
        "stats": "├ ❤️ {} · 💬 {} · 🔁 {}",
        "views": "├ 👁 {}",
        "music": "├ 🎧 {}",
        "meta": "└ ⏱ {} · {} · <i>{}</i>",
        "meta_photos": "└ 🖼 {} · {} · <i>{}</i>",
        "photos_count": "{} photos",
        "u_k": "{}K",
        "u_m": "{}M",
    }

    config = loader.ModuleConfig(
        loader.ConfigValue(
            "sources",
            ["tiktok", "tikwm"],
            (
                "Откуда брать, по порядку. tiktok — напрямую у TikTok, ссылка"
                " никуда больше не уходит; tikwm — сторонний сервис, он увидит,"
                " что ты качаешь"
            ),
            validator=loader.validators.Series(
                validator=loader.validators.Choice(["tiktok", "tikwm"])
            ),
        ),
        loader.ConfigValue(
            "caption",
            "full",
            "Подпись под видео: полная, короткая или без неё",
            validator=loader.validators.Choice(["full", "short", "none"]),
        ),
        loader.ConfigValue(
            "hd",
            False,
            "Просить качество получше (медленнее и тяжелее)",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "photos",
            True,
            "Присылать фотопосты альбомом",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "as_file",
            False,
            "Слать документом, а не видео",
            validator=loader.validators.Boolean(),
        ),
        loader.ConfigValue(
            "max_mb",
            100,
            "Тяжелее скольки мегабайт не качать",
            validator=loader.validators.Integer(minimum=1, maximum=2000),
        ),
        loader.ConfigValue(
            "proxy",
            "",
            (
                "HTTP-прокси, если TikTok режет твой сервер:"
                " http://логин:пароль@хост:порт. Пусто — без прокси"
            ),
            validator=loader.validators.String(max_len=256),
        ),
        loader.ConfigValue(
            "timeout",
            60,
            "Сколько секунд ждать ответа",
            validator=loader.validators.Integer(minimum=5, maximum=300),
        ),
    )

    # ------------------------------------------------------------------ #
    #  Команда
    # ------------------------------------------------------------------ #
    @loader.command(aliases=["тт", "tiktok"])
    async def ttcmd(self, message):
        """<ссылка> — видео из TikTok без водяного знака"""
        link = await self._link(message)

        if link is None:
            args = utils.get_args_raw(message)
            await utils.answer(
                message,
                self.strings["no_link"] if args else self.strings["usage"].format(self._prefix),
            )
            return

        sent = await utils.answer(message, self.strings["working"])
        timeout = aiohttp.ClientTimeout(total=self.config["timeout"])
        troubles = []

        async with aiohttp.ClientSession(timeout=timeout) as session:
            post = await self._fetch(session, link, troubles)

            if post is None:
                await utils.answer(sent, self._trouble_card(troubles))
                return

            if not post.get("video") and not post.get("images"):
                await utils.answer(sent, self._trouble_card(troubles))
                return

            if not post.get("video"):
                if not self.config["photos"]:
                    await utils.answer(sent, self.strings["no_video"].format(self._prefix))
                    return

                await self._send_photos(session, sent, post)
                return

            await self._send_video(session, sent, post)

    # ------------------------------------------------------------------ #
    #  Добыча
    # ------------------------------------------------------------------ #
    async def _fetch(self, session, link: str, troubles: list):
        """Обойти источники по порядку; удачный запомнить на будущее."""
        order = [
            name
            for name in (self.config["sources"] or ["tiktok", "tikwm"])
            if name in {"tiktok", "tikwm"}
        ] or ["tiktok", "tikwm"]

        lucky = self.get("lucky")

        if lucky in order:
            order.remove(lucky)
            order.insert(0, lucky)

        for name in order:
            handler = getattr(self, f"_via_{name}")

            try:
                post = await handler(session, link)
            except (aiohttp.ServerTimeoutError, TimeoutError):
                troubles.append((name, self.strings["reason_timeout"]))
                continue
            except aiohttp.ClientResponseError as error:
                troubles.append((name, self.strings["reason_http"].format(error.status)))
                continue
            except Exception:
                logger.exception("Источник %s не отдал ролик", name)
                troubles.append((name, self.strings["reason_broken"]))
                continue

            # Источник возвращает либо разобранный пост, либо строку с тем,
            # почему не вышло — её и показываем.
            if post is None:
                troubles.append((name, self.strings["reason_empty"]))
                continue

            if isinstance(post, str):
                troubles.append((name, post))
                continue

            self.set("lucky", name)
            post["source"] = name

            return post

        return None

    async def _via_tiktok(self, session, link: str):
        """Спросить сам TikTok — ссылка никуда на сторону не уходит."""
        full = await self._expand(session, link)
        found = AWEME.search(full)

        if not found:
            return None

        aweme = found.group(1) or found.group(2)
        # Идентификаторы разные при каждом запросе: лимит у TikTok висит на
        # устройстве, и с одним и тем же он быстро упирается в 429.
        params = {
            "aweme_id": aweme,
            "device_id": str(random.randint(10 ** 18, 10 ** 19 - 1)),
            "iid": str(random.randint(10 ** 18, 10 ** 19 - 1)),
            "openudid": f"{random.getrandbits(64):016x}",
            "version_code": "300904",
            "version_name": "30.9.4",
            "app_name": "musical_ly",
            "channel": "googleplay",
            "device_platform": "android",
            "device_type": "SM-G991B",
            "device_brand": "samsung",
            "os_version": "12",
            "resolution": "1080*2400",
            "dpi": "420",
            "ssmix": "a",
            "aid": "1233",
        }
        headers = {"User-Agent": ANDROID, "Accept": "application/json"}
        answers = []

        for host in HOSTS:
            async with session.get(
                f"https://{host}/aweme/v1/feed/",
                params=params,
                headers=headers,
                proxy=self._proxy,
            ) as response:
                if response.status != 200:
                    answers.append(str(response.status))
                    continue

                payload = await response.json(content_type=None)

            items = (payload or {}).get("aweme_list") or []

            if items and str(items[0].get("aweme_id")) == aweme:
                return self._read_tiktok(items[0])

            answers.append(self.strings["reason_empty"])

        return self.strings["reason_hosts"].format(
            utils.escape_html(", ".join(dict.fromkeys(answers)))
        )

    def _read_tiktok(self, item: dict) -> dict:
        """Разложить ответ TikTok по своим полочкам."""
        video = item.get("video") or {}
        author = item.get("author") or {}
        music = item.get("music") or {}
        counts = item.get("statistics") or {}

        images = [
            urls[0]
            for urls in (
                (picture.get("display_image") or {}).get("url_list")
                for picture in ((item.get("image_post_info") or {}).get("images") or [])
            )
            if urls
        ]

        return {
            "video": self._first((video.get("play_addr") or {}).get("url_list")),
            "images": images,
            "title": item.get("desc") or "",
            "author": author.get("nickname") or "",
            "nick": author.get("unique_id") or "",
            "music": " — ".join(
                part for part in (music.get("title"), music.get("author")) if part
            ),
            "duration": round((video.get("duration") or 0) / 1000),
            "width": video.get("width") or 0,
            "height": video.get("height") or 0,
            "likes": counts.get("digg_count") or 0,
            "comments": counts.get("comment_count") or 0,
            "shares": counts.get("share_count") or 0,
            "views": counts.get("play_count") or 0,
        }

    async def _via_tikwm(self, session, link: str):
        """Сторонний сервис: работает стабильнее, но видит твою ссылку.

        Ходим POST-ом и с полным набором заголовков браузера: на голый GET
        сервис отвечает 403.
        """
        headers = {
            "User-Agent": BROWSER,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.tikwm.com/",
            "Origin": "https://www.tikwm.com",
        }
        form = {"url": link, "hd": "1" if self.config["hd"] else "0"}

        async with session.post(
            TIKWM, data=form, headers=headers, proxy=self._proxy
        ) as response:
            if response.status != 200:
                return self._reason_http(response.status, await self._peek(response))

            payload = await response.json(content_type=None)

        if not payload:
            return None

        if payload.get("code") != 0:
            # Тут же прилетает и «Free Api Limit» — покажем как есть
            return self.strings["reason_said"].format(
                utils.escape_html(str(payload.get("msg") or "")[:80])
            )

        return self._read_tikwm(payload.get("data") or {})

    def _read_tikwm(self, data: dict) -> dict:
        """Разложить ответ tikwm по тем же полочкам."""
        music = data.get("music_info") or {}
        best = data.get("hdplay") if self.config["hd"] else None

        return {
            "video": self._absolute(best or data.get("play") or data.get("wmplay")),
            "images": [self._absolute(url) for url in (data.get("images") or [])],
            "title": data.get("title") or "",
            "author": (data.get("author") or {}).get("nickname") or "",
            "nick": (data.get("author") or {}).get("unique_id") or "",
            "music": " — ".join(
                part for part in (music.get("title"), music.get("author")) if part
            )
            or (data.get("music") if isinstance(data.get("music"), str) else ""),
            "duration": data.get("duration") or 0,
            "width": 0,
            "height": 0,
            "likes": data.get("digg_count") or 0,
            "comments": data.get("comment_count") or 0,
            "shares": data.get("share_count") or 0,
            "views": data.get("play_count") or 0,
        }

    async def _expand(self, session, link: str) -> str:
        """Развернуть короткую ссылку до полной — в ней есть номер ролика."""
        if not SHORT.search(link):
            return link

        async with session.get(
            link,
            headers={"User-Agent": BROWSER},
            allow_redirects=True,
            proxy=self._proxy,
        ) as response:
            return str(response.url)

    # ------------------------------------------------------------------ #
    #  Отправка
    # ------------------------------------------------------------------ #
    async def _send_video(self, session, sent, post: dict) -> None:
        limit = self.config["max_mb"] * 1024 * 1024
        data = await self._pull(session, post["video"], limit)

        if isinstance(data, int):
            await utils.answer(
                sent,
                self.strings["too_big"].format(
                    self._weight(data), self._weight(limit), self._prefix
                ),
            )
            return

        if data is None:
            await utils.answer(sent, self._trouble_card([(post["source"], self.strings["reason_empty"])]))
            return

        width, height, seconds = self._measure(data)
        buffer = io.BytesIO(data)
        buffer.name = "tiktok.mp4"

        attributes = []

        if not self.config["as_file"]:
            attributes.append(
                types.DocumentAttributeVideo(
                    duration=seconds or post.get("duration") or 0,
                    w=width or post.get("width") or 0,
                    h=height or post.get("height") or 0,
                    supports_streaming=True,
                )
            )

        await utils.answer_file(
            sent,
            buffer,
            caption=self._caption(post, len(data)),
            force_document=self.config["as_file"],
            attributes=attributes or None,
        )

    async def _send_photos(self, session, sent, post: dict) -> None:
        limit = self.config["max_mb"] * 1024 * 1024
        album = []

        for number, url in enumerate(post["images"][:10], 1):
            data = await self._pull(session, url, limit)

            if isinstance(data, int) or data is None:
                continue

            picture = io.BytesIO(data)
            picture.name = f"tiktok_{number}.jpg"
            album.append(picture)

        if not album:
            await utils.answer(sent, self._trouble_card([(post["source"], self.strings["reason_empty"])]))
            return

        await self.client.send_file(
            sent.peer_id,
            album,
            caption=self._caption(post, 0),
            reply_to=getattr(sent, "reply_to_msg_id", None),
        )

        try:
            await sent.delete()
        except Exception:
            logger.info("Служебное сообщение стереть не вышло")

    async def _pull(self, session, url: str, limit: int):
        """Скачать в память. int — сколько весит то, что не влезло."""
        headers = {"User-Agent": ANDROID, "Referer": "https://www.tiktok.com/"}

        async with session.get(url, headers=headers, proxy=self._proxy) as response:
            response.raise_for_status()
            promised = int(response.headers.get("Content-Length") or 0)

            if promised > limit:
                return promised

            chunks, taken = [], 0

            async for chunk in response.content.iter_chunked(1 << 16):
                taken += len(chunk)

                # Длину обещают не всегда — сторожим и по факту
                if taken > limit:
                    return taken

                chunks.append(chunk)

        return b"".join(chunks) or None

    # ------------------------------------------------------------------ #
    #  Подпись
    # ------------------------------------------------------------------ #
    def _caption(self, post: dict, size: int) -> str:
        mode = self.config["caption"]

        if mode == "none":
            return ""

        author = utils.escape_html(post.get("author") or "")
        nick = utils.escape_html(post.get("nick") or "")
        head = (
            self.strings["by_nick"].format(author or nick, nick)
            if nick
            else self.strings["by"].format(author or "TikTok")
        )

        if mode == "short":
            return head

        parts = [head]
        title = (post.get("title") or "").strip()

        if title:
            parts.append(self._cut(utils.escape_html(title), 700))

        rows = [
            self.strings["stats"].format(
                self._count(post.get("likes")),
                self._count(post.get("comments")),
                self._count(post.get("shares")),
            )
        ]

        if post.get("views"):
            rows.append(self.strings["views"].format(self._count(post["views"])))

        if post.get("music"):
            rows.append(self.strings["music"].format(
                self._cut(utils.escape_html(post["music"]), 80)
            ))

        if post.get("images") and not post.get("video"):
            rows.append(
                self.strings["meta_photos"].format(
                    self.strings["photos_count"].format(len(post["images"])),
                    self._weight(size) if size else "—",
                    post.get("source", ""),
                )
            )
        else:
            rows.append(
                self.strings["meta"].format(
                    self._clock(post.get("duration")),
                    self._weight(size) if size else "—",
                    post.get("source", ""),
                )
            )

        parts.append("\n".join(rows))

        return self._cut("\n\n".join(parts), 1024)

    def _trouble_card(self, troubles: list) -> str:
        if not troubles:
            troubles = [("tiktok", self.strings["reason_empty"])]

        lines = [
            self.strings["line_last" if index == len(troubles) - 1 else "line_failed"].format(
                name, reason
            )
            for index, (name, reason) in enumerate(troubles)
        ]

        # Про прокси говорим один раз внизу, а не в каждой строке
        blocked = any(mark in reason for _, reason in troubles for mark in ("403", "429"))
        foot = self.strings["foot_blocked" if blocked else "foot_order"]

        return self.strings["failed"].format(
            "\n".join(lines), foot.format(self._prefix)
        )

    # ------------------------------------------------------------------ #
    #  Мелочи
    # ------------------------------------------------------------------ #
    async def _link(self, message):
        """Ссылка из аргументов, а нет — из сообщения, на которое ответили."""
        found = LINK.search(utils.get_args_raw(message) or "")

        if found:
            return found.group(0)

        reply = await message.get_reply_message()

        if reply is not None:
            found = LINK.search(getattr(reply, "raw_text", None) or "")

            if found:
                return found.group(0)

        return None

    @property
    def _proxy(self):
        return (self.config["proxy"] or "").strip() or None

    def _reason_http(self, status: int, peek: str) -> str:
        if peek and status not in {403, 429}:
            return self.strings["reason_http_body"].format(status, peek)

        return self.strings["reason_http"].format(status)

    @staticmethod
    async def _peek(response) -> str:
        """Кусочек ответа — по нему видно, кто именно отказал."""
        try:
            body = await response.text()
        except Exception:
            return ""

        clean = re.sub(r"<[^>]+>", " ", body or "")

        return utils.escape_html(" ".join(clean.split())[:90])

    @staticmethod
    def _first(items):
        for item in items or []:
            if item:
                return item

        return None

    @staticmethod
    def _absolute(url):
        """tikwm иногда отдаёт путь без хоста."""
        if not url:
            return None

        return url if url.startswith("http") else f"https://www.tikwm.com{url}"

    @staticmethod
    def _measure(data: bytes):
        """Ширина, высота и длительность прямо из mp4.

        Без них телетон ставит видео w=1, h=1 — клиент считает кадр
        квадратным и растягивает картинку. Читаем заголовок сами, чтобы
        не зависеть ни от сервиса, ни от hachoir.
        """
        try:
            return _mp4(data)
        except Exception:
            logger.info("Заголовок mp4 не разобрался")
            return 0, 0, 0

    def _count(self, number) -> str:
        number = int(number or 0)

        if number >= 1_000_000:
            return self.strings["u_m"].format(round(number / 1_000_000, 1))

        if number >= 1000:
            return self.strings["u_k"].format(round(number / 1000, 1))

        return str(number)

    @staticmethod
    def _clock(seconds) -> str:
        minutes, seconds = divmod(int(seconds or 0), 60)
        return f"{minutes}:{seconds:02d}"

    @staticmethod
    def _weight(size) -> str:
        step = float(size or 0)

        for unit in ("Б", "КБ", "МБ", "ГБ"):
            if step < 1024 or unit == "ГБ":
                return f"{step:.0f} {unit}" if unit == "Б" else f"{step:.1f} {unit}"

            step /= 1024

    @staticmethod
    def _cut(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text

        return re.sub(r"&[a-z]{0,6};?$", "", text[:limit]) + "…"

    @property
    def _prefix(self) -> str:
        return self.client.dispatcher.prefixes[0]


def _mp4(data: bytes):
    """Достать (ширина, высота, длительность) из заголовка mp4."""
    moov = _inside(data, b"moov")

    if moov is None:
        return 0, 0, 0

    width = height = seconds = 0

    for kind, body in _boxes(moov):
        if kind == b"mvhd":
            seconds = _mvhd(body)
        elif kind == b"trak":
            found = _tkhd(body)

            # У звуковой дорожки размеры нулевые — нам нужна картинка
            if all(found):
                width, height = found

    return width, height, seconds


def _boxes(data: bytes):
    """Пройтись по коробкам mp4 одного уровня."""
    offset = 0

    while offset + 8 <= len(data):
        size = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        head = 8

        if size == 1:  # настоящий размер не влез в 32 бита
            if offset + 16 > len(data):
                return

            size = struct.unpack(">Q", data[offset + 8 : offset + 16])[0]
            head = 16
        elif size == 0:  # коробка тянется до конца файла
            size = len(data) - offset

        if size < head or offset + size > len(data):
            return

        yield kind, data[offset + head : offset + size]
        offset += size


def _inside(data: bytes, wanted: bytes):
    """Тело первой коробки с таким именем."""
    for kind, body in _boxes(data):
        if kind == wanted:
            return body

    return None


def _mvhd(body: bytes) -> int:
    """Длительность: число тактов, делённое на их частоту."""
    if body[0] == 1:
        scale, length = struct.unpack(">IQ", body[20:32])
    else:
        scale, length = struct.unpack(">II", body[12:20])

    return round(length / scale) if scale else 0


def _tkhd(body: bytes):
    """Ширина и высота дорожки — в конце tkhd, числами 16.16."""
    head = _inside(body, b"tkhd")

    if head is None or len(head) < 8:
        return 0, 0

    width, height = struct.unpack(">II", head[-8:])

    return width >> 16, height >> 16

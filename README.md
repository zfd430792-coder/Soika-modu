# Модули Сойки

Каталог модулей для [Сойки](https://github.com/zfd430792-coder/soika) и правила,
по которым они пишутся.

```
.ml counter          поставить отсюда
.dlmod <ссылка>      поставить по ссылке
.loadmod             поставить .py файл (ответом на него)
```

| Модуль | Команды | Что делает |
|---|---|---|
| [`template.py`](template.py) | `.template` | скелет: скопировал, переименовал, пишешь |
| [`counter.py`](counter.py) | `.count`, `.counts`, `.delcount` | образец: база, кнопки, конфиг |
| [`username.py`](username.py) | `.uz`, `.uzadd`, `.uztake`, `.uzdel`, `.uzlist` | ждёт, когда освободится чужой юзернейм, и занимает его каналом |
| [`saver.py`](saver.py) | `.saver`, `.saverbox`, `.savermake`, `.saverclean`, `.saverignore` | складывает одноразовые медиа и удалённые сообщения в личные каналы |
| [`tiktok.py`](tiktok.py) | `.tt`, `.ttinfo`, `.ttaudio`, `.ttcfg`, `.ttproxy` | видео из TikTok без водяного знака, с автором и описанием |
| [`ai.py`](ai.py) | `.ai`, `.aimodels`, `.aikey`, `.airole`, `.aicfg` | вопрос к нейросети через Groq, модель из живого списка |
| [`grab.py`](grab.py) | `.gm`, `.gmall`, `.gmhere`, `.gmbox`, `.gmstop` | забирает медиа из постов с запретом сохранения и обходит каналы целиком |
| [`mute.py`](mute.py) | `.mute`, `.unmute`, `.botmute`, `.mutes`, `.mutebots` | мут в личке со стиранием у обоих и мут в группе, который не обойти ботами |

---

> ⚠️ **Чужой модуль выполняется от имени твоего аккаунта.** Он видит переписку,
> может писать от тебя, читать базу Сойки и отправлять наружу что угодно —
> включая сессию и токен инлайн-бота. Песочницы нет. Ставь только то, чьему
> автору доверяешь, и открывай `.py` перед установкой.

---

## Форма модуля

Модуль — один `.py` файл с классом-наследником `loader.Module`. Ни второго
класса, ни пакета, ни установки в систему.

```python
from .. import loader, utils


@loader.tds
class ПриветМод(loader.Module):
    """Здоровается"""

    strings = {"name": "Привет", "hello": "👋 <b>Привет, {}!</b>"}
    strings_en = {"hello": "👋 <b>Hi, {}!</b>"}

    @loader.command(alias="пр")
    async def hellocmd(self, message):
        """[имя] — поздороваться"""
        name = utils.get_args_raw(message) or "мир"
        await utils.answer(message, self.strings["hello"].format(utils.escape_html(name)))
```

Обязательное: `@loader.tds`, докстрока класса, `strings["name"]`, докстрока у
каждой команды. Остальное — по необходимости.

---

## Правила

### 1. Тексты — в `strings`, не в коде

Строка, которую видит пользователь, лежит в `strings` и берётся оттуда. В коде —
только ключ. Английский вариант в `strings_en`; чего там нет, возьмётся из
`strings`.

```python
await utils.answer(message, "Готово!")              # нет
await utils.answer(message, self.strings["done"])   # да
```

### 2. Пользовательский текст — через `escape_html`

Разметка HTML. Всё, что пришло от человека или из сети, экранируется. Иначе одна
угловая скобка ломает сообщение.

```python
await utils.answer(message, self.strings["found"].format(utils.escape_html(запрос)))
```

### 3. Отвечать — через `utils.answer`

Не `client.send_message`. `answer` правит своё сообщение вместо нового, режет
длинный текст и умеет `grep`. Возвращает сообщение — его можно править дальше.

```python
sent = await utils.answer(message, self.strings["working"])
результат = await долгая_операция()
await utils.answer(sent, self.strings["result"].format(результат))
```

Файл — `utils.answer_file`, картинка с подписью — `utils.answer_with_banner`.

### 4. Настройки — в конфиг с валидатором

Всё, что пользователь захочет поменять, идёт в `ModuleConfig`, а не в константы.
Значение читается там, где используется, иначе правка через `.cfg` не
подействует до перезапуска.

```python
config = loader.ModuleConfig(
    loader.ConfigValue(
        "city", "Москва", "Город по умолчанию",
        validator=loader.validators.String(max_len=64),
    ),
)
```

### 5. Состояние — в базу, не в атрибуты

Атрибуты умирают при перезагрузке модуля. `self.get` / `self.set` /
`self.pointer` — своя секция в базе, переживает перезапуск. Класть только то,
что переживает JSON.

```python
self.set("last_run", time.time())
счётчики = self.pointer("counters", {})   # правки сохраняются сами
```

### 6. Опасное — под `@loader.owner`

Выполнение кода, работа с файлами, деньги, рассылки — только владелец. Декоратор
прав ставится над `@loader.command()`.

```python
@loader.owner
@loader.command()
async def dangercmd(self, message): ...
```

### 7. Служебные методы — с подчёркивания

Метод, чьё имя кончается на `cmd`, автоматически становится командой. Внутреннее
называй `_helper`, чтобы не завести команду случайно.

### 8. Проверять аргументы и объяснять

Нет аргумента — скажи, что нужно и как. Не молчи и не падай.

```python
if not (args := utils.get_args_raw(message)):
    await utils.answer(message, self.strings["usage"])
    return
```

### 9. Ошибки ловить самому

Сеть, файлы и чужие API падают. Пользователю — понятная строка, подробности — в
лог. Голый трейсбек в чате это не ответ.

```python
try:
    данные = await запрос()
except Exception:
    logger.exception("Сервис не ответил")
    await utils.answer(message, self.strings["failed"])
    return
```

### 10. Кнопки — с запасным путём

Инлайн-бот может быть не поднят. Проверяй и умей ответить текстом.

```python
if self.inline is not None and self.inline.init_complete:
    if await self.inline.form(текст, message=message, reply_markup=кнопки):
        return

await utils.answer(message, текст)
```

### 11. За собой убирать

Открыл сессию, запустил задачу, повесил хендлер — закрой в `on_unload`.

### 12. Вотчер — короткий

Вотчер вызывается на каждое сообщение. Тяжёлое — в `utils.spawn`, фильтры — в
декораторе, а не внутри тела.

---

## Справочник

### Шапка файла

| Строка | Зачем |
|---|---|
| `# meta developer: @username` | автор, виден в `.help <модуль>` |
| `# meta banner: <ссылка>` | картинка над справкой и карточкой установки |
| `# meta description: текст` | описание для каталога |
| `# requires: aiohttp pillow` | пакеты, Сойка поставит их сама |

### Команды и обработчики

| Декоратор | Что делает |
|---|---|
| `@loader.command()` | метод `имяcmd` → команда `.имя` |
| `@loader.command(alias="и")` | плюс короткое имя |
| `@loader.command(aliases=["и", "имя2"])` | несколько имён |
| `@loader.watcher(only_pm=True)` | смотрит все сообщения |
| `@loader.loop(interval=60, autostart=True)` | фоновая задача |
| `@loader.raw_handler(UpdateNewMessage)` | сырые апдейты Telegram |
| метод `имя_inline_handler` | инлайн-запрос `@бот имя` |
| метод `имя_callback_handler` | нажатие кнопки с `data` |

### Права

| Декоратор | Кому |
|---|---|
| `@loader.owner` | только владельцу |
| `@loader.sudo` | владельцу и `.sudo` |
| `@loader.group_admin` | админам чата |
| `@loader.group_member` | участникам чата |
| `@loader.pm` | любому в личке |
| `@loader.everyone` | всем |

По умолчанию — владелец и `.sudo`. Владелец переопределяет права любой команды
через `.security`, и его выбор важнее написанного в коде.

### Аргументы

| Вызов | Что вернёт |
|---|---|
| `utils.get_args_raw(message)` | всё после команды строкой |
| `utils.get_args(message)` | список, кавычки уважаются |
| `utils.get_args_split_by(message, ",")` | разбить своим разделителем |
| `utils.get_args_html(message)` | с сохранением разметки |
| `utils.get_kwargs(message)` | `ключ=значение` → словарь |
| `await utils.get_target_user(message)` | юзер из реплая, `@ника` или id |

### Фильтры вотчеров и команд

`out`, `in`, `only_pm`, `no_pm`, `only_chats`, `no_chats`, `only_groups`,
`only_channels`, `only_media`, `no_media`, `no_forwards`, `no_stickers`,
`no_docs`, `no_inline`, `no_commands`, `only_commands`, а также `startswith=`,
`endswith=`, `contains=`, `regex=`, `chat_id=`, `from_id=`.

### Валидаторы конфига

`Boolean`, `Integer(minimum=, maximum=)`, `Float`, `String(max_len=)`, `RegExp`,
`Choice`, `MultiChoice`, `Series`, `Link`, `TelegramID`, `Emoji`, `Hidden`,
`NoneType`, `Union`.

Пустое значение разрешается так:

```python
validator=loader.validators.Union(
    loader.validators.NoneType(),
    loader.validators.Link(),
)
```

### Инлайн

| Вызов | Что делает |
|---|---|
| `self.inline.form(текст, message, reply_markup=)` | сообщение с кнопками |
| `self.inline.gallery(message, [ссылки])` | листаемая галерея |
| `self.inline.list(message, [страницы])` | постраничный текст |
| `call.answer("текст")` | всплывашка на кнопке |
| `call.edit(текст, reply_markup=)` | переписать сообщение |
| `call.delete()` | удалить сообщение |

Кнопка — словарь: `{"text": ..., "callback": метод, "args": (...)}`, либо
`"url"`, либо `"input"`, либо `"data"`.

### Жизненный цикл

| Метод | Когда вызывается |
|---|---|
| `client_ready(self, client, db)` | модуль загружен, клиент на связи |
| `on_dlmod(self)` | один раз сразу после установки |
| `on_unload(self)` | модуль выгружают |
| `config_complete(self)` | настройки применены, в том числе после `.cfg` |

### Что доступно внутри

`self.client`, `self.db`, `self.inline`, `self.allmodules`, `self.tg_id`,
`self.lookup("Имя")`, `self.get/set/pointer`, `self.import_lib(ссылка)`,
`self.invoke("команда", message=...)`.

### Прервать работу

| Исключение | Смысл |
|---|---|
| `loader.LoadError("почему")` | модуль не может загрузиться |
| `loader.SelfUnload("почему")` | выгрузить себя |
| `loader.SelfSuspend("почему")` | приостановиться до перезапуска |
| `loader.StopLoop` | выйти из `@loader.loop` |

---

## Перед публикацией

- [ ] один файл, один класс, работает после `.loadmod`
- [ ] тексты в `strings`, английский в `strings_en`
- [ ] пользовательский ввод через `escape_html`
- [ ] ответы через `utils.answer`
- [ ] настройки в конфиге с валидаторами
- [ ] состояние в базе, а не в атрибутах
- [ ] опасные команды под `@loader.owner`
- [ ] докстроки у класса и у каждой команды
- [ ] ошибки поймал, пользователю объяснил
- [ ] кнопки с запасным текстовым путём
- [ ] `# requires:` для внешних пакетов
- [ ] `on_unload` гасит задачи и закрывает сессии

---

## Как попасть в каталог

Пул-реквест: один `.py` файл в корень репозитория, по чек-листу выше, без
обфускации и без обращений к адресам, о которых не сказано в описании. Модули,
которые собирают данные пользователя или ведут себя не так, как написано, в
каталог не берутся.

Свой каталог держится где угодно: `.cfg` → **Загрузчик** → `repo`, адрес вида
`https://raw.githubusercontent.com/пользователь/репозиторий/main`.

---

## Если не работает

| Симптом | Причина |
|---|---|
| команда не появилась | метод не кончается на `cmd` либо файл не загрузился — `.logs error` |
| `Команда .x уже занята` | имя занято другим модулем, переименуй |
| сообщение без разметки или обрывается | забыт `escape_html` |
| кнопок нет | инлайн-бот не поднялся — `.botinfo`, `.logs error` |
| модуль пропал после перезапуска | `SelfUnload` или падение при загрузке |
| `ModuleNotFoundError` | не указан `# requires:` |
| настройка не применяется | значение прочитано один раз при загрузке |

Проверить кусок кода, не переустанавливая модуль, — `.e`: там доступны `client`,
`db`, `self`, `utils`, `loader`, `modules`, `message`, `reply`.

---

## Совместимость

Модули под Hikka, FTG, GeekTG и Dragon работают без правок: `hikkatl` →
`telethon`, `hikka` → `soika` подменяются на лету.

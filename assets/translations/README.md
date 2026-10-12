# Переводы интерфейса

Исходный язык интерфейса — русский (`ru`). Английский (`en`) — дополнительный
Qt-перевод. Эти каталоги не относятся к EN/RU данным проектов Stellaris.

`stellaris_localizer_en.ts` — исходный каталог для Qt Linguist; сейчас содержит
английский перевод интерфейса: окон, меню, редакторских действий, поиска,
подсказок, статусов, подтверждений и сообщений об ошибках.
После постепенного добавления `self.tr("Русский текст")` в QObject-классы
или `QCoreApplication.translate("Context", "Русский текст")` вне них обновляйте
каталог через `pyside6-lupdate`, переводите его и компилируйте через
`pyside6-lrelease`. Контекст перевода должен оставаться стабильным.

Из корня проекта (PowerShell):

```powershell
$sources = @('gui_main.py') + @(Get-ChildItem -LiteralPath src -Recurse -Filter *.py | ForEach-Object { $_.FullName })
& .\.venv\Scripts\pyside6-lupdate.exe @sources -source-language ru -target-language en -ts assets/translations/stellaris_localizer_en.ts
& .\.venv\Scripts\pyside6-lrelease.exe assets/translations/stellaris_localizer_en.ts -qm assets/translations/stellaris_localizer_en.qm
```

Передавайте Python-файлы явно: сканирование каталога через `-extensions py`
в используемой версии инструмента не извлекает Python-строки.
Для строк уровня модуля используйте `QT_TRANSLATE_NOOP`, а перевод выполняйте
при показе. Строки с параметрами переводятся до `.format(...)`; имена файлов,
ключи и значения параметров не переводятся. Диагностика и причины сопоставления,
сохранённые в данных проекта, остаются на исходном языке; `src.ui_messages`
переводит их только для UI. `.qm` не содержит пользовательских данных.

Runtime загружает только `stellaris_localizer_en.qm`. После изменения `.ts`
нужно заново выполнить `pyside6-lrelease`. Готовый `.qm` автоматически
входит в существующую сборку через `datas=[('assets', 'assets')]`.
Пути определяются через `src.resources.asset_path` и работают в исходниках
и PyInstaller bundle.

Глобальный ключ `ui_language` хранится в существующем QSettings
`StellarisLocalizer/StellarisLocalizer`. API: `AppSettings().ui_language` и
`AppSettings().set_ui_language("en")`. Допустимы `ru` и `en`, по умолчанию `ru`.
Настройка применяется при следующем запуске. В Start Window выбор языка
сохраняется сразу; в Settings → «Интерфейс» — только по кнопке «Сохранить».
«Отмена» сохраняет прежнее значение. Подсказка о перезапуске сравнивает
сохранённый/выбранный язык с фактическим языком текущего запуска, доступным
через `src.i18n.current_ui_language()`. Live switching и автоматического
перезапуска нет.

`src.i18n.initialize_ui_language` вызывается до создания окон. Для `ru`
переводчик не создаётся. Если английский каталог отсутствует, повреждён или
не установлен Qt, остаётся русский исходный текст; сохранённая настройка
при этом не перезаписывается. Непереведённые строки также остаются русскими.
QLocale и язык данных проекта не меняются.

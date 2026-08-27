# Visa Generator

Автозаполнение визовых документов для агентств в Баку: сканы из `input/` → анкета, сопроводительное письмо и чек-лист в `output/`.

Имена транслитерируются по ICAO Doc 9303, даты — `DD/MM/YYYY`. Данные заявителя никуда кроме `output/` не сохраняются.

## Что нужно

- Python 3.10+
- Ключ Gemini: [Google AI Studio](https://aistudio.google.com/apikey)
- Для PDF-сканов в `input/` — [Poppler](https://github.com/osber/poppler-windows/releases) (нужен `pdf2image`)

```bash
pip install -r requirements.txt
```

В корне проекта создайте `.env`:

```
GEMINI_API_KEY=ваш_ключ
```

По желанию: `GEMINI_MODEL=gemini-3.6-flash` (по умолчанию берётся `gemini-3.5-flash-lite` и запасные Flash-модели).

## Папки

| Папка | Что класть |
|---|---|
| `input/` | фото/сканы паспорта, şəxsiyyət vəsiqəsi, справок (JPG, PNG, PDF) |
| `templates/` | бланк Шенгена и схема DS-160 (уже есть) |
| `output/` | готовые файлы |

Не коммитьте `input/` и `output/` — там персональные данные.

## Запуск

1. Очистите `input/` от чужих файлов, положите сканы **одного** заявителя.
2. Из корня проекта:

```bash
python scripts/fill_form.py
```

Италия / Шенген — режим по умолчанию. Для США (выгрузка ответов DS-160):

```bash
python scripts/fill_form.py --target-country usa
```

В Cursor можно написать **process-visa** или **заполни анкету** — агент сам запустит скрипт и проверит `output/`.

Если бланка `templates/schengen_italy.pdf` нет:

```bash
python scripts/create_schengen_template.py
```

## Что получится

Италия:

- `output/filled_[ФАМИЛИЯ]_schengen.pdf`
- `output/cover_letter_[ФАМИЛИЯ].pdf`
- `output/document_checklist_[ФАМИЛИЯ].txt`

США: вместо анкеты — `output/filled_[ФАМИЛИЯ]_ds160.json`, письмо и чек-лист те же.

Пустые поля в бланке значат, что на сканах этого не было. Скрипт ничего не выдумывает: добавьте нужный документ в `input/` и запустите снова.

## Если ошибка

- `input/` пуст — положите JPG/PNG/PDF.
- Нет ключа — задайте `GEMINI_API_KEY` в `.env`.
- 404 по модели — ключ обычно живой; скрипт сам переберёт актуальные бесплатные Flash. При необходимости укажите `GEMINI_MODEL`.

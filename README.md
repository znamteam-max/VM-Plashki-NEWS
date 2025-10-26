
# NBA Card Bot — Starter Kit

Бот по имени игрока и заданной статистике собирает PNG на прозрачном фоне по выбранному шаблону (5 вариантов).

## Установка
```bash
python -m venv venv && source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # вставьте BOT_TOKEN
python app.py
```

## Важно
- Добавьте шрифты с кириллицей в `assets/fonts/`: Montserrat-Bold.ttf, Montserrat-SemiBold.ttf, Exo2-Bold.ttf.
- Иконки лежат в `assets/icons/` (можете заменить своими).
- Для Windows можно заранее положить PNG логотипы команд в `assets/cache/` (logo_{teamId}.png), чтобы не использовать CairoSVG.


# StoryArt workspace skeleton

Минимальный переносимый каркас StoryArt для создания локальных style-pack, ведения библиотеки вспомогательных референсов и проверки риска генерации.

Репозиторий намеренно не содержит пользовательские стили, изображения, исходные коллекции, созданных персонажей, результаты генераций и временные материалы. `.gitignore` использует разрешающий список файлов и дополнительно запрещает распространённые форматы изображений.

## Что входит

- `tools/style_pack_manager.py` — создаёт, наполняет и проверяет `<STYLE>_PROJECT_PACK` и `<STYLE>_GENERATIONS`;
- `tools/body_reference_manager.py` — безопасно добавляет материалы в локальную `BODY_REFERENCE_LIBRARY`;
- `tools/generation_risk_assessor.py` — формирует D1-D10 отчёт для промта и прикрепляемых референсов;
- `config`, `templates`, `docs` — переносимая конфигурация и шаблоны;
- `tests` — базовая проверка менеджеров;
- `AGENTS.example.md` — нейтральная локальная политика без пользовательских стилей.

## Быстрое развёртывание на Windows

Из PowerShell в корне клона выполните:

```powershell
.\scripts\bootstrap.ps1
```

Скрипт создаст локальное виртуальное окружение `.venv`, установит Pillow, скопирует нейтральный `AGENTS.example.md` в игнорируемый `AGENTS.md`, запустит тесты и проверит обнаружение style-pack.

Ручной эквивалент:

```powershell
# Создать изолированное Python-окружение только для этого проекта.
py -3 -m venv .venv

# Установить единственную внешнюю зависимость менеджеров изображений.
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Создать локальную агентскую инструкцию, которая не будет попадать в Git.
Copy-Item AGENTS.example.md AGENTS.md

# Проверить переносимый код до добавления собственных данных.
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Создание нового локального стиля

```powershell
# Создать чистую пару MY_STYLE_PROJECT_PACK + MY_STYLE_GENERATIONS
# и сразу скопировать исходные референсы без изменения оригиналов.
.\.venv\Scripts\python.exe tools\style_pack_manager.py init `
  --style-name "MY_STYLE" `
  --source "D:\Path\To\MyReferences"

# Проверить созданную структуру и манифесты.
.\.venv\Scripts\python.exe tools\style_pack_manager.py validate `
  --style-name "MY_STYLE"
```

Созданные пакеты, изображения и `GENERATION_RESULTS` останутся только на локальной машине и будут проигнорированы Git.

## Контроль перед публикацией

```powershell
# Показать только файлы, которые реально войдут в следующий коммит.
git diff --cached --name-only

# Убедиться, что Git не отслеживает распространённые форматы изображений.
git ls-files | Select-String -Pattern '\.(png|jpe?g|webp|gif|bmp|tiff?|psd)$'
```

Вторая команда не должна выводить ничего.

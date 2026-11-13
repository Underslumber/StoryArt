# Исправления маршрутов и работы со стилями

Поручение: «1. Исправь маршруты. 2. Скорректируй работу со стилями, чтобы они соблюдались».

Реализовано 6 ноября 2026 в рабочем проекте StoryArt. Утверждённые изображения,
профили и реестры не изменялись. Провайдерных генераций, калибровок и новых
стилевых/идентификационных источников не было.

| Роль | Production-маршрут |
|---|---|
| Root | Sol 6.1 Medium |
| Обычный bounded worker, STYLE_LIBRARIAN, IDENTITY_CURATOR, CODE_IMPLEMENTER | Sol 6.1 Low |
| CALL_PLANNER, независимый VISUAL_QA/CODE_REVIEW | Sol 6.1 Medium |
| Исключительная обоснованная диагностика | Sol 6.1 High |
| Явно выбранный ChatGPT Web image agent | Sol 6.1 Low, прежние ограничения Web сохраняются |

Реестр `config/model_routes.json`, шаблон конфигурации, инструкции, skill и
handoff согласованы. Исправлен список ролей переключателя профилей: он принимает
существующий WEB_IMAGE. Реальный PowerShell switcher проверен на копии; реальные
handoff profiles проверены тестом. Локальный `.codex/config.toml` уже совпадает с
этим production root/default — его переписывание не понадобилось. Изменение
реестра не переключает уже работающие чаты. Luna Low — только явный эксперимент,
Astra не используется.

Новые reference plans schema 7 содержат hash-bound `style_rendering_contract`.
В exact prompt до оценки риска попадает отдельная rendering authority с
наблюдениями по выбранным STYLE-источникам; при штатном JSON frontend номера
изображений берутся из реального resolved manifest. Manager и независимый
execution guard проверяют текущие bytes, STYLE роли и физическое присутствие
вложений. Лицо, тело и одежда остаются собственными design authorities и не
заменяют манеру STYLE. К конкретным стилям не применяется чужой универсальный
фильтр «широкие мазки».

В существующем visual-review JSON теперь требуется сравнение с exact STYLE
paths/hashes по шести осям: масштаб следов/мазка/линии, края, светотеневые группы,
цветовая структура, материалы и иерархия деталей. Отдельно оцениваются применимые
лицо, тело, материалы/одежда и фон. BACK не требует невидимого лица.
`STYLE=PASS` в CLI не отменяет comparative FAIL/NOT_CHECKED. Это контроль
полноты и согласованности реальной визуальной аттестации, а не автоматическая
метрика художественного сходства.

Старые immutable планы schema ≤6, GENERATOR_DEFAULT и исключение для прямого
one-step edit сохранены. READY retry не меняет уже оценённый стилевой блок.
Небольшая зависимая правка: пустой `direct_edit_completion_receipt` из CLI
считается отсутствием opt-in и идёт по обычному строгому lifecycle, вместо
ошибочного включения специального завершения Деспара.

Проверки: 222 теста общего целевого прогона; после последних двух узких проверок
контракта — 22 style unit tests и 3 полных пятиэтапных adult/child CLI-сценария.
`py_compile`, `git diff --check`, сверка локального config и замороженных hashes
пройдены. Свежий независимый Sol Medium: PASS. Итоговый Claude Sonnet: PASS,
без блокирующих замечаний. Они проверяли software scope; генератор не вызывали.

Реальное повышение визуальной fidelity новым изображением **не проверено**.
Число 90% остаётся авторской целью, не измеренным score и не гарантией
провайдера. При следующей порученной генерации root обязан выполнить новый
сравнительный QA и продолжить исправление при drift, а не принять кадр по
одному совпадению палитры/лица.

Применение и формат JSON: [STYLE_RENDERING_CONTRACT.md](D:/Projects/StoryArt/docs/STYLE_RENDERING_CONTRACT.md).
Доказательства и список изменённых файлов: [CHECKS.json](D:/Projects/StoryArt/.agent/style-routing-20261106/CHECKS.json),
[actual.diff](D:/Projects/StoryArt/.agent/style-routing-20261106/actual.diff),
[Sol review](D:/Projects/StoryArt/.agent/style-routing-20261106/SOL_REVIEW.txt),
[Claude final](D:/Projects/StoryArt/.agent/style-routing-20261106/CLAUDE_FINAL_RESULT.txt).

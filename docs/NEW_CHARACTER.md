# Новый персонаж: короткий маршрут

Это рабочая шпаргалка для `CHARACTER_BASE`. Полные правила остаются в
`docs/PORTABLE_GENERATION_RULES.md`, `docs/EXECUTION_GUARD.md` и
`skills/storyart-orchestrator/SKILL.md`; при расхождении действуют они.
Читай этот файл первым и открывай полные разделы только для текущего шага.

Пять этапов, их отдельные генерации и независимый QA не меняются:
`01_FACE_IDENTITY`, `02_PHYSIQUE_FRONT`, `03_PHYSIQUE_SIDE`,
`04_PHYSIQUE_BACK`, `05_CHARACTER_ASSEMBLY`. Каждый этап — отдельный
полноразмерный файл. Лист обзора служит только для показа и никогда не
становится референсом.

## Решения пользователя

Пользователь решает три раза:

1. **Паспорт персонажа** до первой генерации.
2. **Лицо** (`01_FACE_IDENTITY`). Лицо генерируется по одному варианту за раз:
   это главное в персонаже, поэтому каждый вариант показывается отдельно и
   правится до точного попадания.
3. **FRONT** (`02_PHYSIQUE_FRONT`). Он задаёт пропорции и стиль тела для
   остальных видов.

После FRONT агент без остановок делает SIDE, BACK и сборку 3/4, каждый своей
генерацией с полным QA. Затем показывает лист обзора вместе с пятью
отдельными файлами, и пользователь утверждает персонажа или называет, что
переделать.

## Порядок

Все команды выполняет агент. `<REQ>` — папка активного запроса
`<STYLE>_GENERATIONS\00_PENDING\<request-id>`.

1. **Guard и план.** Как обычно: `task_execution_guard.py start`, меню
   `Стиль и референсы` с `--generation-purpose CHARACTER_BASE`, затем
   `generation_request.py` для плана.
2. **Паспорт.** Собери из описания и канонических картинок пользователя:

   ```powershell
   python tools\character_base.py brief-template --output "<REQ>\brief-draft.json"
   ```

   Заполни поля: возраст на вид, вид, подача пола, рост в головах,
   телосложение, кожа или мех, волосы, глаза, форма лица, особые приметы;
   при необходимости уши, палитра. `source`: `USER`, `CANON_IMAGE` или
   `AGENT_PROPOSAL`. Предположения агента помечай `AGENT_PROPOSAL` и
   показывай пользователю отдельной строкой. В `must_not` перечисли то, чего
   быть не должно (например, светлая морда, маленькие уши). Покажи паспорт
   пользователю таблицей и после его подтверждения сохрани:

   ```powershell
   python tools\character_base.py brief --request-dir "<REQ>" --brief-file "<REQ>\brief-draft.json" --confirmation-quote "<точные слова пользователя>"
   ```

3. **Цикл этапа.** Спроси, что дальше:

   ```powershell
   python tools\character_base.py next --request-dir "<REQ>"
   ```

   Ответ даёт `STAGE`, `REQUIRED_QA`, `SWIMWEAR_RUNG` для взрослых видов тела,
   блок `PASSPORT_PROMPT` и строки `AVOID`. Блок паспорта и все строки `AVOID`
   включай в точный промпт этапа без перефразирования. Затем:
   `generation_request.py` с `--call-file`, `EXECUTION_STARTED`, генерация.
4. **Ревью результата.** Шаблон визуального ревью уже содержит все фразы
   промпта:

   ```powershell
   python tools\character_base.py review-template --request-dir "<REQ>" --attempt-id <ID> --image "<output>" --author "<reviewer>" --output "<REQ>\review-<ID>.json"
   ```

   Замени каждый `TODO` на `PASS` или `FAIL` с наблюдением. Для отклонённого
   кадра достаточно `prompt_adherence.status=FAIL`,
   `checked_scope=REJECTION_FINDINGS` и только проваленных фраз, дословно и в
   порядке промпта; принятый кадр проверяется по всем фразам.
   Для видов тела измерь кадр: `character_base.py fit-canvas --image "<output>"`
   выдаёт `SUBJECT_HEIGHT_PERCENT`. Если фигура выше 88%, команда делает
   отдельную копию с расширенным холстом и файлом происхождения; оригинал не
   меняется. Копию можно регистрировать только тем путём, который допускает
   guard для этой попытки; иначе это `CANVAS=FAIL` и новая попытка с
   поправкой кадрирования.
5. **Запись.** Один вызов делает `record-generation` и нужную отметку guard:

   ```powershell
   python tools\character_base.py record --style-name "<STYLE>" --request-id <request-id> --request-dir "<REQ>" --stage <STAGE> --image "<output>" --attempt-id <ID> --visual-review-json "<REQ>\review-<ID>.json" --qa-json "<REQ>\qa-<ID>.json" --description "<что на кадре>"
   ```

   `qa-<ID>.json`: `{"results": {"<QA>": "PASS|FAIL", ...}, "limb_qa_evidence": "...",
   "observed_topology": "TWO_PIECE", "findings": ["..."], "avoid": ["..."]}`.
   При любом `FAIL` команда регистрирует `REJECTED`, пишет `ATTEMPT_REJECTED` и
   добавляет находки в журнал сбоев запроса; их строки `avoid` попадут в
   следующие промпты через `next`. При успехе пишется `STAGE_COMPLETED`.
6. **Шлюз.** После лица и после FRONT `next` отвечает `WAIT_USER_APPROVAL`.
   Покажи полноразмерный файл, дождись ответа и запиши его:

   ```powershell
   python tools\character_base.py gate --request-dir "<REQ>" --stage 01_FACE_IDENTITY --user-quote "<слова пользователя>"
   ```

   Одобрение привязано к хешу файла: переделанное лицо требует нового ответа.
7. **Финал.** Когда `next` отвечает `SHOW_SHEET_AND_APPROVE`:

   ```powershell
   python tools\character_base.py sheet --request-dir "<REQ>"
   ```

   Отправь лист обзора и пять отдельных файлов. После одобрения выполни
   `style_pack_manager.py approve-character`. Паспорт станет
   `locked_invariants` профиля, а журнал сбоев — `known_failure_modes`.

## Правки по ходу

Замечание пользователя продолжает тот же запрос: `USER_CORRECTION` и
`STAGE_REOPENED` для затронутого этапа. Новую папку запроса v2, v3… не
создавай. Если правка меняет внешность, сначала обнови паспорт той же
командой `brief` (ревизия растёт, прошлая сохраняется в
`CHARACTER_BRIEF_HISTORY`), затем переделай этап. Конкретную находку
запиши `character_base.py log-failure --stage <STAGE> --finding "..." --avoid "..."`,
чтобы она не повторилась в следующих видах.

## Тело и одежда

Взрослый персонаж: обычный непрозрачный раздельный купальник `TWO_PIECE`
для FRONT, SIDE и BACK; лестницы вариантов нет. После отказа провайдера одна
переформулировка, затем вопрос пользователю. Ребёнок: только закрытая
возрастная одежда из контракта плана.

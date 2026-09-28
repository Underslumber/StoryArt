# Предохранитель выполнения StoryArt

## Граница текущего чата

Для изображений разрешены утверждённые папки проекта и одна активная папка
текущего запроса. Нельзя перечислять, искать или открывать другие неутверждённые
папки, даже для восстановления похожей сцены или проверки старого статуса.
`CODEX_THREAD_ID` связывает новый guard с чатом; внешний индекс `.agent/chat_requests`
проверяется до чтения guard/плана. Чужой или старый непривязанный запрос не
переносится: создаётся новый уникальный запрос с нулевой историей попыток.
При COMPLETE привязка текущего арта сохраняется: исправление пользователя в том
же чате продолжает этот запрос через USER_CORRECTION, сохраняя его выборы.
Новый запрос заменяет привязку завершённого запроса; после замены прежняя папка
снова недоступна. Одновременно доступна только одна папка, чужие запросы и
старые непривязанные папки по-прежнему запрещены.
Выборы из текущего диалога сохраняются; старые планы из других папок не читаются.
Профиль задаёт только название стиля. Процент и BODY_REFERENCE_LIBRARY требуют
меню «Стиль и референсы», если пользователь ещё не выбрал их в этом чате.

## Авторизация пользователя при генерации

Явная просьба пользователя авторизует запрошенную работу, но не невыбранные параметры. Сценарий StoryArt и доступные проектные инструменты обязательны по умолчанию. Перед вызовом генератора проверь историю текущего чата; уже выбранные параметры не спрашивай повторно. Для отсутствующих выборов используй существующий структурированный native context-menu chooser, а в Default mode — `request_user_input_async` со всеми независимыми пропущенными полями в одной форме. Не заменяй доступный chooser вопросом обычным текстом, не разделяй один выбор на последовательные вопросы, сохраняй полное соответствие каждого варианта параметрам и повторно используй chat-scoped выборы. Пользователь может явно выбрать native/default стиль и отсутствие optional references; молчание выбором не считается. Для названного project-персонажа approved identity references остаются обязательными. Разреши approved registry/profile и привяжи approved assembly и подходящие identity references к точному вызову. Если персонаж или нужные источники не найдены/не прикреплены, сообщи конкретный блокер и не подменяй персонажа. Выйти из сценария можно только по прямой просьбе пользователя, после конкретного предупреждения и подтверждения после него. Внешние ограничения платформы не обходятся.

## Одношаговая правка вложенного изображения

Для прямой одношаговой правки существующего изображения пользователя, включая маску, приложенную в чат, используй вложения напрямую в инструменте редактирования. Не требуй локального сохранения, guard, REFERENCE_PLAN, опросника стиля, риск-отчёта или QA-квитанции. Этот обход требований для прямой правки сохраняется. Для генерации и проектных операций обязательны сценарий, явно выбранные параметры и предусмотренные проверки. Выйти из сценария можно только по прямой просьбе пользователя после конкретного предупреждения и подтверждения после него; внешние ограничения платформы не обходятся. Архив и записи создавай, только если это полезно или прямо запрошено; они не должны задерживать или блокировать работу.

`tools/task_execution_guard.py` не оценивает художественное качество. Он защищает основную задачу пользователя от четырёх сбоев процесса:

- долгая скрытая подготовка без запуска основной работы;
- расход действий и токенов на всё новые исследования;
- превращение запроса на изображение в разработку менеджера, тестов или правил;
- статус `active`, за которым нет реального результата.

## Контракт задачи

До первого содержательного действия агент фиксирует:

- исходную цель пользователя (`goal_lock`);
- главный видимый результат (`primary_deliverable`);
- допустимую область файлов (`allowed_scope`);
- неизменяемые параметры ТЗ (`locked_invariants`): ракурс, ориентацию, место действия, число персонажей и главный композиционный приём;
- тип задачи;
- лимиты подготовки и ожидания выполнения.

Для генерации состояние хранится здесь:

```text
<STYLE>_GENERATIONS/00_PENDING/<request-id>/EXECUTION_GUARD.json
```

Для другой длительной работы проекта используется локальный путь `.task_guard/<request-id>/EXECUTION_GUARD.json`.

## Контрольные пороги watchdog по умолчанию

- не более 20 активных минут до запуска основной операции;
- не более 12 содержательных этапов подготовки;
- не более 20 минут непрозрачного ожидания уже запущенной операции;
- ожидание обязательного ответа пользователя не входит в активное время.

Это пороги обнаружения зависшей подготовки или непрозрачного ожидания, а не срок жизни задачи. При срабатывании guard сохраняет статус `ACTIVE`, запрещает только несвязанное дальнейшее исследование и требует перейти к исполняемому этапу. Завершить подготовку плана через `prepare-generation`, разрешить текущие слоты, рассчитать риск точного вызова, выполнить `prepare-call`, начать готовую операцию, сверить её статус и записать видимый результат остаётся разрешено. Само истечение времени или числа checkpoint никогда не является `BLOCKER` и не разрешает завершить задачу.

Пороги не дают агенту право тратить 20 минут на простое действие, которое уже готово к выполнению.

## Последовательность для изображения

### 1. Зафиксировать задачу

В активном workspace StoryArt любая генерация обязательно проходит через сценарий и доступные проектные инструменты. До вызова генератора проверь чат и используй сохранённые выборы. Если стиль, политика референсов или персонаж ещё не выбраны, покажи компактную нумерованную копию полных вариантов, затем немедленно вызови structured chooser; в Default mode используй `request_user_input_async` со всеми независимыми пропущенными полями и теми же вариантами. Карточка — основной способ выбора, список — резервная копия. Не говори, что меню появилось, если вызов chooser не состоялся успешно. Если UI-вызов действительно недоступен или завершился ошибкой, назови точную причину и оставь список для цифрового ответа. Выбор в карточке или однозначный цифровой ответ завершают выбор без повторного подтверждения. Не повторяй выбор, покрытый выбранным профилем. Пользователь может явно выбрать стиль генератора по умолчанию и отсутствие optional references; отсутствие выбора не считается согласием. Для `CHAR_NNN` найди approved registry/profile, затем привяжи его `CHARACTER_ASSEMBLY` и применимые face/body references к точному физическому вызову; identity references обязательны независимо от optional reference choice. Если персонажа или источники нельзя разрешить/прикрепить, остановись и назови причину, не заменяй его generic-персонажем. Выйти из сценария можно только по прямой просьбе пользователя, с конкретным предупреждением и подтверждением после предупреждения. Для standalone-изображения вне StoryArt этот guard не используется; отдельный тип `USER_REQUESTED_IMAGE` запрещён. Правка вложенного изображения остаётся отдельным одношаговым маршрутом. Если проектный инструмент недоступен, зафиксируй конкретную ошибку и не заявляй об успешном запуске сценария.

Никогда не выводи желание обхода правила или сценария из краткости/формулировки запроса. Пользователь должен прямо попросить выйти из сценария. Перед выходом предупреди о конкретных вероятных последствиях и всегда дождись подтверждения после предупреждения. Внешние ограничения платформы обходить нельзя.

Не ищи и не перечисляй исторические неподтверждённые генерации, чтобы выбрать базу, референс или вариант продолжения. Это включает `00_PENDING`, копии в `GENERATION_RESULTS` со статусом без одобрения, а также статусы `TEST`, `STAGING`, `REJECTED`, `DRAFT` и аналогичные. Наличие файла в архиве или другом чате не является одобрением. Для QA активного запроса разрешены только его собственные выходы внутри области, зафиксированной guard. Ранее созданный арт можно повторно использовать только если пользователь выбрал именно его в текущем чате либо authoritative manager нашёл его среди одобренных объектов подходящей роли.

`IMAGE_GENERATION` использует точный `REFERENCE_PLAN`. В `execution_call.user_selections` перед readiness должны быть `style`, `reference_policy`, `character`; каждая запись содержит выбранное пользователем `choice` и точную `user_quote` из чата. Спрашивай только отсутствующие решения. `PROJECT_STYLE_ONLY` разрешает выбранный project STYLE без optional user/body-library references; для `CHAR_NNN` обязательные approved identity assets сохраняются. Для `CHAR_NNN` `character.choice` обязан совпасть с `REFERENCE_PLAN.character_id`, утверждённый `selected_references.character_assembly` — иметь тот же `source_character_id`, а его path/hash с ролью `CHARACTER_ASSEMBLY` — присутствовать в физических slots точного вызова. Точный execution-call hash также фиксируется в ready-binding.

`IMAGE_GENERATION_NATIVE_DEFAULT` допустим только когда пользователь выбрал `GENERATOR_DEFAULT`, `NO_REFERENCES` и `NONE` для project-персонажа. Он остаётся внутри guard-сценария, фиксирует точный промпт, выборы и риск-отчёт, требует `READY_FOR_EXECUTION`, `REQUESTED_DELIVERABLE`, реальный файл результата и стандартные delivery/COMPLETE проверки. Не используй его для имени существующего персонажа или применимого утверждённого профиля.

Команда создаёт неизменяемый контракт цели и бюджета. Её запускают до поиска стиля и выбора референсов:

```powershell
python tools\task_execution_guard.py start `
  --state "<STYLE>_GENERATIONS\00_PENDING\<request-id>\EXECUTION_GUARD.json" `
  --request-id "<request-id>" `
  --task-kind IMAGE_GENERATION `
  --goal "<точная цель пользователя>" `
  --deliverable "<первый видимый результат>" `
  --invariant "camera_view=REAR" `
  --invariant "orientation=LANDSCAPE" `
  --invariant "setting=BALCONY" `
  --required-stage "FACE_IDENTITY" `
  --required-stage "PHYSIQUE_FRONT" `
  --required-stage "PHYSIQUE_SIDE" `
  --required-stage "PHYSIQUE_BACK" `
  --required-stage "CHARACTER_ASSEMBLY" `
  --allowed-scope "<STYLE>_GENERATIONS\00_PENDING\<request-id>" `
  --allowed-scope "GENERATION_RESULTS"
```

### 2. Фиксировать подготовку в IMAGE_GENERATION

Для IMAGE_GENERATION команда учитывает логический блок подготовки и проверяет остаток бюджета. Отдельный тип USER_REQUESTED_IMAGE запрещён; standalone-запросы вне StoryArt не создают проектный guard:

```powershell
python tools\task_execution_guard.py checkpoint `
  --state "<path>\EXECUTION_GUARD.json" `
  --event PREFLIGHT `
  --summary "Проверен локальный набор стиля и выбраны кандидаты."
```

Один checkpoint соответствует логическому результату, а не каждой прочитанной строке. Искусственно дробить этапы или выполнять исследования без checkpoint запрещено.

### 3. Обязательный выбор пользователя для IMAGE_GENERATION

До `READY_FOR_EXECUTION` пользователь должен явно выбрать стиль, политику референсов и персонажа. Если выбор уже сделан в текущем чате, зафиксируй его дословную цитату и используй; если нет — фактически вызови structured chooser и дождись ответа. В Default mode используй `request_user_input_async` со всеми независимыми недостающими полями вместе. Если UI-инструмент выбора недоступен, покажи варианты прямо в ответе; не объявляй невызванное меню открытым. Не задавай повторно поле, уже разрешённое выбранным вариантом. Пользователь может выбрать стиль генератора по умолчанию и отсутствие optional references, но молчание не является выбором. Имена персонажей проекта должны разрешаться в утверждённый ID и его identity assembly; обязательные identity refs не отменяются выбором no optional refs. Значение `user_quote` — provenance, а не автоматическое доказательство: передай точную цитату из текущего чата и сверь её с видимым диалогом.

Первая команда фиксирует ожидание конкретного ответа; вторая продолжает отсчёт после фактического ответа. Это не объявление терминального `BLOCKER`:

```powershell
python tools\task_execution_guard.py checkpoint --state "<path>" --event WAITING_FOR_USER --summary "Нужно уточнить действительно недостающее обязательное поле."
python tools\task_execution_guard.py checkpoint --state "<path>" --event USER_RESUMED --summary "Пользователь уточнил обязательное поле результата."
```

### 4. Подготовить задачу и вызов в IMAGE_GENERATION

Этот шаг применяется, когда к запросу относится существующий style-pack/персонажный план. `style_pack_manager.py prepare-generation` записывает `PREPARED_AWAITING_EXECUTABLE_CALL`; затем точная подготовка конкретного вызова переводит план в готовность. До этого проверь текущий чат: если стиль, референс-политика или личность персонажа ещё не выбраны пользователем, открой structured chooser с независимыми недостающими полями вместе и зафиксируй guard как `WAITING_FOR_USER`. Пользователь может явно выбрать `GENERATOR_DEFAULT`; нельзя выводить его из умолчания. Для названного персонажа этот выбор выполняется через reference-bound маршрут с обязательными утверждёнными identity refs. Не спрашивай повторно уже известное из текущего чата.

`IMAGE_GENERATION_NATIVE_DEFAULT` is only for a character-free request after the user explicitly selected default style and no references. A named character with explicitly selected generator-default style uses the reference-bound route, with no project STYLE slots and with the character's approved assembly and applicable identity refs attached. Create `execution_call.json` with `request_id`, `prompt: {text, text_sha256}`, `user_selections` containing exact style/reference/character choices and user quotes, and the complete `risk_assessment` JSON. For `IMAGE_GENERATION_NATIVE_DEFAULT`, its `input_binding.prompt_sha256` must match the prompt and `input_binding.references` must be empty. Pass the same file to both readiness and execution:

```powershell
python tools\task_execution_guard.py checkpoint --state "<path>\EXECUTION_GUARD.json" --event READY_FOR_EXECUTION --summary "Bind the exact native/default prompt and empty-reference risk assessment." --execution-call "<path>\execution_call.json"
python tools\task_execution_guard.py checkpoint --state "<path>\EXECUTION_GUARD.json" --event EXECUTION_STARTED --summary "Generate the single requested native/default image." --output-contract REQUESTED_DELIVERABLE --execution-call "<path>\execution_call.json"
```

Для reference-bound плана используй один запуск подготовки:

```powershell
python tools/generation_request.py --request-file <request.json> --call-file <call.json>
```

Схема JSON описана в `docs/EFFICIENT_WORKFLOW.md`, раздел One preparation request.
Команда сама выполняет prepare-generation, resolve-call, оценку точного промпта
и вложений, prepare-call и READY_FOR_EXECUTION. Не повторяй эти команды отдельно
и не добавляй validate-only. Для исправления только вызова используй
`--finalize-only`; для уже READY многоэтапного плана укажи stage_id.
Самостоятельные команды менеджера остаются для адресного восстановления ошибки.

Не начинай подготовку следующей обязательной стадии до QA и регистрации текущей. У нового `CHARACTER_BASE` сохраняются все пять этапов: `FACE_IDENTITY`, `PHYSIQUE_FRONT`, `PHYSIQUE_SIDE`, `PHYSIQUE_BACK`, `CHARACTER_ASSEMBLY`.

### 5. Непосредственно перед генератором

После READY_FOR_EXECUTION сразу выполняй EXECUTION_STARTED с тем же планом
и стадией. Этот переход проверяет актуальные байты, промпт и отсутствие активной
попытки. Отдельный CALL_VALIDATED в обычном маршруте не нужен.

Не создавай вспомогательное изображение или варианты для запроса одной сцены. Манекен, маска, таблица пропорций, тест топологии и другой незапрошенный арт не разрешены из-за QA или риска модерации. Для внутренней проверки используй измерения и оверлеи. Перед генерацией сверяй все закреплённые инварианты.

```powershell
python tools\task_execution_guard.py checkpoint `
  --state "<path>\EXECUTION_GUARD.json" `
  --event EXECUTION_STARTED `
  --reference-plan "<request-dir>\REFERENCE_PLAN.json" `
  --stage "SCENE" `
  --output-contract REQUESTED_DELIVERABLE `
  --summary "Запускается одноэтапная SCENE с проверенными вложениями."
```

`--visual-review-json` относится к записи результата `record-generation`, а не к событию `EXECUTION_STARTED`.

EXECUTION_STARTED возвращает attempt_id. Сразу после checkpoint вызови встроенный image_gen с подготовленными prompt и referenced_image_paths. Отдельного исполнителя StoryArt не требуется; не ищи и не проверяй его доступность. Если реальный receipt операции уже получен, сохрани его через --provider-operation-receipt; не выдумывай receipt. Guard сам не отправляет вызов провайдеру: этот вызов выполняет root через image_gen.

### 6. Зафиксировать результат

Сохрани результат через `style_pack_manager.py record-generation` в исходном формате и качестве. Команда требует `--attempt-id`, `--reference-plan` и совпадающий `--character-id`; для многоэтапного плана укажи `--stage-id`. Она проверяет привязку попытки к запросу, персонажу, плану и стадии, архивирует оригинал, применяет нужные QA-слои, регистрирует результат и записывает `VISIBLE_RESULT` с правильным статусом. Используй `STAGING` для промежуточной стадии, `TEST` для готового прошедшего QA результата и `REJECTED` для результата, который не прошёл QA. Статус не означает пользовательскую доставку.

Every project-plan registration requires a reviewer-authored `--visual-review-json`, validated against the immutable attempt snapshot before archive or copy side effects. Inspect the full-resolution result against the exact executed prompt and applicable approved identity/style references; independently assess anatomy/body mechanics, whole-image visible defects, and every explicit prompt constraint. The prompt checklist must contain one evidence-backed verdict for each sentence/clause segment of the exact prompt, in order; a generic whole-prompt item cannot stand in for the checklist. `NOT_APPLICABLE` anatomy requires an explicit no-visible-anatomy finding and substantive reason, and is forbidden for full-body, physique, and assembly stages. A failed layer is recorded as `REJECTED` with the report preserved for correction through the authorized scenario.

```powershell
python tools\style_pack_manager.py record-generation `
  --workspace "<workspace>" `
  --style-name "<style>" `
  --request-id "<request-id>" `
  --image "<request-dir>\FRAME.png" `
  --description "<what this stage shows>" `
  --character-id "<planned id, or NONE>" `
  --attempt-id "<UUID returned by EXECUTION_STARTED>" `
  --reference-plan "<request-dir>\REFERENCE_PLAN.json" `
  --visual-review-json "<request-dir>\VISUAL_REVIEW.json" `
  --status TEST `
  --qa-attachments PASS `
  --qa-canvas PASS `
  --qa-stage-layer PASS `
  --qa-style PASS `
  --qa-subject-accuracy PASS `
  --qa-no-unrequested-characters PASS `
  --qa-focal-hierarchy PASS `
  --qa-lighting PASS `
  --qa-background PASS `
  --qa-composition PASS `
  --qa-artifact-integrity PASS
```

Передавай только те `--qa-*` проверки, которые перечислены как обязательные для текущей стадии в плане; они должны отражать фактический независимый просмотр.

Запиши `RESULT_DELIVERED` отдельно, только для доступного `TEST`-результата и с подтверждением пользовательской доставки. `STAGING` и `REJECTED` нельзя отметить как финальную доставку. QA и закрытие обязательных этапов выполняются отдельно.

```powershell
python tools\task_execution_guard.py checkpoint `
  --state "<path>\EXECUTION_GUARD.json" `
  --event RESULT_DELIVERED `
  --attempt-id "<UUID>" `
  --delivery-evidence "<ссылка или UI receipt доставки>" `
  --summary "TEST-результат передан пользователю."
```

Для обязательных стадий `PHYSIQUE_*` `EXECUTION_STARTED` дополнительно требует `--stage` и `--swimwear-rung`. Без прямого пользовательского override первой разрешена только `EXTREME_MICRO`. QA-отклонение завершённого изображения не открывает следующую ступень автоматически: оно может обосновать ограниченное исправление конкретного QA-дефекта покрытия, топологии одежды или пропорций в рамках активной ступени. Не переформулируй вызов по кругу после отказа провайдера; запиши его через `ATTEMPT_REFUSED`. Неизвестный исход сначала сверяется через `ATTEMPT_UNKNOWN` / `ATTEMPT_RECONCILED`. Следующая ступень открывается только после документированного исчерпания утверждённых QA-исправлений текущей ступени и записи `--rung-routes-exhausted`; так открываются `BIKINI`, затем `TWO_PIECE`. `STAGE_COMPLETED` требует совпадения `--swimwear-rung` и `--observed-topology`; `SPORT_TOP`, `ONE_PIECE`, `SHORTS` и `OTHER` не могут завершить physique-этап. Отступление от проектного дефолта требует одновременно `--user-swimwear-override` и точную цитату пользователя в `--user-override-evidence`.

После прохождения обязательных проверок закройте конкретный этап:

```powershell
python tools\task_execution_guard.py checkpoint `
  --state "<path>\EXECUTION_GUARD.json" `
  --event STAGE_COMPLETED `
  --stage "PHYSIQUE_SIDE" `
  --evidence "<path>\PHYSIQUE_SIDE.png" `
  --summary "Боковой вид записан и прошёл обязательные проверки."
```

`VISIBLE_RESULT` означает только появление очередного видимого результата. Он не означает завершение всей многоэтапной задачи.
`STAGE_COMPLETED` требует прохождения всех обязательных QA-слоёв конкретной стадии и доказательств. `RESULT_DELIVERED` отдельно фиксирует фактическую передачу `TEST`-результата пользователю. `COMPLETE` требует закрытых обязательных стадий и успешной доставки итогового `TEST`. Один только архив, локальный файл, похвала или сообщение «готово» не удовлетворяют этим переходам.

## Защита от остановки после промежуточного результата

Корректирующее сообщение пользователя по умолчанию изменяет активную задачу, а не отменяет её. Зафиксируйте коррекцию:

```powershell
python tools\task_execution_guard.py checkpoint `
  --state "<path>\EXECUTION_GUARD.json" `
  --event USER_CORRECTION `
  --correction-impact PRESERVE `
  --summary "Сохранить выбранный SIDE и продолжить BACK и ASSEMBLY."
```

`PRESERVE` означает, что меняются только явно названные дефекты, а все закреплённые параметры ТЗ остаются прежними. Если фразу можно разумно прочитать и как сохранение, и как отмену ракурса/композиции, используйте `--correction-impact AMBIGUOUS`: guard остановит изменение и потребует уточнить смысл у пользователя.

Изменить закреплённый параметр можно только по прямой цитате пользователя:

```powershell
python tools\task_execution_guard.py checkpoint `
  --state "<path>\EXECUTION_GUARD.json" `
  --event USER_CORRECTION `
  --correction-impact CHANGE `
  --invariant-change "camera_view=FRONT" `
  --user-approved-invariant-change `
  --invariant-change-evidence "Сделай следующий кадр спереди." `
  --summary "Пользователь прямо изменил ракурс."
```

Перед реальным вызовом генератора необходимо перечислить все текущие значения. Отсутствующее или противоположное значение блокирует `EXECUTION_STARTED`:

```powershell
python tools\task_execution_guard.py checkpoint `
  --state "<path>\EXECUTION_GUARD.json" `
  --event EXECUTION_STARTED `
  --reference-plan "<request-dir>\REFERENCE_PLAN.json" `
  --invariant-assert "camera_view=REAR" `
  --invariant-assert "orientation=LANDSCAPE" `
  --invariant-assert "setting=BALCONY" `
  --output-contract REQUESTED_DELIVERABLE `
  --summary "Запускается кадр с закреплённым задним ракурсом."
```

Если исправление сделало уже закрытый этап непригодным, откройте только его:

```powershell
python tools\task_execution_guard.py checkpoint `
  --state "<path>\EXECUTION_GUARD.json" `
  --event STAGE_REOPENED `
  --stage "PHYSIQUE_SIDE" `
  --summary "Пользователь запросил исправление бокового вида."
```

Перед финальным ответом, завершающим задачу, обязательно выполните `COMPLETE`. Команда вернёт `ACTION_REQUIRED` и перечислит незакрытые этапы, если агент пытается остановиться после FACE, FRONT, SIDE или другого промежуточного результата:

```powershell
python tools\task_execution_guard.py checkpoint `
  --state "<path>\EXECUTION_GUARD.json" `
  --event COMPLETE `
  --summary "Все обязательные результаты готовы, записаны и проверены."
```

После обычного замечания нельзя завершать ход фразой «остановился на этом варианте», если исходная цель содержит незакрытые этапы. Разрешены только продолжение следующего этапа либо `BLOCKER` с конкретной непреодолимой причиной.

Отклонение на QA не то же самое, что отказ провайдера или неизвестный результат. QA-отклонение связывается с конкретной попыткой и оставляет обязательный этап открытым:

```powershell
python tools\task_execution_guard.py checkpoint `
  --state "<path>\EXECUTION_GUARD.json" `
  --event ATTEMPT_REJECTED `
  --attempt-id "<UUID>" `
  --stage "PHYSIQUE_FRONT" `
  --qa-layer "COVERAGE" `
  --outcome QA_REJECTED `
  --summary "Точный вызов отклонён; продолжается утверждённый безопасный маршрут."
```

Отказ провайдера записывается как `ATTEMPT_REFUSED` с `--attempt-id` и подтверждением отказа через `--reconciliation-evidence`. При тайм-ауте или неизвестном исходе сначала запиши `ATTEMPT_UNKNOWN`, затем разрешай повтор только после `ATTEMPT_RECONCILED` с outcome `AVAILABLE`, `REJECTED`, `REFUSED`, `CANCELLED` либо `UNKNOWN` и долговременным свидетельством сверки. `STOP` фиксирует локальную остановку и неизвестный удалённый исход; он не отменяет операцию у провайдера. `CANCEL` требует квитанцию фактической отмены. Поздний результат сохраняется и сверяется, но не возобновляет задачу автоматически. `USER_RESUMED` используется только после ответа на `WAITING_FOR_USER`.

Для взрослого `CHARACTER_BASE` используется фиксированная последовательность покрытия: проверенный экстремальный микро-вариант, обычное бикини, обычный раздельный купальник. Если блокируется даже последний вариант, сначала ищите дефект точного вызова: неоднозначный возраст, формулировки промпта, содержимое исходников, неверные роли или суммарный риск вложений. Это не основание менять концепцию одежды или завершать задачу. Несовершеннолетние персонажи используют только полностью возрастно-безопасную нейтральную одежду и не проходят взрослый маршрут минимального покрытия.

Для нового взрослого `CHARACTER_BASE`, если пользователь не указал другое покрытие, каждая проекция всегда начинается с экстремального микробикини. Только после записанной неудачи разрешён переход к обычному бикини, затем в крайнем случае к обычному раздельному купальнику. Несоответствие активной ступени — спортивный топ, кроп-топ, высокая талия, шорты, широкий закрытый низ, цельная или иная более закрытая конструкция — является `REJECTED`, а не пригодной основой для последующей правки одежды. Купальник по умолчанию описывается промптом; отдельный одежный референс подключается только по прямому указанию пользователя. `BODY_REFERENCE_LIBRARY` остаётся доступной для пропорций: сначала используется подходящий безопасный телесный кандидат, при блокировке пробуется другой, и только после полного визуального просмотра релевантного пула и отсутствия подходящего реального фото телосложение может полностью переноситься в `PROMPT_BODY_SPEC`. Каждый точный набор заново оценивается по D.

При незакрытых стадиях `BLOCKER` разрешён только для действительно непреодолимой причины после исчерпания всех утверждённых безопасных маршрутов; это подтверждается одновременно флагами `--hard-blocker --safe-routes-exhausted`. Иначе guard требует `ATTEMPT_REJECTED` и продолжение выполнения. Коррекция пользователя снимает даже ранее записанный `BLOCKED` и возвращает задачу к следующей обязательной стадии.

## Контроль области задачи

Запрос «создай персонажа» не разрешает исправлять `style_pack_manager.py`, тесты или правила проекта. Если такая проблема обнаружена, агент фиксирует блокер:

```powershell
python tools\task_execution_guard.py checkpoint `
  --state "<path>" `
  --event BLOCKER `
  --summary "Менеджер отклоняет подтверждённый профиль; требуется отдельное решение пользователя об изменении инфраструктуры."
```

`SCOPE_CHANGE` проходит только с `--user-approved-scope-change` и только после прямого согласия пользователя. Исправление инфраструктуры выполняется отдельной задачей с собственной целью и бюджетом.

## Реакция на срабатывание

Событие `WATCHDOG_TRIGGERED` означает жёсткую остановку только дальнейшей подготовки. Задача остаётся `ACTIVE`. Разрешены следующие переходы:

1. завершить `prepare-generation`, разрешить точные вложения текущей стадии, оценить точный промпт и слоты и выполнить `prepare-call`, который пишет `READY_FOR_EXECUTION`;
2. безопасно запустить уже готовую основную операцию через `EXECUTION_STARTED`;
3. проверить фактическое состояние уже запущенной операции и дождаться/записать её видимый результат;
4. записать `BLOCKER` только при конкретной внешней непреодолимой причине и немедленно показать её пользователю.

Продолжать анализ, добавлять референсы «на всякий случай», подбирать почти одинаковые формулировки по кругу или менять проект запрещено. Завершать задачу, просить «перезапустить guard» или объявлять таймер блокером также запрещено.

Проверить живое состояние и пересчитать временной лимит помогает команда:

```powershell
python tools\task_execution_guard.py status --state "<path>\EXECUTION_GUARD.json"
```

Статус проверяется не реже чем после шести содержательных инструментальных действий и при десяти активных минутах без видимого результата. Метка интерфейса `active` не считается доказательством прогресса.

## Как защита остановила бы инцидент 2026-07-20

В запросе на персонажа основным результатом был первый кадр `FACE_IDENTITY`. Фактически задача больше двух часов готовила материалы, попыталась изменить менеджер и тесты, сообщила `READY_FOR_GENERATION`, но не вызвала генератор.

С новым контрактом последовательность прерывается раньше:

1. Цель блокируется как создание видимого `FACE_IDENTITY`, а не развитие инфраструктуры StoryArt.
2. Попытка изменить менеджер или тесты получает ошибку `Scope expansion is forbidden` и превращается в короткий отчёт о блокере.
3. Через 20 активных минут или 12 этапов подготовки guard записывает `WATCHDOG_TRIGGERED`, сохраняет задачу активной и запрещает дальнейший анализ, но разрешает точное разрешение слотов, риск-оценку фактического вызова и `prepare-call`.
4. После `READY_FOR_EXECUTION` разрешён прямой `EXECUTION_STARTED` с тем же планом и стадией, необязательный `CALL_VALIDATED` перед ним либо конкретный `BLOCKER`.
5. Задача не может отметить работу завершённой и не может записать генерацию без реального файла результата.

Таким образом, при том же сбое пользователь получил бы изображение либо конкретный блокер примерно в пределах первого контрольного окна, а не спустя два часа без результата.

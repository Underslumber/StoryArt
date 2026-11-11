# Завершение сохранённой автором сцены

`approve-variation --kind scene --author-accepted-scene` сохраняет точный кадр
в `02_SCENES` как `APPROVED_SCENE`, сохраняя замечания QA. Это авторское
решение о сцене, а не технический PASS, TEST-приёмка, новая личность или одежда.

После штатного сохранения checkpoint `AUTHOR_SCENE_STORED` завершает только
одну текущую `SCENE`/`SINGLE_PASS`. Его единственное evidence — файл
`AUTHOR_SCENE_COMPLETION.json` в текущем request folder. Валидатор
`tools/author_scene_completion.py` проверяет:

- текущие request, attempt, revision, character и неизменяемый вызов;
- исходные байты provider, сохранённый кадр и единственную запись APPROVED_SCENE;
- точную цитату и сохранённые QA findings из штатной записи;
- реальные user messages из host session log за пределами writable workspace,
  после показа именно этого исходного изображения (SHA256 встроенного image
  блока совпадает с provider); копии цитат агента недостаточно; между показом
  и принятием не допускаются другие user turns или изображения;
- пути без symlink/junction, хеши отдельных строк журнала и delivery evidence.

Receipt содержит `schema_version: 1`, `request_id`, `attempt_id`,
`task_revision`, `character_id`, `generation_id`, `approved_scene_path`,
`output_sha256`, `approval_quote`, `technical_findings`, `delivery_evidence`,
`host_session_log`, `provider_output_path`, `host_delivery` и
`host_user_messages`. Log bindings задают номер строки (с единицы) и SHA256
её UTF-8 байтов без перевода строки. User bindings дополнительно задают
`message_id` и точный `quote`; несколько цитат объединяются через `; `
в том же порядке, что при штатном сохранении.

Checkpoint атомарно фиксирует доставку как `APPROVED_SCENE`, закрывает только
эту сцену и снимает полученную активную попытку. Уже доставленный TEST не
переписывается. Повтор с тем же receipt идемпотентен.
`COMPLETE` заново проверяет все доказательства. Старые QA, provider artifact
и result evidence не переписываются, технические FAIL не становятся PASS.
CHARACTER_BASE, UNKNOWN, другой request/revision и смешение с другими
специальными маршрутами завершения не допускаются.

```powershell
.\scripts\storyart.ps1 task_execution_guard checkpoint --state <request>/EXECUTION_GUARD.json --event AUTHOR_SCENE_STORED --summary "Автор принял и сохранил точную сцену" --evidence <request>/AUTHOR_SCENE_COMPLETION.json
```

После этого используется обычный checkpoint `COMPLETE` и проверка `status`.

Новая `USER_CORRECTION` или `STAGE_REOPENED` архивирует прежний receipt и
удаляет его активную completion binding, позволяя продолжить тот же запрос.
Исходный host session log перечитывается при завершении: если он исчез или
строки изменились, проверка закрывается с ошибкой. Корень журнала берётся из
host home и не переопределяется переменной окружения; доверие предполагает,
что sessions находятся за пределами разрешённых агенту writable roots.

# Reference packs

Каталоги необязательных пулов референсов. В Git лежат только условия
источника, список файлов с SHA-256 и ссылкой на оригинал (`files.csv`) и
разметка StoryArt (`metadata/`). Сами изображения в репозиторий не
попадают: их раздают авторы, и их условия запрещают перепубликацию.

```powershell
# Какие пулы есть и что уже установлено.
.\scripts\storyart.ps1 reference_packs list
# Скачать атлас поз с сайта автора и разложить в BODY_REFERENCE_LIBRARY/POSE_ATLAS.
.\scripts\storyart.ps1 reference_packs install POSE_ATLAS
# Взять файлы из своей копии вместо скачивания (совпадение проверяется по хешу).
.\scripts\storyart.ps1 reference_packs install POSE_ATLAS --from-dir D:\backup\pose_atlas
# Проверить хеши установленных файлов.
.\scripts\storyart.ps1 reference_packs verify
```

Файл принимается только при совпадении SHA-256. Если оригинал изменился или
пропал, `install` сообщает `HASH_MISMATCH` или `NO_SOURCE` и ничего не
подменяет. Разметка из `metadata/` копируется в пул, если локальной копии нет
или она совпадает; изменённая локальная разметка сохраняется
(`--overwrite-metadata` заменяет её).

То же при подготовке проекта на чистой машине:

```powershell
.\scripts\bootstrap.ps1 -WithReferences POSE_ATLAS,COSTUME_DB_01
# Все пулы; файлы без публичной ссылки берутся из своей копии.
.\scripts\bootstrap.ps1 -WithReferences all -ReferencesFromDir D:\backup\refs
```

Без `-WithReferences` ничего не скачивается, `bootstrap` только показывает список.

Обновить каталог после изменений пула на машине, где он установлен:

```powershell
.\scripts\storyart.ps1 reference_packs build POSE_ATLAS
```

| Пул | Источник | Каталог |
|---|---|---|
| `POSE_ATLAS` | [ai-pose-gallery](https://ai-pose-gallery.pages.dev/), зеркало anhao на зафиксированном коммите | готов, 1439 файлов |
| `COSTUME_DB_01` | [ai-costume-gallery](https://ai-costume-gallery.pages.dev/) | готов, 1407 файлов, все скачиваются |
| `PINTEREST_SKETCH` | пины Pinterest, авторы не установлены | 586 файлов, 350 скачиваются с i.pinimg.com, остальные — вырезки |
| `DRAWING_GUIDES` | уроки [@oldmanlux](https://x.com/oldmanlux) | 238 файлов, все — вырезки фрагментов |
| `OLCHAS` | работы OlchaS | 620 файлов, 222 скачиваются с Tumblr/JoyReactor |

Вырезки и файлы, у которых нет постоянной ссылки на оригинал (подписанные
ссылки DeviantArt истекают), в `files.csv` записаны без `url`. Их можно
поставить только из своей копии: `install <ПУЛ> --from-dir <папка>`; иначе
`install` сообщает `NO_SOURCE`. Недоступная ссылка даёт `DOWNLOAD_FAILED`, а
остальные файлы пула ставятся дальше. Права на перераспространение у
Pinterest не установлены, у остальных пулов — запрещены.

В `pack.json` раздел `build` задаёт список файлов маской `glob` или полем
`paths_from` (манифест пула), а ссылки — шаблоном `url_template` или
`url_sources`: ссылка из манифеста загрузок присваивается файлу только при
совпадении SHA-256, поэтому вырезка не получает ссылку на свой оригинал.
`build` убирает из копий разметки абсолютный путь рабочей папки.

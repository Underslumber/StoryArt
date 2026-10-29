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

Обновить каталог после изменений пула на машине, где он установлен:

```powershell
.\scripts\storyart.ps1 reference_packs build POSE_ATLAS
```

| Пул | Источник | Каталог |
|---|---|---|
| `POSE_ATLAS` | [ai-pose-gallery](https://ai-pose-gallery.pages.dev/), зеркало anhao на зафиксированном коммите | готов, 1439 файлов |
| `COSTUME_DB_01` | [ai-costume-gallery](https://ai-costume-gallery.pages.dev/) | нужно выполнить `build` на машине с пулом |

Пулы `PINTEREST_SKETCH`, `DRAWING_GUIDES` и `OLCHAS` пока не подключены:
у их файлов разные авторы и вырезки, это следующий шаг.

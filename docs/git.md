# Using geo3way with git

## Set up (once per clone, by every collaborator)

```bash
pip install geo3way
cd my-map-repo
geo3way install-git                       # default patterns: *.geojson *.geojsonl *.gpkg
geo3way install-git "data/*.geojson"      # or your own patterns
geo3way install-git --options "--id-field uuid"   # ids in an attribute instead of "id"
geo3way install-git --global              # for all repositories of this user
```

This writes three entries to the repository's git config, plus lines in `.gitattributes`:

```ini
[merge "geo3way"]
    name = geo3way geometry-aware merge
    driver = "/path/to/python" -m geo3way git-merge-driver %O %A %B %P
[diff "geo3way"]
    command = "/path/to/python" -m geo3way git-diff
```

```gitattributes
*.geojson merge=geo3way diff=geo3way
```

Commit `.gitattributes`. The config part stays local: for security reasons git never runs a
command named in a cloned repository unless that clone's own config allows it. That is why
each collaborator runs `geo3way install-git` once.

## Day to day

* `git diff` shows feature-level changes:

  ```text
  geo3way diff a/city.geojson b/city.geojson
  ~ b1  floors: 3 -> 4; geometry: 1 edited vertex range in part 0
  - b4  deleted
  + shed-b  added Polygon
  ```

  To see the raw text diff, use `git diff --no-ext-diff`.
* `git merge`, `git pull`, `git rebase` and `git cherry-pick` call geo3way for matching
  files.
  * **Clean merge:** nothing to do.
  * **Conflicts:** git reports the file as conflicted. The file is valid GeoJSON or GeoPackage
    holding the merged data, with *our* version of each conflicted feature (no `<<<<<<<`
    markers). Next to it, `<file>.conflicts.geojson` lists every conflict; open it in QGIS.
    Edit the data as needed, then `git add <file>` and `git commit`. Delete the report
    afterwards (`*.conflicts.geojson` is a good line for `.gitignore`).

## Tips

* **Keep files diff-friendly.** geo3way writes GeoJSON with one feature per line and keeps
  feature order, so plain `git log -p` stays readable too.
* **GeoPackage in git.** GeoPackages are binary SQLite files. git stores each version whole,
  so the repository grows with every commit. This is fine for small layers. For large ones,
  use GeoJSON(L) in git, or a database-aware tool such as Kart.
* **Ids.** Use UUIDs for features created by different people (`geo3way add-ids`). QGIS keeps
  attribute values when you edit geometry, so ids survive normal editing. Do not use
  *Save As* with "fid" regeneration when the fid is your id.

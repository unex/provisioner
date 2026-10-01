# Provisioner phone data

This repository holds the phone data for
[endpointman](https://github.com/unex/endpointman), the endpoint manager
module for FreePBX. The data is brand files, family files, configuration
templates, phone dictionaries, images and ring tones.

The repository holds no program code. The drivers (`base.php` and
`phone.php`) ship with endpointman in `lib/provisioner/`.

## Package server

GitHub Pages publishes the packages. Set the endpointman "Package Server"
to the URL for your FreePBX version.

| FreePBX | endpointman | Package server URL |
| --- | --- | --- |
| 17 | 17 | `https://unex.github.io/provisioner/v4/` |
| 16 | 16 | `https://unex.github.io/provisioner/` |

endpointman 17 sets the `v4/` URL when you install or upgrade the module.

The `v4/` site holds only data files. endpointman 17 refuses a package
that holds any other file.

## Layout

| Path | Content |
| --- | --- |
| `endpoint/<brand>/brand_data.json` | The brand: name, id, directory and OUI list. |
| `endpoint/<brand>/<family>/family_data.json` | The family and its model list. |
| `endpoint/<brand>/<family>/` | The templates and other data files of the family. |
| `build.py` | The build. |
| `tests/check_build.py` | The check of the build result. |
| `legacy/` | The frozen site for FreePBX 16. Refer to [Legacy files](#legacy-files). |
| `.github/workflows/static.yml` | The workflow that builds, checks and deploys the site. |

## Build

The build needs Python 3 and git. It uses only the Python standard
library.

```sh
python3 build.py
python3 tests/check_build.py
```

The build deletes `build/` and writes a new site there:

| Path | Content |
| --- | --- |
| `build/v4/master.json` | The list of brands. |
| `build/v4/update_status` | `1` while the build runs, `0` after a good build. |
| `build/v4/<brand>/<brand>.json` | `brand_data.json` with the family list, package name, md5sum, `last_modified` and `changelog` from the build. |
| `build/v4/<brand>/<brand>.tgz` | All files under `endpoint/<brand>/`, with `<brand>/` as the top directory. |
| `build/<other paths>` | A copy of `legacy/`. |

The build does not publish firmware. It skips each directory with the
name `firmware`.

The tarballs are reproducible. The entries are sorted. Each entry has
owner 0 and group 0, mode 0755 for a directory and 0644 for a file, and
the brand `last_modified` as its time. Thus the same commit gives the
same md5sum.

### Timestamps

The `last_modified` of a brand is the time of the last commit that
changed `endpoint/<brand>/`. The commit that removed the drivers changed
every brand, so the build ignores changes to `.php` files and to
`anacrypt`. The lowest value is 1684822309, the time of the 2023 deploy.
Thus endpointman shows an update only for a brand with new data.

The `changelog` of a brand is the subject of that commit. `master.json`
gets the highest `last_modified` of all brands.

The build reads the full git history. In a shallow clone it stops with
an error. Outside a git work tree it uses the file times and shows a
warning.

### Allowed files

The build accepts a file only when it passes all of these rules:

1. The extension is on this list (any case): `json xml cfg jpg png gif
   bmp wav mp3 log htm html txt ini cnf conf dat bin csv`.
   Or the full name is on this list: `cfg$mac`, `$mac`, `ht70x`.
2. The file does not start with `\x7fELF` or `MZ`.
3. The file does not start with `#!`. A Yealink configuration file
   starts with `#!version:`, so the build accepts that header.
4. The file does not contain `<?php` (any case) or `<?=`.
5. The file is a regular file. It is not a symbolic link or a hard link.
6. The path is relative and does not contain `..`.

When a file fails a rule, the build lists each rejected path and stops
with exit status 1. The build does not remove a bad file without a
message. endpointman applies the same lists when it extracts a package.
If you change a list, change it in both repositories.

`tests/check_build.py` reads each tarball in `build/v4/` and applies the
same rules to each entry. It also compares each md5sum with its tarball,
and compares the site root with `legacy/` byte for byte.

## Deploy

A push to `master` runs the workflow. The workflow runs the build and
the check, and then deploys `build/` to GitHub Pages. When the build or
the check fails, the workflow does not deploy, and Pages keeps the last
good site.

A pull request runs the build and the check only.

## Legacy files

`legacy/` holds the files that the site served at its root URL before
the `v4/` site. These are `master.json`, `update_status`,
`provisioner_net.tgz` and the JSON file and tarball of each brand. They
come from the deploy of 2023-05-23. Each tarball matches the md5sum in
its brand file.

These files serve endpointman 16 on FreePBX 16. endpointman 16 needs the
PHP library in `provisioner_net.tgz`, and the old tarballs hold PHP
drivers. Do not change these files. The build copies them to the site
root without changes and never builds them again.

When FreePBX 16 support ends, delete `legacy/`. The site root then
holds only `v4/`.

## Change the data

1. Edit the files under `endpoint/<brand>/`.
2. Run `python3 build.py` and `python3 tests/check_build.py`.
3. Commit the change. The commit time becomes the new `last_modified`
   of the brand.

To add a model, add an entry to `model_list` in `family_data.json`. Use
a model `id` that the family does not use. endpointman makes the model
key from the brand id, the family id and the model id.

## License

Refer to [LICENSE](LICENSE).

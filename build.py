#!/usr/bin/env python3
"""Build the package server site from endpoint/.

The build writes the site to build/:

  build/v4/master.json             brand list for endpointman 17
  build/v4/update_status           "1" during the build, "0" after
  build/v4/<brand>/<brand>.json    brand_data.json with build values
  build/v4/<brand>/<brand>.tgz     all data files of the brand
  build/<legacy files>             copy of legacy/, without changes

The build accepts only data files. It stops with exit status 1 and lists
each rejected path when a file fails a rule. It does not remove a bad
file without a message.

Python 3 standard library only.
"""

import gzip
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile

ROOT = os.path.dirname(os.path.abspath(__file__))
ENDPOINT_DIR = os.path.join(ROOT, "endpoint")
LEGACY_DIR = os.path.join(ROOT, "legacy")
BUILD_DIR = os.path.join(ROOT, "build")
SITE_DIR = os.path.join(BUILD_DIR, "v4")

# endpointman uses the same two lists when it extracts a package. Change
# both together.
ALLOWED_EXTENSIONS = frozenset((
    "json", "xml", "cfg", "jpg", "png", "gif", "bmp", "wav", "mp3", "log",
    "htm", "html", "txt", "ini", "cnf", "conf", "dat", "bin", "csv",
))
ALLOWED_NAMES = frozenset(("cfg$mac", "$mac", "ht70x"))

# Yealink config files must start with this header. It is not an
# interpreter line, so the #! rule allows it.
YEALINK_HEADER = b"#!version:"

# The time of the 2023 deploy. A brand with no newer data change keeps
# this value, so that endpointman shows no update for it.
LAST_MODIFIED_FLOOR = 1684822309

# The commit that moved the drivers to endpointman removed these files
# from every brand. Do not count it as a change to the brand data.
HISTORY_EXCLUDES = ("**/*.php", "**/anacrypt")

BRAND_DIR_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
MASTER_VERSION = "3"
MASTER_PACKAGE = "provisioner_net.tgz"


class BuildError(Exception):
    pass


def warn(message):
    print("WARNING: " + message, file=sys.stderr)


def name_is_allowed(name):
    """Return True when the file name is on the allowlist."""
    if name in ALLOWED_NAMES:
        return True
    base, ext = os.path.splitext(name)
    if not base or not ext:
        return False
    return ext[1:].lower() in ALLOWED_EXTENSIONS


def content_problem(data):
    """Return the reason that the content is not data, or None."""
    if data.startswith(b"\x7fELF"):
        return "ELF executable"
    if data.startswith(b"MZ"):
        return "DOS or Windows executable"
    if data.startswith(b"#!") and not data.startswith(YEALINK_HEADER):
        return "script with #!"
    if b"<?php" in data.lower():
        return "contains <?php"
    if b"<?=" in data:
        return "contains <?="
    return None


def path_problem(relpath):
    """Return the reason that a package path is not safe, or None."""
    if relpath.startswith("/") or relpath.startswith("\\"):
        return "absolute path"
    if re.match(r"^[A-Za-z]:", relpath):
        return "absolute path"
    parts = relpath.replace("\\", "/").split("/")
    if ".." in parts:
        return "path contains .."
    if "" in parts or "." in parts:
        return "path is not normal"
    return None


def file_problem(relpath, data):
    """Apply all rules to one regular file. Return a reason or None."""
    reason = path_problem(relpath)
    if reason:
        return reason
    if not name_is_allowed(os.path.basename(relpath)):
        return "name is not on the allowlist"
    return content_problem(data)


def git(*args):
    result = subprocess.run(
        ("git", "-C", ROOT) + args,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", "replace")


def git_is_usable():
    inside = git("rev-parse", "--is-inside-work-tree")
    if inside is None or inside.strip() != "true":
        return False
    if (git("rev-parse", "--is-shallow-repository") or "").strip() == "true":
        raise BuildError(
            "The clone is shallow, so git log gives wrong times. "
            "Fetch the full history (actions/checkout fetch-depth: 0)."
        )
    return True


def brand_history(brand, use_git, files):
    """Return (last_modified, changelog) for a brand."""
    if use_git:
        top = "endpoint/" + brand
        pathspecs = [top] + [
            ":(exclude,glob)" + top + "/" + pattern
            for pattern in HISTORY_EXCLUDES
        ]
        out = git("log", "-1", "--format=%ct%x00%s", "--", *pathspecs)
        if out and "\x00" in out:
            stamp, subject = out.strip("\n").split("\x00", 1)
            pending = git("-c", "core.fileMode=false", "status",
                          "--porcelain", "--", top)
            if pending:
                warn("endpoint/%s has changes that are not committed. "
                     "last_modified comes from the last commit." % brand)
            return max(int(stamp), LAST_MODIFIED_FLOOR), subject
        warn("git has no history for endpoint/%s. "
             "last_modified comes from the file times." % brand)
    newest = max(os.lstat(path).st_mtime for path, _ in files)
    return max(int(newest), LAST_MODIFIED_FLOOR), ""


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, obj):
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def collect_brand(brand, rejected):
    """Walk endpoint/<brand>. Return the directories and files to pack.

    Each entry is (source path, path in the tarball). The function adds
    each bad path to rejected.
    """
    brand_root = os.path.join(ENDPOINT_DIR, brand)
    dirs = [(brand_root, brand)]
    files = []
    for current, subdirs, names in os.walk(brand_root, followlinks=False):
        rel_current = os.path.relpath(current, ENDPOINT_DIR).replace(os.sep, "/")
        keep = []
        for sub in sorted(subdirs):
            source = os.path.join(current, sub)
            relpath = rel_current + "/" + sub
            if os.path.islink(source):
                rejected.append(("endpoint/" + relpath, "symbolic link"))
                continue
            if sub == "firmware":
                print("endpoint/%s holds firmware. The build does not "
                      "publish firmware." % relpath)
                continue
            reason = path_problem(relpath)
            if reason:
                rejected.append(("endpoint/" + relpath, reason))
                continue
            keep.append(sub)
            dirs.append((source, relpath))
        subdirs[:] = keep
        for name in sorted(names):
            source = os.path.join(current, name)
            relpath = rel_current + "/" + name
            info = os.lstat(source)
            if stat.S_ISLNK(info.st_mode):
                rejected.append(("endpoint/" + relpath, "symbolic link"))
                continue
            if not stat.S_ISREG(info.st_mode):
                rejected.append(("endpoint/" + relpath, "not a regular file"))
                continue
            if info.st_nlink > 1:
                rejected.append(("endpoint/" + relpath, "hard link"))
                continue
            with open(source, "rb") as handle:
                data = handle.read()
            reason = file_problem(relpath, data)
            if reason:
                rejected.append(("endpoint/" + relpath, reason))
                continue
            files.append((source, relpath))
    dirs.sort(key=lambda item: item[1].split("/"))
    files.sort(key=lambda item: item[1].split("/"))
    return dirs, files


def make_tarball(path, dirs, files, mtime):
    """Write a reproducible gzip tarball. Return its md5sum."""
    def base_info(name):
        info = tarfile.TarInfo(name)
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.mtime = mtime
        return info

    raw = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                       compresslevel=9, mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w",
                          format=tarfile.GNU_FORMAT) as tar:
            entries = [(rel, None) for _, rel in dirs]
            entries += [(rel, src) for src, rel in files]
            entries.sort(key=lambda item: item[0].split("/"))
            for rel, source in entries:
                info = base_info(rel)
                if source is None:
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    tar.addfile(info)
                else:
                    with open(source, "rb") as handle:
                        data = handle.read()
                    info.type = tarfile.REGTYPE
                    info.mode = 0o644
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
    blob = raw.getvalue()
    with open(path, "wb") as handle:
        handle.write(blob)
    return hashlib.md5(blob).hexdigest()


def family_entry(brand, family):
    data = read_json(os.path.join(ENDPOINT_DIR, brand, family,
                                  "family_data.json"))["data"]
    if data.get("directory") != family:
        raise BuildError("endpoint/%s/%s/family_data.json: directory is %r"
                         % (brand, family, data.get("directory")))
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "directory": data.get("directory"),
        "description": data.get("description", ""),
        "changelog": data.get("changelog", ""),
        "last_modified": data.get("last_modified"),
    }


def build_brand(brand, use_git, dirs, files):
    """Build one brand. Return its master.json entry and last_modified."""
    brand_json = read_json(os.path.join(ENDPOINT_DIR, brand,
                                        "brand_data.json"))
    info = brand_json["data"]["brands"]
    if info.get("directory") != brand:
        raise BuildError("endpoint/%s/brand_data.json: directory is %r"
                         % (brand, info.get("directory")))

    families = sorted(
        name for name in os.listdir(os.path.join(ENDPOINT_DIR, brand))
        if os.path.isfile(os.path.join(ENDPOINT_DIR, brand, name,
                                       "family_data.json"))
    )
    last_modified, changelog = brand_history(brand, use_git, files)

    out_dir = os.path.join(SITE_DIR, brand)
    os.makedirs(out_dir)
    package = brand + ".tgz"
    md5sum = make_tarball(os.path.join(out_dir, package), dirs, files,
                          last_modified)

    info["family_list"] = [family_entry(brand, fam) for fam in families]
    info["package"] = package
    info["md5sum"] = md5sum
    info["last_modified"] = last_modified
    info["changelog"] = changelog
    write_json(os.path.join(out_dir, brand + ".json"), brand_json)

    print("%-12s %s  %d  %d files" % (brand, md5sum, last_modified,
                                      len(files)))
    return {"name": info["name"], "directory": brand}, last_modified


def copy_legacy():
    """Copy legacy/ to the build root. Return the number of files."""
    if not os.path.isdir(LEGACY_DIR):
        return 0
    count = 0
    for current, subdirs, names in os.walk(LEGACY_DIR):
        subdirs.sort()
        rel = os.path.relpath(current, LEGACY_DIR)
        target_dir = os.path.normpath(os.path.join(BUILD_DIR, rel))
        if os.path.relpath(target_dir, BUILD_DIR).split(os.sep)[0] == "v4":
            raise BuildError("legacy/ must not hold a v4 directory")
        os.makedirs(target_dir, exist_ok=True)
        for name in sorted(names):
            shutil.copyfile(os.path.join(current, name),
                            os.path.join(target_dir, name))
            count += 1
    return count


def main():
    if os.path.isdir(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)
    os.makedirs(SITE_DIR)
    write_text(os.path.join(SITE_DIR, "update_status"), "1")

    use_git = git_is_usable()
    if not use_git:
        warn("This is not a git work tree. last_modified comes from the "
             "file times.")

    brands = sorted(
        name for name in os.listdir(ENDPOINT_DIR)
        if os.path.isfile(os.path.join(ENDPOINT_DIR, name, "brand_data.json"))
    )
    rejected = []
    for name in sorted(os.listdir(ENDPOINT_DIR)):
        if name not in brands:
            rejected.append(("endpoint/" + name, "not a brand directory"))
        elif not BRAND_DIR_PATTERN.fullmatch(name):
            rejected.append(("endpoint/" + name, "bad brand directory name"))
    if not brands:
        raise BuildError("endpoint/ holds no brand")

    # Check all brands first, so that one run lists every bad path.
    contents = {brand: collect_brand(brand, rejected) for brand in brands}
    if rejected:
        print("The build rejected these paths:", file=sys.stderr)
        for path, reason in rejected:
            print("  %s: %s" % (path, reason), file=sys.stderr)
        return 1

    entries = []
    stamps = []
    for brand in brands:
        entry, stamp = build_brand(brand, use_git, *contents[brand])
        entries.append(entry)
        stamps.append(stamp)

    master = {"data": {
        "last_modified": max(stamps),
        "package": MASTER_PACKAGE,
        "brands": entries,
        "version": MASTER_VERSION,
    }}
    write_json(os.path.join(SITE_DIR, "master.json"), master)

    count = copy_legacy()
    print("Copied %d legacy files to build/" % count)

    write_text(os.path.join(SITE_DIR, "update_status"), "0")
    print("Built %d brands in build/v4/" % len(entries))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BuildError as error:
        print("ERROR: %s" % error, file=sys.stderr)
        sys.exit(1)

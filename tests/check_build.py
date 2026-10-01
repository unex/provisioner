#!/usr/bin/env python3
"""Check the site that build.py wrote to build/.

The check reads the tarballs back and applies the rules of build.py to
each entry. It also makes sure that the site root holds the files of
legacy/ without changes. The check stops with exit status 1 when it
finds a problem.
"""

import filecmp
import hashlib
import os
import sys
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.dont_write_bytecode = True

import build  # noqa: E402

errors = []


def fail(message):
    errors.append(message)


def md5_of(path):
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_tarball(brand, path):
    count = 0
    with tarfile.open(path, "r:gz") as tar:
        for member in tar:
            name = member.name.rstrip("/") if member.isdir() else member.name
            where = "%s: %s" % (os.path.relpath(path, build.BUILD_DIR), name)
            reason = build.path_problem(name)
            if reason:
                fail("%s: %s" % (where, reason))
                continue
            if name.split("/")[0] != brand:
                fail("%s: first path part is not %s" % (where, brand))
            if member.isdir():
                continue
            if not member.isreg():
                fail("%s: not a regular file or a directory" % where)
                continue
            data = tar.extractfile(member).read()
            reason = build.file_problem(name, data)
            if reason:
                fail("%s: %s" % (where, reason))
            count += 1
    return count


def check_brand(brand):
    brand_dir = os.path.join(build.SITE_DIR, brand)
    json_path = os.path.join(brand_dir, brand + ".json")
    if not os.path.isfile(json_path):
        fail("v4/%s/%s.json is missing" % (brand, brand))
        return None
    info = build.read_json(json_path)["data"]["brands"]
    if info.get("directory") != brand:
        fail("v4/%s/%s.json: directory is %r"
             % (brand, brand, info.get("directory")))
    package = info.get("package")
    if package != brand + ".tgz":
        fail("v4/%s/%s.json: package is %r" % (brand, brand, package))
        return None
    tgz_path = os.path.join(brand_dir, package)
    if not os.path.isfile(tgz_path):
        fail("v4/%s/%s is missing" % (brand, package))
        return None
    actual = md5_of(tgz_path)
    if info.get("md5sum") != actual:
        fail("v4/%s/%s.json: md5sum is %r, the tarball has %s"
             % (brand, brand, info.get("md5sum"), actual))
    stamp = info.get("last_modified")
    if not isinstance(stamp, int) or stamp < build.LAST_MODIFIED_FLOOR:
        fail("v4/%s/%s.json: last_modified is %r" % (brand, brand, stamp))
    extra = sorted(set(os.listdir(brand_dir)) - {brand + ".json", package})
    for name in extra:
        fail("v4/%s/%s: file is not expected" % (brand, name))
    files = check_tarball(brand, tgz_path)
    print("%-12s %s  %d files" % (brand, actual, files))
    return stamp


def check_site():
    site = build.SITE_DIR
    if not os.path.isdir(site):
        fail("build/v4/ is missing. Run build.py first.")
        return
    with open(os.path.join(site, "update_status"), "rb") as handle:
        status = handle.read()
    if status != b"0":
        fail("v4/update_status is %r, not '0'" % status)

    master = build.read_json(os.path.join(site, "master.json"))["data"]
    if master.get("version") != build.MASTER_VERSION:
        fail("v4/master.json: version is %r" % master.get("version"))
    if master.get("package") != build.MASTER_PACKAGE:
        fail("v4/master.json: package is %r" % master.get("package"))
    listed = [entry.get("directory") for entry in master.get("brands", [])]
    if listed != sorted(listed):
        fail("v4/master.json: brands are not sorted by directory")
    present = sorted(name for name in os.listdir(site)
                     if os.path.isdir(os.path.join(site, name)))
    if sorted(listed) != present:
        fail("v4/master.json lists %s, build/v4/ holds %s"
             % (listed, present))
    for name in sorted(os.listdir(site)):
        path = os.path.join(site, name)
        if not os.path.isdir(path) and name not in ("master.json",
                                                    "update_status"):
            fail("v4/%s: file is not expected" % name)

    stamps = [check_brand(brand) for brand in listed]
    stamps = [stamp for stamp in stamps if stamp is not None]
    if stamps and master.get("last_modified") != max(stamps):
        fail("v4/master.json: last_modified is %r, newest brand is %d"
             % (master.get("last_modified"), max(stamps)))


def relative_files(top, skip=None):
    found = set()
    for current, subdirs, names in os.walk(top):
        rel_dir = os.path.relpath(current, top)
        if rel_dir == "." and skip in subdirs:
            subdirs.remove(skip)
        for name in names:
            found.add(os.path.normpath(os.path.join(rel_dir, name)))
    return found


def check_legacy():
    if not os.path.isdir(build.LEGACY_DIR):
        print("legacy/ is not present. Nothing to compare.")
        return
    legacy = relative_files(build.LEGACY_DIR)
    root = relative_files(build.BUILD_DIR, skip="v4")
    for rel in sorted(legacy - root):
        fail("build/%s is missing (legacy/ has it)" % rel)
    for rel in sorted(root - legacy):
        fail("build/%s is not in legacy/" % rel)
    same = 0
    for rel in sorted(legacy & root):
        if not filecmp.cmp(os.path.join(build.LEGACY_DIR, rel),
                           os.path.join(build.BUILD_DIR, rel), shallow=False):
            fail("build/%s is not the same as legacy/%s" % (rel, rel))
        else:
            same += 1
    print("legacy       %d files at the site root are the same as legacy/"
          % same)


def main():
    check_site()
    check_legacy()
    if errors:
        print("The check found %d problems:" % len(errors), file=sys.stderr)
        for message in errors:
            print("  " + message, file=sys.stderr)
        return 1
    print("The check found no problems.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

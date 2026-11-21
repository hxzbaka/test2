#!/usr/bin/env python3
"""Prepare verified firmware inputs and adapt pinned UniXNU scripts for T610YP Data seeds."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import zipfile

TARGETS = {
    "iphone-17.7": {
        "sha256": "8a68c431f2f5cce0c53d20a42930b898bcef7236cbd3efa307bcdc487375c82a",
        "product": "iPhone12,1", "device_class": "n104ap",
        "version": "17.7", "build": "21H16",
    },
    "iphone-x-16.7.16": {
        "sha256": "ece1194f7e233e9109c5790a8d23a4564a10a254e15ec5b09978a343265568fb",
        "product": "iPhone10,3", "device_class": "d22ap",
        "version": "16.7.16", "build": "20H392",
    },
}
IPAD_LAUNCHD_SHA256 = "8cec9a3c44a443b08e977ccc5d577850316ad428c575dc01f09b018dbd5b6a25"
IPAD_TARGET = "ipados-17.7.11"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise SystemExit("Pinned seed script does not match expected input: " + old)
    return text.replace(old, new, 1)


def extract(ipsw, output, target):
    spec = TARGETS[target]
    if digest(ipsw) != spec["sha256"]:
        raise SystemExit("iPhone IPSW SHA-256 does not match " + target)
    if output.exists():
        raise SystemExit("Refusing to overwrite " + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ipsw) as archive:
        manifest = plistlib.loads(archive.read("BuildManifest.plist"))
        if (manifest.get("ProductVersion") != spec["version"] or
                manifest.get("ProductBuildVersion") != spec["build"] or
                spec["product"] not in manifest.get("SupportedProductTypes", [])):
            raise SystemExit("Unexpected IPSW product or version")
        matches = [item for item in manifest["BuildIdentities"]
                   if item["Info"].get("DeviceClass", "").lower() == spec["device_class"]
                   and item["Info"].get("RestoreBehavior") == "Erase"]
        if len(matches) != 1:
            raise SystemExit("Expected one erase identity for " + target)
        member = matches[0]["Manifest"]["OS"]["Info"]["Path"]
        entry = archive.getinfo(member)
        partial = output.with_suffix(".partial")
        try:
            with archive.open(entry) as source, partial.open("xb") as dest:
                shutil.copyfileobj(source, dest, 8 * 1024 * 1024)
            if partial.stat().st_size != entry.file_size:
                raise SystemExit("Incomplete OS extraction")
            partial.rename(output)
        finally:
            partial.unlink(missing_ok=True)
    print(json.dumps({"product": spec["product"], "version": spec["version"], "build": spec["build"],
                      "member": member, "sha256": digest(output)}, indent=2))


def select_system_mount(entities, spec):
    mounts = set()
    for entity in entities:
        mount = entity.get("mount-point")
        if not mount:
            continue
        path = Path(mount)
        version_path = path / "System/Library/CoreServices/SystemVersion.plist"
        if not version_path.is_file():
            continue
        with version_path.open("rb") as source:
            version = plistlib.load(source)
        if (version.get("ProductBuildVersion"), version.get("ProductVersion")) == (
                spec["build"], spec["version"]):
            mounts.add(path)
    if len(mounts) != 1:
        raise SystemExit(f"Expected one mounted System volume for {spec['build']}, found {len(mounts)}")
    mount = mounts.pop()
    if not (mount / "sbin/launchd").is_file() or not (mount / "private/var").is_dir():
        raise SystemExit(f"System volume at {mount} is missing launchd or the initial private/var tree")
    return mount


def prepare(unixnu, image, target):
    spec = TARGETS[target]
    scripts = unixnu / "scripts"
    attached = plistlib.loads(subprocess.check_output(
        ["hdiutil", "attach", "-readonly", "-nobrowse", "-owners", "off", "-plist", str(image)]))
    entities = attached["system-entities"]
    device = next(e["dev-entry"] for e in entities if e.get("dev-entry"))
    try:
        mount = select_system_mount(entities, spec)
        fixture = (scripts / "prepare-owner-data-fixture.py").read_text()
        fixture = replace_once(
            fixture, "source = ROOT / '.cache/firmware/target-volumes/os-mount'",
            "source = Path(os.environ['T610YP_SEED_OS_MOUNT'])")
        fixture = replace_once(fixture, IPAD_LAUNCHD_SHA256, digest(mount / "sbin/launchd"))
        adapted = scripts / "t610yp-iphone-prepare-owner-data-fixture.py"
        adapted.write_text(fixture)
        subprocess.run([sys.executable, str(adapted)],
                       env=dict(os.environ, T610YP_SEED_OS_MOUNT=str(mount)), check=True)
    finally:
        if subprocess.run(["hdiutil", "detach", device]).returncode:
            subprocess.run(["hdiutil", "detach", "-force", device], check=True)


def adapt_skip_setup(unixnu, target):
    scripts = unixnu / "scripts"
    skip = (scripts / "seed-data-skip-setup.py").read_text()
    if target != IPAD_TARGET:
        spec = TARGETS[target]
        skip = replace_once(skip, "BUILD, VERSION = '21H461', '17.7.11'",
                            f"BUILD, VERSION = '{spec['build']}', '{spec['version']}'")
    # The unpacked seed is sparse and can be larger than the runner's RAM.
    skip = replace_once(skip, "        if raw.read_bytes()[32:36] != b'NXSB':",
                        "        with raw.open('rb') as handle:\n"
                        "            header = handle.read(36)\n"
                        "        if header[32:36] != b'NXSB':")
    (scripts / "t610yp-seed-data-skip-setup.py").write_text(skip)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("extract")
    p.add_argument("ipsw", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--target", choices=TARGETS, default="iphone-17.7")
    p = sub.add_parser("prepare")
    p.add_argument("unixnu", type=Path)
    p.add_argument("image", type=Path)
    p.add_argument("--target", choices=TARGETS, default="iphone-17.7")
    p = sub.add_parser("adapt-skip-setup")
    p.add_argument("unixnu", type=Path)
    p.add_argument("--target", choices=[IPAD_TARGET, *TARGETS], required=True)
    args = parser.parse_args()
    if args.action == "extract":
        extract(args.ipsw, args.output, args.target)
    elif args.action == "prepare":
        prepare(args.unixnu.resolve(), args.image.resolve(), args.target)
    else:
        adapt_skip_setup(args.unixnu.resolve(), args.target)


if __name__ == "__main__":
    main()

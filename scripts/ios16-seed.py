#!/usr/bin/env python3
"""Prepare the iPhone X iOS 16.7.16 OS input for the UniXNU Data seed scripts."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import zipfile

IPSW_SHA256 = "ece1194f7e233e9109c5790a8d23a4564a10a254e15ec5b09978a343265568fb"
PRODUCTS = {"iPhone10,3", "iPhone10,6"}
VERSION = "16.7.16"
BUILD = "20H392"


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise SystemExit("Pinned seed script does not match expected input: " + old)
    return text.replace(old, new, 1)


def extract(ipsw, output):
    if digest(ipsw) != IPSW_SHA256:
        raise SystemExit("iPhone X IPSW SHA-256 does not match iOS 16.7.16 / 20H392")
    if output.exists():
        raise SystemExit("Refusing to overwrite " + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ipsw) as archive:
        manifest = plistlib.loads(archive.read("BuildManifest.plist"))
        supported = set(manifest.get("SupportedProductTypes", []))
        if (manifest.get("ProductVersion") != VERSION or
                manifest.get("ProductBuildVersion") != BUILD or
                not PRODUCTS.intersection(supported)):
            raise SystemExit("Unexpected IPSW product or version")
        matches = [
            item for item in manifest["BuildIdentities"]
            if item["Info"].get("RestoreBehavior") == "Erase"
            and item["Info"].get("DeviceClass", "").lower() == "d22ap"
        ]
        if not matches:
            raise SystemExit("Expected an iPhone X d22ap erase identity")
        member = matches[0]["Manifest"]["OS"]["Info"]["Path"]
        entry = archive.getinfo(member)
        partial = output.with_suffix(".partial")
        try:
            with archive.open(entry) as source, partial.open("xb") as target:
                shutil.copyfileobj(source, target, 8 * 1024 * 1024)
            if partial.stat().st_size != entry.file_size:
                raise SystemExit("Incomplete OS extraction")
            partial.rename(output)
        finally:
            partial.unlink(missing_ok=True)
    print(json.dumps({
        "products": sorted(PRODUCTS.intersection(supported)),
        "version": VERSION,
        "build": BUILD,
        "member": member,
        "sha256": digest(output),
    }, indent=2))


def adapt(unixnu):
    scripts = unixnu / "scripts"
    launchd = unixnu / ".cache/firmware/target-volumes/os-mount/sbin/launchd"
    if not launchd.is_file():
        raise SystemExit("Verified iOS 16 OS must be mounted before preparing the seed")
    launchd_sha256 = digest(launchd)

    fixture = (scripts / "prepare-owner-data-fixture.py").read_text()
    fixture = replace_once(
        fixture,
        "8cec9a3c44a443b08e977ccc5d577850316ad428c575dc01f09b018dbd5b6a25",
        launchd_sha256,
    )
    (scripts / "t610yp-ios16-prepare-owner-data-fixture.py").write_text(fixture)

    skip = (scripts / "seed-data-skip-setup.py").read_text()
    skip = replace_once(skip, "BUILD, VERSION = '21H461', '17.7.11'",
                        f"BUILD, VERSION = '{BUILD}', '{VERSION}'")
    (scripts / "t610yp-ios16-seed-data-skip-setup.py").write_text(skip)

    print(json.dumps({"launchd_sha256": launchd_sha256,
                      "fixture_script": "t610yp-ios16-prepare-owner-data-fixture.py",
                      "skip_script": "t610yp-ios16-seed-data-skip-setup.py"}, indent=2))


parser = argparse.ArgumentParser(description=__doc__)
sub = parser.add_subparsers(dest="action", required=True)
extract_parser = sub.add_parser("extract")
extract_parser.add_argument("ipsw", type=Path)
extract_parser.add_argument("output", type=Path)
adapt_parser = sub.add_parser("adapt")
adapt_parser.add_argument("unixnu", type=Path)
args = parser.parse_args()

if args.action == "extract":
    extract(args.ipsw, args.output)
else:
    adapt(args.unixnu.resolve())

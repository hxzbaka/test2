#!/usr/bin/env python3
"""Prepare iPhone 11 iOS 17.7 inputs for the pinned UniXNU seed scripts."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import zipfile

IPSW_SHA256 = "8a68c431f2f5cce0c53d20a42930b898bcef7236cbd3efa307bcdc487375c82a"
IPAD_LAUNCHD_SHA256 = "8cec9a3c44a443b08e977ccc5d577850316ad428c575dc01f09b018dbd5b6a25"


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


def extract(ipsw, output):
    if digest(ipsw) != IPSW_SHA256:
        raise SystemExit("iPhone IPSW SHA-256 does not match 17.7 / 21H16")
    if output.exists():
        raise SystemExit("Refusing to overwrite " + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ipsw) as archive:
        manifest = plistlib.loads(archive.read("BuildManifest.plist"))
        if (manifest.get("ProductVersion") != "17.7" or
                manifest.get("ProductBuildVersion") != "21H16" or
                "iPhone12,1" not in manifest.get("SupportedProductTypes", [])):
            raise SystemExit("Unexpected IPSW product or version")
        matches = [item for item in manifest["BuildIdentities"]
                   if item["Info"].get("DeviceClass", "").lower() == "n104ap"
                   and item["Info"].get("RestoreBehavior") == "Erase"]
        if len(matches) != 1:
            raise SystemExit("Expected one iPhone 11 erase identity")
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
    print(json.dumps({"product": "iPhone12,1", "version": "17.7", "build": "21H16",
                      "member": member, "sha256": digest(output)}, indent=2))


def adapt(unixnu):
    scripts = unixnu / "scripts"
    launchd = unixnu / ".cache/firmware/target-volumes/os-mount/sbin/launchd"
    if not launchd.is_file():
        raise SystemExit("Verified iPhone OS must be mounted before preparing the seed")
    fixture = (scripts / "prepare-owner-data-fixture.py").read_text()
    fixture = replace_once(fixture, IPAD_LAUNCHD_SHA256, digest(launchd))
    (scripts / "t610yp-iphone-prepare-owner-data-fixture.py").write_text(fixture)
    skip = (scripts / "seed-data-skip-setup.py").read_text()
    skip = replace_once(skip, "BUILD, VERSION = '21H461', '17.7.11'",
                        "BUILD, VERSION = '21H16', '17.7'")
    (scripts / "t610yp-iphone-seed-data-skip-setup.py").write_text(skip)


parser = argparse.ArgumentParser(description=__doc__)
sub = parser.add_subparsers(dest="action", required=True)
p = sub.add_parser("extract")
p.add_argument("ipsw", type=Path)
p.add_argument("output", type=Path)
p = sub.add_parser("adapt")
p.add_argument("unixnu", type=Path)
args = parser.parse_args()
if args.action == "extract":
    extract(args.ipsw, args.output)
else:
    adapt(args.unixnu.resolve())

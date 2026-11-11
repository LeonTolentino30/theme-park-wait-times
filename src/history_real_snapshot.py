"""One authorized, fixed-source local extraction. No upload or public serving."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from . import history_extraction as extraction, history_snapshots as snapshots

ROOT = Path(__file__).resolve().parents[1]
START = "2026-11-08T19:43:55Z"
END = "2026-11-11T00:00:00Z"
BUNDLE_NAME = "phase8k-20261108T194355Z-20261111T000000Z"


def collector_is_running():
    if os.name != "nt":
        raise extraction.ExtractionError("This fixed-source entry point requires its reviewed Windows environment")
    command = "$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'src[.]collection_pilot' }).Count"
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True, timeout=15, check=True)
    if result.stdout.strip() not in {"0", "1", "2", "3"}:
        raise extraction.ExtractionError("Cannot establish collector process state")
    return result.stdout.strip() != "0"


def fingerprint(path):
    return {suffix or "database": hashlib.sha256(Path(str(path)+suffix).read_bytes()).hexdigest() if Path(str(path)+suffix).exists() else None
            for suffix in ("", "-wal", "-shm", "-journal")}


def require_ignored(root, destination):
    relative = destination.relative_to(root).as_posix()
    ignored = subprocess.run(["git", "check-ignore", "--quiet", "--", relative], cwd=root).returncode == 0
    tracked = subprocess.check_output(["git", "ls-files", "--", relative], cwd=root)
    if not ignored or tracked.strip():
        raise extraction.ExtractionError("Private output must be Git-ignored and untracked")


def restrict_directory(path):
    if os.name == "nt":
        # Use and verify a complete protected DACL, rather than combining icacls actions.
        literal = str(Path(path).resolve()).replace("'", "''")
        command = "\n".join([
            "$ErrorActionPreference='Stop'",
            "$path='" + literal + "'",
            "$owner=[System.Security.Principal.WindowsIdentity]::GetCurrent().User",
            "$system=[System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')",
            "$security=[System.Security.AccessControl.DirectorySecurity]::new()",
            "$security.SetAccessRuleProtection($true,$false)",
            "foreach($sid in @($owner,$system)) { $security.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($sid,[System.Security.AccessControl.FileSystemRights]::FullControl,[System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit',[System.Security.AccessControl.PropagationFlags]::None,[System.Security.AccessControl.AccessControlType]::Allow)) }",
            "Set-Acl -LiteralPath $path -AclObject $security",
            "$acl=Get-Acl -LiteralPath $path",
            "$rules=@($acl.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier]))",
            "$other=@($rules | Where-Object { $_.IdentityReference.Value -notin @($owner.Value,$system.Value) })",
            "if(-not $acl.AreAccessRulesProtected -or $other.Count -ne 0 -or $rules.Count -ne 2) { throw 'Private ACL verification failed' }",
        ])
        encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], capture_output=True, check=True, timeout=15)
    else:
        path.chmod(0o700)


class PrivateLocalStore:
    """Read only fixed manifest/content-addressed objects, never arbitrary files."""
    def __init__(self, directory):
        self.directory = Path(directory).resolve()

    def get(self, key):
        if key != "manifest.json" and not re.fullmatch(r"history/v2/[0-9a-f]{64}\.json", key):
            raise snapshots.SnapshotError("Invalid private snapshot key")
        path = self.directory/key
        if any(part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()) for part in (self.directory, path, *path.parents) if part != part.parent):
            raise snapshots.SnapshotError("Private snapshot links are not supported")
        with path.open("rb") as stream:
            raw = stream.read(snapshots.MAX_BYTES+1)
        if len(raw) > snapshots.MAX_BYTES:
            raise snapshots.SnapshotError("Private snapshot too large")
        return raw


def generate(scope, *, root=ROOT):
    if type(scope) is not extraction.ExtractionScope or snapshots.utc(scope.start) != snapshots.utc(START) or snapshots.utc(scope.end) != snapshots.utc(END):
        raise extraction.ExtractionError("Only the explicitly authorized Phase 8K interval is enabled")
    root = Path(root).resolve()
    database = root/"private/themeparks/pilot/pilot.sqlite3"
    destination = root/"private/themeparks/snapshots"/BUNDLE_NAME
    if destination.exists():
        raise FileExistsError("Private snapshot destination already exists; refusing another extraction")
    for path in (database, destination, *database.parents, *destination.parents):
        if path == root.parent:
            continue
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise extraction.ExtractionError("Linked source/output paths are not supported")
    require_ignored(root, destination)
    if not database.is_file() or any(Path(str(database)+suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise extraction.ExtractionError("This non-mutating entry point requires a closed, checkpointed database without sidecars")
    if collector_is_running():
        raise extraction.ExtractionError("Collector is active; refusing immutable access or process changes")
    before = fingerprint(database)
    started = time.monotonic()
    rows = extraction._read_rows(database, scope, immutable=True)
    read_seconds = time.monotonic()-started
    # Connection has closed here. Never checkpoint/delete/alter any source file.
    after = fingerprint(database)
    if before != after or collector_is_running():
        raise extraction.ExtractionError("Source changed during extraction; no snapshot written")
    data = extraction._build_snapshot(rows, scope, allow_real=True)
    store = snapshots.package(data, allow_real=True)
    snapshots.load(store, allow_real=True)
    manifest = snapshots.decode(store.get("manifest.json"))
    report = extraction.quality_summary(data, allow_real=True)
    report.update(requested_start=scope.start, requested_end_exclusive=scope.end,
                  sha256=manifest["sha256"], bytes=manifest["bytes"],
                  sqlite_read_seconds=round(read_seconds, 6), source_fingerprints=before,
                  source_unchanged=True, validation="schema, mappings, exact timestamps, duplicates, canonical bytes, SHA-256 and size passed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(exist_ok=False)
    restrict_directory(destination) # no real records are written until ACL succeeds
    target = destination/manifest["key"]
    target.parent.mkdir(parents=True)
    with target.open("xb") as stream:
        stream.write(store.get(manifest["key"]))
    with (destination/"aggregate_report.json").open("xb") as stream:
        stream.write(snapshots.canonical(report))
    with (destination/"manifest.json").open("xb") as stream:
        stream.write(store.get("manifest.json"))
    assert snapshots.load(PrivateLocalStore(destination), allow_real=True) == data
    if fingerprint(database) != before:
        raise extraction.ExtractionError("Source changed after extraction; review private output before use")
    return destination, report


def main():
    parser = argparse.ArgumentParser(description="Authorized fixed-source, local-only historical snapshot")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--attraction-id", action="append", required=True)
    parser.add_argument("--generate-local-snapshot", action="store_true", required=True)
    args = parser.parse_args()
    scope = extraction.ExtractionScope(tuple(args.attraction_id), args.start, args.end)
    path, report = generate(scope)
    print(json.dumps({"private_bundle": str(path), "aggregate_report": report}, indent=2))


if __name__ == "__main__":
    main()

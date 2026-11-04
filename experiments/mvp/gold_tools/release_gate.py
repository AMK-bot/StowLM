"""Cheap fail-closed gate for the leader-authorized 500/800/1700 small release.

This checks frozen bytes and booking settings, without constructing ships,
loading solver code, or claiming checker approval of any loading plan.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

SUPPORTED_SHIP_SETTINGS = {
    "feeder_500": {"booking_slack": 0.25, "booking_mass_fraction": 0.55},
    "feeder_800": {"booking_slack": 0.25, "booking_mass_fraction": 0.45},
    "feeder_1700": {"booking_slack": 0.35, "booking_mass_fraction": 0.45},
}


def release_ready(corpus_root) -> dict:
    """Return ``{ready, reason, ...}``; incomplete/concurrent writes stay closed."""
    root = Path(corpus_root).resolve()

    def require(condition, reason):
        if not condition:
            raise ValueError(reason)

    def path_for(name):
        path = (root / name).resolve()
        require(path.is_relative_to(root) and path.is_file(), f"missing or unsafe file: {name}")
        return path

    def read(name):
        return json.loads(path_for(name).read_text())

    def pin(name, digest):
        require(isinstance(digest, str) and len(digest) == 64, f"missing checksum: {name}")
        require(hashlib.sha256(path_for(name).read_bytes()).hexdigest() == digest,
                f"checksum mismatch: {name}")

    try:
        registry = read("releases.json")
        require(registry.get("format") == "stowlm-mvp-releases-v1", "unknown release registry format")
        manifest = read("manifest.json")
        require(manifest.get("format") == "stowlm-mvp-corpus-v2", "v2 frozen manifest required")
        frozen_release = manifest["release"]
        require(frozen_release.get("status") in {"partial_qualified", "complete"}, "frozen release not qualified")
        settings = SUPPORTED_SHIP_SETTINGS
        for release in (registry, frozen_release):
            require(all(release.get("ship_settings", {}).get(ship) == expected
                        for ship, expected in settings.items()),
                    "ship settings differ from leader-qualified 500/800/1700 settings")
        require(manifest.get("rule_freeze_v1_enabled") is False, "manifest lacks switch-OFF evidence")
        hashes = manifest["hashes"]
        # Other ships may amend the live registry and metadata after this freeze.
        # Verify our authorized memberships below, never global metadata bytes.
        requested = read("test.requested.json")["specs"]
        showcases = read("showcase/showcase-manifest.json")["records"]
        expected_test, expected_showcase = [], []

        rows = manifest["files"]
        by_path = {row["path"]: row for row in rows}
        require(len(by_path) == len(rows), "duplicate frozen path")
        coverage = set()
        active_heldout = 0
        for row in rows:
            if (row.get("split") not in {"test", "showcase"}
                    or row.get("active_for_current_release") is not True
                    or row.get("rung") != "small" or row.get("ship") not in settings):
                continue
            active_heldout += 1
            for name, digest in ((row["path"], row["sha256"]), (row["audit_path"], row["audit_sha256"])):
                require(hashes.get(name) == digest, f"row differs from frozen hash: {name}")
                pin(name, digest)
            if row.get("rung") in {"small", "medium"}:
                ship = row.get("ship")
                require(row.get("rung") == "small" and ship in settings, "unqualified active heldout ship/rung")
                audit = read(row["audit_path"])
                config = audit["config"]
                require(audit.get("instance_sha256") == row["sha256"], "audit instance hash mismatch")
                require(config.get("vessel") == ship and config.get("rung", config.get("size")) == "small",
                        "audit ship/rung mismatch")
                require(all(row.get(key) == value and config.get(key) == value
                            for key, value in settings[ship].items()), "heldout booking settings mismatch")
                coverage.add((row["split"], ship))
                if row["split"] == "test":
                    expected_test.append(config)
                else:
                    expected_showcase.append((config, row["sha256"]))
        require(coverage == {(split, ship) for split in ("test", "showcase") for ship in settings},
                "qualified small test/showcase coverage incomplete")
        def authorized(config):
            return (config.get("vessel") in settings
                    and config.get("rung", config.get("size")) == "small")

        def canonical(value):
            return json.dumps(value, sort_keys=True)

        require(sorted(map(canonical, expected_test)) == sorted(canonical(c) for c in requested if authorized(c)),
                "authorized test requested membership/config mismatch")
        actual_showcase = [(r["config"], r["instance_sha256"]) for r in showcases if authorized(r["config"])]
        require(sorted(map(canonical, expected_showcase)) == sorted(map(canonical, actual_showcase)),
                "authorized showcase membership/config mismatch")
        pins = frozen_release.get("preserved_v1", [])
        live_pins = {p["path"]: p for p in registry.get("preserved_v1", [])}
        require(any(p.get("split") == "test" and p.get("rung") == "tiny" for p in pins)
                and any(p.get("split") == "showcase" and p.get("rung") == "full" for p in pins),
                "preserved tiny/full pins absent")
        for preserved in pins:
            require(live_pins.get(preserved["path"]) == preserved, "live preserved pin differs from frozen release")
            row = by_path.get(preserved["path"])
            require(row is not None and row.get("active_for_current_release") is True,
                    "preserved pin absent from active frozen rows")
            require((row.get("split"), row.get("rung")) == (preserved["split"], preserved["rung"]),
                    "preserved pin split/rung mismatch")
            for key in ("path", "sha256", "audit_path", "audit_sha256"):
                require(row.get(key) == preserved[key], "preserved pin differs from frozen row")
            pin(preserved["path"], preserved["sha256"])
            pin(preserved["audit_path"], preserved["audit_sha256"])
        return dict(ready=True, reason="qualified 500/800/1700 heldout bytes frozen", release=frozen_release.get("current_release"),
                    current_registry_release=registry.get("current_release"), status=frozen_release["status"], ship_settings=settings, qualified_ships=sorted(settings),
                    active_heldout=active_heldout,
                    manifest_sha256=hashlib.sha256(path_for("manifest.json").read_bytes()).hexdigest())
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return dict(ready=False, reason=str(error))

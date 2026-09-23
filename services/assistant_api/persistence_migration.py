"""Offline export/validation contract for a future SQLite → Supabase migration."""

from __future__ import annotations

from typing import Any

from .persistence import ENTITIES, PersistenceProvider, content_hash


def export_bundle(source: PersistenceProvider) -> dict[str, Any]:
    entities = {entity: source.items(entity) for entity in ENTITIES}
    checksums = {entity: content_hash({"records": rows}) for entity, rows in entities.items()}
    manifest = {"schema_version": 1, "records_source": sum(map(len, entities.values())),
                "records_target": 0, "success": 0, "failed": 0,
                "checksum": content_hash(checksums), "entity_checksums": checksums,
                "migration_status": "export_only_not_imported"}
    return {"manifest": manifest, "entities": entities}


def validate_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    entities = bundle.get("entities")
    manifest = bundle.get("manifest")
    if not isinstance(entities, dict) or not isinstance(manifest, dict) or set(entities) != set(ENTITIES):
        raise ValueError("invalid_migration_bundle")
    checksums = {entity: content_hash({"records": rows}) for entity, rows in entities.items()}
    if checksums != manifest.get("entity_checksums") or content_hash(checksums) != manifest.get("checksum"):
        raise ValueError("migration_checksum_mismatch")
    if sum(map(len, entities.values())) != manifest.get("records_source"):
        raise ValueError("migration_count_mismatch")
    return manifest


def reconcile(source: PersistenceProvider, target: PersistenceProvider) -> dict[str, Any]:
    left = export_bundle(source)["manifest"]
    right = export_bundle(target)["manifest"]
    return {"records_source": left["records_source"], "records_target": right["records_source"],
            "success": int(left["checksum"] == right["checksum"]),
            "failed": int(left["checksum"] != right["checksum"]),
            "checksum": left["checksum"], "target_checksum": right["checksum"]}


def import_bundle(target: PersistenceProvider, bundle: dict[str, Any], *,
                  dry_run: bool = True, confirm: bool = False) -> dict[str, Any]:
    source_manifest = validate_bundle(bundle)
    if not dry_run and not confirm:
        raise ValueError("migration_import_requires_confirmation")
    result = {"records_source": source_manifest["records_source"], "records_target": 0,
              "success": 0, "failed": 0, "checksum": source_manifest["checksum"],
              "dry_run": dry_run}
    if dry_run:
        return result
    for entity in ENTITIES:
        for row in bundle["entities"][entity]:
            try:
                target.put(entity, row["key"], row["payload"])
                result["success"] += 1
            except (ValueError, OSError, NotImplementedError):
                result["failed"] += 1
    result["records_target"] = sum(len(target.items(entity)) for entity in ENTITIES)
    result["reconciled"] = result["failed"] == 0 and export_bundle(target)["manifest"]["checksum"] == result["checksum"]
    return result

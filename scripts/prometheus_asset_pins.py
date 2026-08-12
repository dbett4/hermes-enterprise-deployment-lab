"""Repository-pinned Prometheus release asset digests for native telemetry proof.

Linux/amd64 and Linux/arm64 digests for Prometheus 3.13.2 are pinned in-repo.
`scripts/telemetry-proof.sh` requires actual == repository_pin == upstream
sha256sums.txt before extraction. This is not TOFU against the download channel
alone.
"""

from __future__ import annotations

from pathlib import Path

PROMETHEUS_VERSION = "3.13.2"

PINNED_SHA256_BY_ARCH: dict[str, str] = {
    "amd64": "0e8c4d46101bd025ea8265e377d2caabc57f488fc1be1c367f37db69ea41be6f",
    "arm64": "7cecb17a6f41d59814e1a0581a1f81f79051ad5973d1ecf39e23a9f747d6572a",
}


def asset_arch_from_uname(machine: str) -> str:
    normalized = machine.strip().lower()
    if normalized in {"x86_64", "amd64"}:
        return "amd64"
    if normalized in {"aarch64", "arm64"}:
        return "arm64"
    raise ValueError(f"unsupported Prometheus proof architecture: {machine}")


def repository_pin_for_arch(arch: str) -> str:
    try:
        return PINNED_SHA256_BY_ARCH[arch]
    except KeyError as exc:
        raise ValueError(f"unsupported Prometheus proof architecture: {arch}") from exc


def digest_from_manifest(manifest_text: str, asset_name: str) -> str:
    for line in manifest_text.splitlines():
        if not line.strip():
            continue
        digest, name = line.split(None, 1)
        if name.lstrip("*") == asset_name:
            return digest
    raise ValueError(f"asset missing from checksum manifest: {asset_name}")


def assert_three_way_equality(
    *,
    actual: str,
    repository_pin: str,
    upstream_manifest: str,
) -> None:
    if not (actual == repository_pin == upstream_manifest):
        raise SystemExit(
            "TELEMETRY_PROOF_FAIL: Prometheus asset digest mismatch "
            f"actual={actual} repository_pin={repository_pin} "
            f"upstream_manifest={upstream_manifest}"
        )


def verify_asset_digests(
    *,
    asset_path: Path,
    checksum_manifest_path: Path,
    asset_name: str,
    arch: str,
) -> str:
    import hashlib

    actual = hashlib.sha256(asset_path.read_bytes()).hexdigest()
    repository_pin = repository_pin_for_arch(arch)
    upstream = digest_from_manifest(
        checksum_manifest_path.read_text(encoding="utf-8"),
        asset_name,
    )
    assert_three_way_equality(
        actual=actual,
        repository_pin=repository_pin,
        upstream_manifest=upstream,
    )
    return actual


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        raise SystemExit(
            "usage: prometheus_asset_pins.py arch <uname-m> | "
            "verify <asset-path> <checksum-path> <asset-name> <arch>"
        )
    command = sys.argv[1]
    if command == "arch":
        print(asset_arch_from_uname(sys.argv[2]))
    elif command == "pin":
        print(repository_pin_for_arch(sys.argv[2]))
    elif command == "verify":
        asset_path = Path(sys.argv[2])
        checksum_path = Path(sys.argv[3])
        asset_name = sys.argv[4]
        arch = sys.argv[5]
        print(
            verify_asset_digests(
                asset_path=asset_path,
                checksum_manifest_path=checksum_path,
                asset_name=asset_name,
                arch=arch,
            )
        )
    else:
        raise SystemExit(f"unknown command: {command}")

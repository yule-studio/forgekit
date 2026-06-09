from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import ssl
from typing import Optional


@dataclass(frozen=True)
class TLSCABundle:
    cafile: Optional[str]
    source: str
    detail: str
    exists: bool


def resolve_ca_bundle() -> TLSCABundle:
    explicit_cafile = _normalize_path(os.getenv("SSL_CERT_FILE"))
    if explicit_cafile is not None:
        if explicit_cafile.exists():
            return TLSCABundle(
                cafile=str(explicit_cafile),
                source="env",
                detail="using SSL_CERT_FILE",
                exists=True,
            )
        return TLSCABundle(
            cafile=str(explicit_cafile),
            source="env-missing",
            detail="SSL_CERT_FILE points to a missing file",
            exists=False,
        )

    default_paths = ssl.get_default_verify_paths()
    default_cafile = _normalize_path(default_paths.cafile)
    if default_cafile is not None and default_cafile.exists():
        return TLSCABundle(
            cafile=str(default_cafile),
            source="default",
            detail="using OpenSSL default cafile",
            exists=True,
        )

    certifi_cafile = _load_certifi_cafile()
    if certifi_cafile is not None and certifi_cafile.exists():
        missing_detail = default_paths.cafile or "unset"
        return TLSCABundle(
            cafile=str(certifi_cafile),
            source="certifi",
            detail=f"default cafile is unavailable ({missing_detail}); certifi fallback is available",
            exists=True,
        )

    missing_detail = default_paths.cafile or "unset"
    return TLSCABundle(
        cafile=None,
        source="missing",
        detail=f"no CA bundle is available (default cafile: {missing_detail})",
        exists=False,
    )


def apply_ca_bundle_fallback() -> TLSCABundle:
    bundle = resolve_ca_bundle()
    if bundle.source == "certifi" and bundle.cafile is not None and "SSL_CERT_FILE" not in os.environ:
        os.environ["SSL_CERT_FILE"] = bundle.cafile
        return TLSCABundle(
            cafile=bundle.cafile,
            source="certifi-applied",
            detail="using certifi CA bundle because the default OpenSSL cafile is unavailable",
            exists=True,
        )
    return bundle


def _load_certifi_cafile() -> Optional[Path]:
    try:
        import certifi
    except ImportError:
        return None

    return _normalize_path(certifi.where())


def _normalize_path(value: Optional[str]) -> Optional[Path]:
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    return Path(normalized).expanduser()

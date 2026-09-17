"""flow — config-driven geodata ETL on DuckDB, output to GeoPackage."""
import os as _os


def _configure_gdal_ca_bundle() -> None:
    # GDAL's bundled libcurl/OpenSSL stack is independent of Python's requests/certifi
    # stack and has no CA bundle configured by default on Windows, which makes ST_Read
    # fail with "unable to get local issuer certificate" on WFS/GML sources that
    # reference their XSD schema over HTTPS even though the initial fetch succeeded.
    bundle = (
        _os.environ.get("GDAL_CACERT")
        or _os.environ.get("CURL_CA_BUNDLE")
        or _os.environ.get("SSL_CERT_FILE")
        or _os.environ.get("REQUESTS_CA_BUNDLE")
    )
    if not bundle:
        import certifi
        bundle = certifi.where()
    _os.environ.setdefault("GDAL_CACERT", bundle)
    _os.environ.setdefault("CURL_CA_BUNDLE", bundle)


_configure_gdal_ca_bundle()

from .config import Pipeline, load_pipeline, load_pipeline_dict, dump_pipeline_yaml
from .engine import run_pipeline, Engine, preview_pipeline

__all__ = [
    "Pipeline",
    "load_pipeline",
    "load_pipeline_dict",
    "dump_pipeline_yaml",
    "run_pipeline",
    "preview_pipeline",
    "Engine",
]

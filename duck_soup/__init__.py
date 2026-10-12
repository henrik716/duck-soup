"""flow — config-driven geodata ETL on DuckDB, output to GeoPackage or GeoParquet."""
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

from .config import Config, dump_config_yaml, load_config, load_config_dict
from .engine import Engine, preview_config_pipeline, run_config

__all__ = [
    "Config",
    "load_config",
    "load_config_dict",
    "dump_config_yaml",
    "run_config",
    "preview_config_pipeline",
    "Engine",
]

"""Auto-download and cache the H2 JDBC driver."""

from __future__ import annotations

import urllib.request
from pathlib import Path

H2_VERSION = "2.3.232"
H2_URL = f"https://repo1.maven.org/maven2/com/h2database/h2/{H2_VERSION}/h2-{H2_VERSION}.jar"

CACHE_DIR = Path.home() / ".cache" / "neo4j-nano"


def get_jdbc_jar() -> Path:
    """Get path to H2 JDBC jar, downloading if needed."""
    jar_path = CACHE_DIR / f"h2-{H2_VERSION}.jar"

    if jar_path.exists():
        return jar_path

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Downloading H2 database ({H2_VERSION}, ~2.5 MB)...")
    tmp_path = jar_path.with_suffix(".tmp")
    urllib.request.urlretrieve(H2_URL, tmp_path)
    tmp_path.rename(jar_path)
    print(f"  Cached at: {jar_path}")

    return jar_path

"""Download and cache Neo4j Enterprise jars for embedded use."""

from __future__ import annotations

import os
import subprocess
import tarfile
from pathlib import Path

NEO4J_VERSION = "2026.05.0"
NEO4J_TAR_URL = f"https://dist.neo4j.org/neo4j-enterprise-{NEO4J_VERSION}-unix.tar.gz"
CACHE_DIR = Path(os.environ.get("NEO4J_NANO_CACHE", Path.home() / ".cache" / "neo4j-nano"))

# Minimal jar set for embedded Neo4j + Virtual Graphs with SQLite backend.
# Determined via JVM -Xlog:class+load tracing with full Enterprise + VG workload.
# 475 → 203 jars (57% reduction).
REQUIRED_JARS = frozenset({
    "HikariCP-7.0.2.jar",
    "annotations-2026.05.0.jar",
    "antlr4-runtime-4.13.2.jar",
    "arrow-memory-core-19.0.0.jar",
    "auth-2.42.32.jar",
    "azure-core-management-1.19.3.jar",
    "caffeine-3.2.3.jar",
    "cdc-2026.05.0.jar",
    "commons-lang3-3.20.0.jar",
    "commons-text-1.15.0.jar",
    "cypher-antlr-ast-common-2026.05.0.jar",
    "cypher-antlr-common-2026.05.0.jar",
    "cypher-parser-ast-common-2026.05.0.jar",
    "cypher-parser-common-2026.05.0.jar",
    "cypher-parser-factory-2026.05.0.jar",
    "cypher-preparser-2026.05.0.jar",
    "cypher-v25-antlr-parser-2026.05.0.jar",
    "cypher-v5-antlr-parser-2026.05.0.jar",
    "cypher-v5-ast-factory-2026.05.0.jar",
    "cypher-v5-parser-listener-2026.05.0.jar",
    "datasource-proxy-1.11.0.jar",
    "eclipse-collections-11.1.0.jar",
    "eclipse-collections-api-11.1.0.jar",
    "gds-write-service-2026.05.0.jar",
    "google-cloud-storage-2.64.1.jar",
    "http-client-spi-2.42.32.jar",
    "identity-spi-2.42.32.jar",
    "ipaddress-5.6.2.jar",
    "jackson-annotations-2.21.jar",
    "jackson-core-2.21.2.jar",
    "jackson-databind-2.21.2.jar",
    "jackson-datatype-jsr310-2.18.4.jar",
    "java-jwt-4.5.1.jar",
    "jctools-core-4.0.6.jar",
    "jetty-http-12.1.8.jar",
    "jetty-servlet-api-4.0.9.jar",
    "jna-5.18.1.jar",
    "jspecify-1.0.0.jar",
    "lighthouse-assembler-2026.05.0.jar",
    "lighthouse-common-2026.05.0.jar",
    "lighthouse-crdt-library-2026.05.0.jar",
    "lighthouse-gossip-protocol-2026.05.0.jar",
    "log4j-api-2.25.4.jar",
    "log4j-core-2.25.4.jar",
    "log4j-layout-template-json-2.25.4.jar",
    "lucene-core-10.4.0.jar",
    "lucene9-shaded-2026.05.0.jar",
    "lz4-java-1.11.0.jar",
    "magnolia_3-1.3.0.jar",
    "metrics-core-4.2.38.jar",
    "metrics-jmx-4.2.38.jar",
    "neo4j-2026.05.0.jar",
    "neo4j-arrow-2026.05.0.jar",
    "neo4j-ast-2026.05.0.jar",
    "neo4j-auth-plugin-api-2026.05.0.jar",
    "neo4j-backup-2026.05.0.jar",
    "neo4j-block-storage-engine-2026.05.0.jar",
    "neo4j-bolt-2026.05.0.jar",
    "neo4j-bolt-connection-11.0.1.jar",
    "neo4j-bolt-connection-netty-11.0.1.jar",
    "neo4j-bolt-connection-routed-11.0.1.jar",
    "neo4j-bolt-messages-2026.05.0.jar",
    "neo4j-capabilities-2026.05.0.jar",
    "neo4j-causal-clustering-2026.05.0.jar",
    "neo4j-cloud-2026.05.0.jar",
    "neo4j-cloud-storage-azb-2026.05.0.jar",
    "neo4j-cloud-storage-gs-2026.05.0.jar",
    "neo4j-cloud-storage-s3-2026.05.0.jar",
    "neo4j-cluster-common-2026.05.0.jar",
    "neo4j-codegen-2026.05.0.jar",
    "neo4j-collections-2026.05.0.jar",
    "neo4j-command-line-2026.05.0.jar",
    "neo4j-common-2026.05.0.jar",
    "neo4j-concurrent-2026.05.0.jar",
    "neo4j-configuration-2026.05.0.jar",
    "neo4j-csv-2026.05.0.jar",
    "neo4j-cypher-2026.05.0.jar",
    "neo4j-cypher-cache-2026.05.0.jar",
    "neo4j-cypher-compiled-expressions-2026.05.0.jar",
    "neo4j-cypher-config-2026.05.0.jar",
    "neo4j-cypher-expression-evaluator-2026.05.0.jar",
    "neo4j-cypher-interpreted-runtime-2026.05.0.jar",
    "neo4j-cypher-ir-2026.05.0.jar",
    "neo4j-cypher-logical-plans-2026.05.0.jar",
    "neo4j-cypher-physical-planning-2026.05.0.jar",
    "neo4j-cypher-pipelined-runtime-2026.05.0.jar",
    "neo4j-cypher-planner-2026.05.0.jar",
    "neo4j-cypher-planner-spi-2026.05.0.jar",
    "neo4j-cypher-runtime-util-2026.05.0.jar",
    "neo4j-cypher-slotted-runtime-2026.05.0.jar",
    "neo4j-data-collector-2026.05.0.jar",
    "neo4j-dbms-2026.05.0.jar",
    "neo4j-dbms-api-2026.05.0.jar",
    "neo4j-dbms-enterprise-2026.05.0.jar",
    "neo4j-diagnostics-2026.05.0.jar",
    "neo4j-discovery-2026.05.0.jar",
    "neo4j-discovery-lighthouse-2026.05.0.jar",
    "neo4j-enterprise-2026.05.0.jar",
    "neo4j-enterprise-cypher-2026.05.0.jar",
    "neo4j-enterprise-fabric-2026.05.0.jar",
    "neo4j-enterprise-kernel-2026.05.0.jar",
    "neo4j-enterprise-procedure-2026.05.0.jar",
    "neo4j-enterprise-query-router-2026.05.0.jar",
    "neo4j-enterprise-record-storage-engine-2026.05.0.jar",
    "neo4j-exceptions-2026.05.0.jar",
    "neo4j-expressions-2026.05.0.jar",
    "neo4j-fabric-2026.05.0.jar",
    "neo4j-fleet-management-2026.05.0.jar",
    "neo4j-front-end-2026.05.0.jar",
    "neo4j-gql-status-2026.05.0.jar",
    "neo4j-graph-engine-capabilities-2026.05.0.jar",
    "neo4j-graph-engine-configuration-2026.05.0.jar",
    "neo4j-graph-engine-data-source-providers-2026.05.0.jar",
    "neo4j-graph-engine-exceptions-2026.05.0.jar",
    "neo4j-graph-engine-mapping-2026.05.0.jar",
    "neo4j-graph-engine-planner-2026.05.0.jar",
    "neo4j-graph-engine-procedures-2026.05.0.jar",
    "neo4j-graph-engine-relational-2026.05.0.jar",
    "neo4j-graph-engine-runtime-2026.05.0.jar",
    "neo4j-graph-engine-schema-2026.05.0.jar",
    "neo4j-graph-engine-spi-2026.05.0.jar",
    "neo4j-graph-engine-sql-2026.05.0.jar",
    "neo4j-graph-engine-values-2026.05.0.jar",
    "neo4j-graph-engine-virtual-ids-2026.05.0.jar",
    "neo4j-graphdb-api-2026.05.0.jar",
    "neo4j-id-generator-2026.05.0.jar",
    "neo4j-import-api-2026.05.0.jar",
    "neo4j-import-util-2026.05.0.jar",
    "neo4j-index-2026.05.0.jar",
    "neo4j-internal-notifications-2026.05.0.jar",
    "neo4j-io-2026.05.0.jar",
    "neo4j-java-driver-6.0.5.jar",
    "neo4j-kernel-2026.05.0.jar",
    "neo4j-kernel-api-2026.05.0.jar",
    "neo4j-layout-2026.05.0.jar",
    "neo4j-lock-2026.05.0.jar",
    "neo4j-logging-2026.05.0.jar",
    "neo4j-lucene-index-2026.05.0.jar",
    "neo4j-metrics-2026.05.0.jar",
    "neo4j-monitoring-2026.05.0.jar",
    "neo4j-native-2026.05.0.jar",
    "neo4j-notifications-2026.05.0.jar",
    "neo4j-operator-2026.05.0.jar",
    "neo4j-procedure-2026.05.0.jar",
    "neo4j-procedure-api-2026.05.0.jar",
    "neo4j-protocol-catchup-2026.05.0.jar",
    "neo4j-protocol-catchup-base-2026.05.0.jar",
    "neo4j-protocol-consensus-2026.05.0.jar",
    "neo4j-protocol-dbms-2026.05.0.jar",
    "neo4j-protocol-lighthouse-2026.05.0.jar",
    "neo4j-protocol-raft-2026.05.0.jar",
    "neo4j-protocol-seed-syncing-2026.05.0.jar",
    "neo4j-protocol-spd-2026.05.0.jar",
    "neo4j-query-logging-2026.05.0.jar",
    "neo4j-query-router-2026.05.0.jar",
    "neo4j-raft-2026.05.0.jar",
    "neo4j-raft-common-2026.05.0.jar",
    "neo4j-record-storage-engine-2026.05.0.jar",
    "neo4j-resource-2026.05.0.jar",
    "neo4j-rewriting-2026.05.0.jar",
    "neo4j-schema-2026.05.0.jar",
    "neo4j-security-2026.05.0.jar",
    "neo4j-security-enterprise-2026.05.0.jar",
    "neo4j-seed-providers-2026.05.0.jar",
    "neo4j-server-2026.05.0.jar",
    "neo4j-server-enterprise-2026.05.0.jar",
    "neo4j-sharded-property-database-2026.05.0.jar",
    "neo4j-sharded-property-database-api-2026.05.0.jar",
    "neo4j-slf4j-provider-2026.05.0.jar",
    "neo4j-spatial-index-2026.05.0.jar",
    "neo4j-ssl-2026.05.0.jar",
    "neo4j-storage-engine-util-2026.05.0.jar",
    "neo4j-store-management-2026.05.0.jar",
    "neo4j-token-api-2026.05.0.jar",
    "neo4j-udc-2026.05.0.jar",
    "neo4j-unsafe-2026.05.0.jar",
    "neo4j-util-2026.05.0.jar",
    "neo4j-values-2026.05.0.jar",
    "neo4j-virtual-database-2026.05.0.jar",
    "neo4j-wal-2026.05.0.jar",
    "netty-buffer-4.2.14.Final.jar",
    "netty-codec-base-4.2.14.Final.jar",
    "netty-codec-compression-4.2.14.Final.jar",
    "netty-common-4.2.14.Final.jar",
    "netty-handler-4.2.14.Final.jar",
    "netty-transport-4.2.14.Final.jar",
    "netty-transport-classes-epoll-4.2.14.Final.jar",
    "netty-transport-classes-io_uring-4.2.14.Final.jar",
    "netty-transport-classes-kqueue-4.2.14.Final.jar",
    "netty-transport-native-unix-common-4.2.14.Final.jar",
    "reactive-streams-1.0.4.jar",
    "scala-library-2.13.17.jar",
    "scala3-library_3-3.7.4.jar",
    "sdk-core-2.42.32.jar",
    "server-api-2026.05.0.jar",
    "shiro-cache-2.1.0.jar",
    "shiro-core-2.1.0.jar",
    "shiro-crypto-core-2.1.0.jar",
    "shiro-crypto-hash-2.1.0.jar",
    "shiro-lang-2.1.0.jar",
    "slf4j-api-2.0.17.jar",
    "sourcecode_3-0.4.2.jar",
    "zstd-jni-1.5.7-7.jar",
})


def get_neo4j_lib_dir() -> Path:
    """
    Return path to the Neo4j lib/ directory containing the minimal jar set.
    Downloads the Neo4j EE tarball and extracts only required jars if not cached.
    """
    lib_dir = CACHE_DIR / f"neo4j-enterprise-{NEO4J_VERSION}" / "lib"
    if lib_dir.exists() and any(lib_dir.glob("*.jar")):
        return lib_dir

    print(f"Running embedded Graph Engine for the first time. Download required.")
    print(f"Downloading Neo4j Enterprise {NEO4J_VERSION} (~366 MB download)...")
    print(f"  From: {NEO4J_TAR_URL}")
    print(f"  Cache: {CACHE_DIR}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Download using curl (handles redirects, shows progress)
    tar_path = CACHE_DIR / f"neo4j-enterprise-{NEO4J_VERSION}-unix.tar.gz"
    if not tar_path.exists():
        tmp_path = tar_path.with_suffix(".tar.gz.tmp")
        result = subprocess.run(
            ["curl", "-L", "-#", "-o", str(tmp_path), NEO4J_TAR_URL],
            check=False,
        )
        if result.returncode != 0 or not tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
            raise RuntimeError(f"Failed to download Neo4j from {NEO4J_TAR_URL}")
        tmp_path.rename(tar_path)
        print(f"  Downloaded: {tar_path.stat().st_size / 1024 / 1024:.0f} MB")

    # Extract only the required jars from lib/
    extract_dir = CACHE_DIR / f"neo4j-enterprise-{NEO4J_VERSION}"
    if not extract_dir.exists():
        print("  Extracting minimal jar set...")
        extract_dir.mkdir(parents=True)
        lib_dir.mkdir(parents=True, exist_ok=True)

        with tarfile.open(tar_path, "r:gz") as tf:
            for member in tf.getmembers():
                parts = member.name.split("/", 1)
                if len(parts) == 2 and parts[1].startswith("lib/") and member.isfile():
                    jar_name = parts[1].removeprefix("lib/")
                    if jar_name in REQUIRED_JARS:
                        member.name = f"lib/{jar_name}"
                        tf.extract(member, path=extract_dir, filter="data")

    if not lib_dir.exists() or not any(lib_dir.glob("*.jar")):
        raise RuntimeError(f"Neo4j lib dir not found at {lib_dir}")

    # Remove tarball to save disk space
    tar_path.unlink(missing_ok=True)

    jar_count = sum(1 for _ in lib_dir.glob("*.jar"))
    size_mb = sum(j.stat().st_size for j in lib_dir.glob("*.jar")) / 1024 / 1024
    print(f"  Ready: {jar_count} jars ({size_mb:.0f} MB)")
    return lib_dir


def get_classpath(lib_dir: Path | None = None) -> str:
    """Build the JVM classpath string from all jars in the Neo4j lib directory."""
    if lib_dir is None:
        lib_dir = get_neo4j_lib_dir()
    jars = sorted(lib_dir.glob("*.jar"))
    if not jars:
        raise RuntimeError(f"No jars found in {lib_dir}")
    return str(lib_dir / "*")

"""GraphEngine — Embedded Neo4j + Virtual Graphs + H2 in-memory via JPype."""

from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .jars import get_neo4j_lib_dir, get_classpath
from .jdbc import get_jdbc_jar
from .nvg_config import NVGConfigGenerator, NodeMapping, RelationshipMapping


class GraphEngine:
    """
    DataFrames in, Cypher out.

    Writes data to SQLite, boots embedded Neo4j with Virtual Graphs,
    and translates Cypher → SQL at query time. No Docker, no server,
    no data duplication.
    """

    def __init__(
        self,
        neo4j_home: str | Path | None = None,
        work_dir: str | Path | None = None,
        accept_license: bool = False,
    ):
        """
        Args:
            neo4j_home: Path to Neo4j lib/ directory. If None, auto-downloads.
            work_dir: Directory for SQLite DB and config. If None, uses temp dir.
            accept_license: Must be True to accept the Neo4j Enterprise license.
                See https://neo4j.com/terms/enterprise_us/
        """
        self._neo4j_lib = Path(neo4j_home) if neo4j_home else None
        self._work_dir = Path(work_dir) if work_dir else None
        self._temp_dir: str | None = None
        self._started = False
        self._db = None  # GraphDatabaseService
        self._mgmt = None  # DatabaseManagementService
        self._jvm_started = False

        if not accept_license:
            raise RuntimeError(
                "Neo4j Enterprise Edition requires license acceptance.\n"
                "Pass accept_license=True to GraphEngine() to confirm you accept the Neo4j "
                "Enterprise license agreement: https://neo4j.com/legal-terms/"
            )

        # H2 in-memory store + NVG config
        self._store = None
        self._config_gen = NVGConfigGenerator()

        # Accumulate mappings
        self._nodes: list[dict] = []
        self._relationships: list[dict] = []

    def add_nodes(
        self,
        data: Any,
        label: str,
        id_column: str,
    ) -> None:
        """
        Register nodes from a DataFrame or list of dicts.

        Args:
            data: pandas/polars DataFrame or list of dicts
            label: Node label (e.g. "Movie")
            id_column: Column to use as unique node identifier
        """
        self._nodes.append({
            "data": data,
            "label": label,
            "id_column": id_column,
        })

    def add_relationships(
        self,
        data: Any,
        type: str,
        source_column: str,
        source_label: str,
        target_column: str,
        target_label: str,
    ) -> None:
        """
        Register relationships from a DataFrame or list of dicts.

        Args:
            data: pandas/polars DataFrame or list of dicts
            type: Relationship type (e.g. "ACTED_IN")
            source_column: Column containing source node IDs
            source_label: Label of the source nodes
            target_column: Column containing target node IDs
            target_label: Label of the target nodes
        """
        self._relationships.append({
            "data": data,
            "type": type,
            "source_column": source_column,
            "source_label": source_label,
            "target_column": target_column,
            "target_label": target_label,
        })

    def start(self) -> None:
        """Write data to H2 in-memory, generate NVG config, start embedded Neo4j with Virtual Graphs."""
        if self._started:
            return

        # Set up work directory
        if self._work_dir:
            self._work_dir.mkdir(parents=True, exist_ok=True)
            work = self._work_dir
        else:
            self._temp_dir = tempfile.mkdtemp(prefix="neo4j_nano_")
            work = Path(self._temp_dir)

        nvg_config_dir = work / "nvg-config"
        neo4j_data_dir = work / "neo4j-data"
        neo4j_data_dir.mkdir(parents=True, exist_ok=True)

        # 1. Start JVM first (H2 store needs it)
        self._ensure_jvm()

        # 2. Write DataFrames to H2 in-memory via JDBC
        from .h2_store import H2Store, H2_JDBC_URL
        self._store = H2Store()
        self._write_data_to_store()
        # Keep connection open — H2 in-memory DB lives as long as a connection exists

        # 3. Generate NVG config (pointing to H2 in-memory)
        self._config_gen.write(nvg_config_dir, jdbc_url=H2_JDBC_URL)

        # 4. Start embedded Neo4j with VG enabled
        self._start_embedded_vg(neo4j_data_dir, nvg_config_dir)
        self._started = True

    def query(self, cypher: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """
        Execute a Cypher query against the Virtual Graph.

        Args:
            cypher: Cypher query string
            parameters: Optional query parameters

        Returns:
            List of result records as dicts
        """
        if not self._started:
            raise RuntimeError("Engine not started. Call engine.start() first.")

        import jpype

        params = parameters or {}
        HashMap = jpype.JClass("java.util.HashMap")
        java_params = HashMap()
        for k, v in params.items():
            java_params.put(k, v)

        results = []
        tx = self._db.beginTx()
        try:
            result = tx.execute(cypher, java_params)
            columns = list(result.columns())
            while result.hasNext():
                row = result.next()
                record = {}
                for col in columns:
                    val = row.get(col)
                    record[col] = self._from_java_value(val)
                results.append(record)
            tx.commit()
        except Exception:
            tx.rollback()
            raise
        finally:
            tx.close()

        return results

    def stop(self) -> None:
        """Shut down the embedded Neo4j instance and release H2 memory."""
        if self._mgmt:
            self._mgmt.shutdown()
            self._mgmt = None
            self._db = None
        if self._store:
            self._store.close()
            self._store = None
        self._started = False

        if self._temp_dir:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None

    def _write_data_to_store(self) -> None:
        """Write all registered nodes and relationships to the JDBC store."""
        for node_def in self._nodes:
            data = node_def["data"]
            label = node_def["label"]
            id_column = node_def["id_column"]
            table_name = f"NODE_{label.upper()}"

            dtypes = self._store.write_table(table_name, data)
            columns = self._get_columns(data)
            properties = [c for c in columns if c != id_column]
            property_types = self._map_dtypes(dtypes, properties)

            self._config_gen.add_node(NodeMapping(
                label=label,
                table_name=table_name,
                id_column=id_column,
                properties=properties,
                property_types=property_types,
            ))

        for rel_def in self._relationships:
            data = rel_def["data"]
            rel_type = rel_def["type"]
            src_col = rel_def["source_column"]
            src_label = rel_def["source_label"]
            tgt_col = rel_def["target_column"]
            tgt_label = rel_def["target_label"]
            table_name = f"REL_{rel_type.upper()}"

            dtypes = self._store.write_table(table_name, data)
            columns = self._get_columns(data)
            properties = [c for c in columns if c not in (src_col, tgt_col)]
            property_types = self._map_dtypes(dtypes, properties)

            self._config_gen.add_relationship(RelationshipMapping(
                type=rel_type,
                table_name=table_name,
                source_column=src_col,
                source_label=src_label,
                target_column=tgt_col,
                target_label=tgt_label,
                properties=properties,
                property_types=property_types,
            ))

    def _ensure_jvm(self) -> None:
        """Start the JVM if not already running."""
        import os
        import jpype
        import jpype.imports

        if jpype.isJVMStarted():
            self._jvm_started = True
            return

        # Set license acceptance env var for Neo4j Enterprise
        os.environ["NEO4J_ACCEPT_LICENSE_AGREEMENT"] = "yes"

        # Resolve Neo4j jars
        lib_dir = self._neo4j_lib if self._neo4j_lib else get_neo4j_lib_dir()
        classpath = get_classpath(lib_dir)

        # Also add SQLite JDBC jar to classpath
        jdbc_jar = get_jdbc_jar()

        # Find JVM path
        jvm_path = self._find_jvm()

        print("Starting JVM with Neo4j + SQLite JDBC classpath...")
        jpype.startJVM(
            jvm_path,
            classpath=[classpath, str(jdbc_jar)],
            convertStrings=True,
        )
        self._jvm_started = True
        atexit.register(self._shutdown_jvm)

    def _start_embedded_vg(self, data_dir: Path, nvg_config_dir: Path) -> None:
        """Create the embedded Neo4j with Virtual Graphs enabled."""
        import jpype

        JavaPath = jpype.JClass("java.nio.file.Path")
        db_dir = JavaPath.of(str(data_dir))

        # Configure with Virtual Graphs enabled
        Builder = jpype.JClass("com.neo4j.dbms.api.EnterpriseDatabaseManagementServiceBuilder")
        GraphDatabaseSettings = jpype.JClass("org.neo4j.configuration.GraphDatabaseSettings")

        builder = Builder(db_dir)

        # Enable Virtual Graphs
        # internal.virtual_graph.enabled = true
        # internal.virtual_graph.home = <path to config>
        try:
            VGSettings = jpype.JClass("com.neo4j.configuration.VirtualGraphSettings")
            builder.setConfig(VGSettings.virtual_graph_enabled, True)
            builder.setConfig(VGSettings.virtual_graph_home, JavaPath.of(str(nvg_config_dir)))
        except Exception:
            # Try alternative config key names
            try:
                from org.neo4j.configuration import SettingImpl
                builder.setConfig(
                    SettingImpl.newBuilder("internal.virtual_graph.enabled", jpype.JClass("org.neo4j.configuration.SettingValueParsers").BOOL, True).build(),
                    True
                )
            except Exception as e:
                print(f"  Warning: Could not set VG config programmatically: {e}")
                print("  Trying properties file approach...")

        # Write a neo4j.conf with VG settings
        conf_dir = data_dir / "conf"
        conf_dir.mkdir(parents=True, exist_ok=True)
        conf_path = conf_dir / "neo4j.conf"
        conf_path.write_text(
            f"server.config.strict_validation.enabled=false\n"
            f"internal.virtual_graph.enabled=true\n"
            f"internal.virtual_graph.home={nvg_config_dir}\n"
            f"server.bolt.enabled=false\n"
            f"server.http.enabled=false\n"
            f"dbms.cluster.endpoints=\n"
            f"dbms.cluster.minimum_initial_system_primaries_count=1\n"
            f"server.cluster.raft.listen_address=:0\n"
            f"server.cluster.listen_address=:0\n"
            f"server.cluster.raft.advertised_address=:0\n"
            f"server.cluster.advertised_address=:0\n"
            f"server.fleet_discovery.enabled=false\n"
            f"dbms.fleet_manager.enabled=false\n"
            f"server.backup.enabled=false\n"
            f"dbms.routing.enabled=false\n"
            f"dbms.usage_report.enabled=false\n"
            f"db.tx_log.rotation.retention_policy=false\n"
            f"db.tx_log.rotation.size=128K\n"
            f"db.checkpoint.interval.time=1h\n"
            f"db.checkpoint.interval.tx=1000000\n"
            f"server.logs.debug.enabled=false\n"
            f"server.logs.gc.enabled=false\n"
            f"server.metrics.enabled=false\n"
            f"dbms.security.auth_enabled=false\n"
        )

        # Neo4j requires a server-logs.xml — provide a minimal one
        logs_xml = conf_dir / "server-logs.xml"
        if not logs_xml.exists():
            logs_xml.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<Configuration status="ERROR">\n'
                '  <Appenders>\n'
                '    <Console name="console" target="SYSTEM_OUT">\n'
                '      <PatternLayout pattern="%d{yyyy-MM-dd HH:mm:ss.SSSZ} %-5p %m%n"/>\n'
                '    </Console>\n'
                '  </Appenders>\n'
                '  <Loggers>\n'
                '    <Root level="ERROR"><AppenderRef ref="console"/></Root>\n'
                '  </Loggers>\n'
                '</Configuration>\n'
            )

        builder.loadPropertiesFromFile(JavaPath.of(str(conf_path)))

        self._mgmt = builder.build()

        # Get default database — it starts asynchronously, so retry until available
        import time
        DEFAULT_DB = GraphDatabaseSettings.DEFAULT_DATABASE_NAME
        for attempt in range(60):
            try:
                self._db = self._mgmt.database(DEFAULT_DB)
                tx = self._db.beginTx()
                tx.close()
                break
            except Exception:
                if attempt == 59:
                    raise
                time.sleep(1)

        print("Neo4j embedded started with Virtual Graphs.")

    @staticmethod
    def _find_jvm() -> str:
        """Find a Java 21+ JVM path."""
        import subprocess
        import os

        # 1. JAVA_HOME environment variable
        java_home = os.environ.get("JAVA_HOME")
        if java_home:
            jvm = Path(java_home) / "lib" / "server" / "libjvm.dylib"
            if jvm.exists():
                return str(jvm)
            jvm = Path(java_home) / "lib" / "server" / "libjvm.so"
            if jvm.exists():
                return str(jvm)

        # 2. macOS: /usr/libexec/java_home
        try:
            result = subprocess.run(
                ["/usr/libexec/java_home", "-v", "21"],
                capture_output=True, text=True, check=True,
            )
            java_home = result.stdout.strip()
            jvm = Path(java_home) / "lib" / "server" / "libjvm.dylib"
            if jvm.exists():
                return str(jvm)
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        # 3. Let JPype find it (default)
        import jpype
        return jpype.getDefaultJVMPath()

    def _from_java_value(self, value: Any) -> Any:
        """Convert Java result values back to Python types."""
        if value is None:
            return None

        import jpype

        # Node
        if jpype.JClass("org.neo4j.graphdb.Node").class_.isInstance(value):
            props = {}
            for key in value.getPropertyKeys():
                props[str(key)] = self._from_java_value(value.getProperty(str(key)))
            props["_labels"] = [str(l.name()) for l in value.getLabels()]
            return props

        # Relationship
        if jpype.JClass("org.neo4j.graphdb.Relationship").class_.isInstance(value):
            props = {}
            for key in value.getPropertyKeys():
                props[str(key)] = self._from_java_value(value.getProperty(str(key)))
            props["_type"] = str(value.getType().name())
            return props

        # Path
        if jpype.JClass("org.neo4j.graphdb.Path").class_.isInstance(value):
            return str(value)

        # Map
        if jpype.JClass("java.util.Map").class_.isInstance(value):
            return {str(k): self._from_java_value(v) for k, v in value.entrySet()}

        # List
        if jpype.JClass("java.util.List").class_.isInstance(value):
            return [self._from_java_value(item) for item in value]

        # Numeric types
        if jpype.JClass("java.lang.Long").class_.isInstance(value):
            return int(value.longValue())
        if jpype.JClass("java.lang.Integer").class_.isInstance(value):
            return int(value.intValue())
        if jpype.JClass("java.lang.Double").class_.isInstance(value):
            return float(value.doubleValue())
        if jpype.JClass("java.lang.Float").class_.isInstance(value):
            return float(value.floatValue())
        if jpype.JClass("java.lang.Boolean").class_.isInstance(value):
            return bool(value.booleanValue())

        # Default
        return str(value) if not isinstance(value, (int, float, bool, str)) else value

    def _get_columns(self, data: Any) -> list[str]:
        """Extract column names from various data types."""
        if hasattr(data, "columns"):
            return list(data.columns)
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
            return list(data[0].keys())
        return []

    def _map_dtypes(self, sqlite_dtypes: dict[str, str], properties: list[str]) -> dict[str, str]:
        """Map SQLite column types to NVG schema types."""
        type_map = {
            "INTEGER": "INTEGER",
            "REAL": "FLOAT",
            "TEXT": "STRING",
        }
        result = {}
        for prop in properties:
            sqlite_type = sqlite_dtypes.get(prop.upper(), "TEXT")
            result[prop] = type_map.get(sqlite_type, "STRING")
        return result

    @staticmethod
    def _shutdown_jvm() -> None:
        """Shutdown JVM on process exit."""
        import jpype
        if jpype.isJVMStarted():
            jpype.shutdownJVM()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stop()

    def __del__(self):
        if self._started:
            self.stop()

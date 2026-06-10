#!/usr/bin/env python3
"""
Build the definitive REQUIRED_JARS whitelist by running embedded Neo4j Enterprise
with ALL jars, using JVM -Xlog:class+load to trace loaded classes to a file.

Uses JPype in-process (no subprocess issues). Maps loaded classes → jar files.

Usage:
  cd /path/to/neo4j-nano && source .venv/bin/activate
  python scripts/build_whitelist.py
"""

import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

NEO4J_VERSION = "2026.05.0"
CACHE_DIR = Path.home() / ".cache" / "neo4j-nano"
TAR_PATH = CACHE_DIR / f"neo4j-enterprise-{NEO4J_VERSION}-unix.tar.gz"
CLASS_LOG = Path("/tmp/neo4j_classload.log")

# ---------------------------------------------------------------------------
# Step 1: Download tarball if missing
# ---------------------------------------------------------------------------
if not TAR_PATH.exists():
    import subprocess
    print(f"Downloading Neo4j Enterprise {NEO4J_VERSION}...")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = TAR_PATH.with_suffix(".tmp")
    subprocess.run(["curl", "-L", "-#", "-o", str(tmp),
                     f"https://dist.neo4j.org/neo4j-enterprise-{NEO4J_VERSION}-unix.tar.gz"],
                    check=True)
    tmp.rename(TAR_PATH)

# ---------------------------------------------------------------------------
# Step 2: Extract ALL jars to temp dir
# ---------------------------------------------------------------------------
tmpdir = tempfile.mkdtemp(prefix="neo4j_whitelist_")
lib_dir = Path(tmpdir) / "lib"
lib_dir.mkdir()
work_dir = Path(tmpdir) / "work"
work_dir.mkdir()

print("Extracting ALL jars from tarball...")
with tarfile.open(TAR_PATH, "r:gz") as tf:
    for member in tf.getmembers():
        parts = member.name.split("/", 1)
        if len(parts) == 2 and parts[1].startswith("lib/") and member.isfile() and member.name.endswith(".jar"):
            jar_name = parts[1].removeprefix("lib/")
            member.name = jar_name
            tf.extract(member, path=str(lib_dir), filter="data")

jar_count = sum(1 for _ in lib_dir.glob("*.jar"))
print(f"  {jar_count} jars extracted to {lib_dir}")

# ---------------------------------------------------------------------------
# Step 3: Build class → jar mapping (scan all jars for .class entries)
# ---------------------------------------------------------------------------
print("Building class → jar index...")
class_to_jar: dict[str, str] = {}
for jar_path in sorted(lib_dir.glob("*.jar")):
    try:
        with zipfile.ZipFile(jar_path) as zf:
            for entry in zf.namelist():
                if entry.endswith(".class"):
                    # Convert path to class name: com/foo/Bar.class → com.foo.Bar
                    class_name = entry[:-6].replace("/", ".")
                    class_to_jar[class_name] = jar_path.name
    except zipfile.BadZipFile:
        pass
print(f"  Indexed {len(class_to_jar)} classes across {jar_count} jars")

# ---------------------------------------------------------------------------
# Step 4: Get SQLite JDBC jar
# ---------------------------------------------------------------------------
jdbc_jar = CACHE_DIR / "sqlite-jdbc-3.49.1.0.jar"
if not jdbc_jar.exists():
    import urllib.request
    print("Downloading SQLite JDBC driver...")
    urllib.request.urlretrieve(
        "https://repo1.maven.org/maven2/org/xerial/sqlite-jdbc/3.49.1.0/sqlite-jdbc-3.49.1.0.jar",
        str(jdbc_jar))

# ---------------------------------------------------------------------------
# Step 5: Create SQLite DB with test data
# ---------------------------------------------------------------------------
db_path = work_dir / "graph.db"
conn = sqlite3.connect(str(db_path))
conn.execute('CREATE TABLE node_person ("PERSON_ID" TEXT, "NAME" TEXT, "AGE" INTEGER)')
conn.execute('INSERT INTO node_person VALUES ("p1", "Alice", 30)')
conn.execute('INSERT INTO node_person VALUES ("p2", "Bob", 25)')
conn.execute('CREATE TABLE node_movie ("MOVIE_ID" TEXT, "TITLE" TEXT, "YEAR" INTEGER)')
conn.execute('INSERT INTO node_movie VALUES ("m1", "The Matrix", 1999)')
conn.execute('INSERT INTO node_movie VALUES ("m2", "Speed", 1994)')
conn.execute('CREATE TABLE rel_acted_in ("PERSON_ID" TEXT, "MOVIE_ID" TEXT, "ROLE" TEXT)')
conn.execute('INSERT INTO rel_acted_in VALUES ("p1", "m1", "Neo")')
conn.execute('INSERT INTO rel_acted_in VALUES ("p2", "m1", "Switch")')
conn.execute('INSERT INTO rel_acted_in VALUES ("p1", "m2", "Jack")')
conn.commit()
conn.close()

# ---------------------------------------------------------------------------
# Step 6: Create NVG config
# ---------------------------------------------------------------------------
nvg_dir = work_dir / "nvg-config"
nvg_dir.mkdir()
(nvg_dir / "datasource.json").write_text(json.dumps({
    "type": "generic",
    "url": f"jdbc:sqlite:{db_path}"
}))
(nvg_dir / "secret.json").write_text(json.dumps({
    "type": "anonymous",
    "username": "",
    "password": ""
}))
(nvg_dir / "schema.json").write_text(json.dumps({
    "catalog": "graph",
    "schema": "main",
    "entities": {
        "nodes": [
            {
                "label": "Person",
                "table": "node_person",
                "key": [{"column": "PERSON_ID"}],
                "properties": [
                    {"column": "NAME", "name": "name", "type": "STRING"},
                    {"column": "AGE", "name": "age", "type": "INTEGER"},
                ],
            },
            {
                "label": "Movie",
                "table": "node_movie",
                "key": [{"column": "MOVIE_ID"}],
                "properties": [
                    {"column": "TITLE", "name": "title", "type": "STRING"},
                    {"column": "YEAR", "name": "year", "type": "INTEGER"},
                ],
            },
        ],
        "relationships": [
            {
                "label": "ACTED_IN",
                "table": "rel_acted_in",
                "start": {
                    "targetEntity": "Person",
                    "keys": [{"nodeColumn": "PERSON_ID", "relationshipColumn": "PERSON_ID"}],
                },
                "end": {
                    "targetEntity": "Movie",
                    "keys": [{"nodeColumn": "MOVIE_ID", "relationshipColumn": "MOVIE_ID"}],
                },
                "key": [{"column": "PERSON_ID"}, {"column": "MOVIE_ID"}],
                "properties": [
                    {"column": "ROLE", "name": "role", "type": "STRING"},
                ],
            },
        ],
    },
}))

# ---------------------------------------------------------------------------
# Step 7: Start JVM with ALL jars + -Xlog:class+load → file
# ---------------------------------------------------------------------------
print(f"\nStarting JVM with ALL {jar_count} jars + class-load tracing → {CLASS_LOG}")

import jpype
import jpype.imports

if jpype.isJVMStarted():
    print("ERROR: JVM already started. Run this script in a fresh Python process.")
    sys.exit(1)

# Build classpath with ALL jars + JDBC
all_jars_glob = str(lib_dir / "*")
jvm_path = None

# Find JVM
java_home = os.environ.get("JAVA_HOME")
if java_home:
    jvm = Path(java_home) / "lib" / "server" / "libjvm.dylib"
    if jvm.exists():
        jvm_path = str(jvm)
if not jvm_path:
    import subprocess
    try:
        result = subprocess.run(["/usr/libexec/java_home", "-v", "21"],
                                capture_output=True, text=True, check=True)
        java_home = result.stdout.strip()
        jvm = Path(java_home) / "lib" / "server" / "libjvm.dylib"
        if jvm.exists():
            jvm_path = str(jvm)
    except Exception:
        pass
if not jvm_path:
    jvm_path = jpype.getDefaultJVMPath()

# Clear old log
CLASS_LOG.unlink(missing_ok=True)

jpype.startJVM(
    jvm_path,
    f"-Xlog:class+load=info:file={CLASS_LOG}:tags,uptime",
    classpath=[all_jars_glob, str(jdbc_jar)],
    convertStrings=True,
)
print("  JVM started.")

# ---------------------------------------------------------------------------
# Step 8: Boot Neo4j Enterprise with Virtual Graphs
# ---------------------------------------------------------------------------
print("Booting Neo4j Enterprise with Virtual Graphs...")

neo4j_data_dir = work_dir / "neo4j-data"
neo4j_data_dir.mkdir(exist_ok=True)

JavaPath = jpype.JClass("java.nio.file.Path")

# Write neo4j.conf
conf_path = neo4j_data_dir / "neo4j.conf"
conf_path.write_text(
    f"internal.virtual_graph.enabled=true\n"
    f"internal.virtual_graph.home={nvg_dir}\n"
    f"server.bolt.enabled=false\n"
    f"server.http.enabled=false\n"
)

Builder = jpype.JClass("com.neo4j.dbms.api.EnterpriseDatabaseManagementServiceBuilder")
builder = Builder(JavaPath.of(str(neo4j_data_dir)))
builder.loadPropertiesFromFile(JavaPath.of(str(conf_path)))

print("  Building DBMS (this takes a moment)...")
mgmt = builder.build()

GraphDatabaseSettings = jpype.JClass("org.neo4j.configuration.GraphDatabaseSettings")
DEFAULT_DB = GraphDatabaseSettings.DEFAULT_DATABASE_NAME

# Wait for the database to become available (it starts asynchronously)
import time
print(f"  Waiting for database '{DEFAULT_DB}' to become available...")
for attempt in range(60):
    try:
        db = mgmt.database(DEFAULT_DB)
        # Check if it's actually available
        tx = db.beginTx()
        tx.close()
        break
    except Exception as e:
        if attempt == 59:
            print(f"  ERROR: Database not available after 60 attempts: {e}")
            mgmt.shutdown()
            sys.exit(1)
        time.sleep(1)
print(f"  DBMS started (attempt {attempt + 1}).")

# ---------------------------------------------------------------------------
# Step 9: Run a comprehensive Cypher workload
# ---------------------------------------------------------------------------
print("Running comprehensive Cypher workload...")

queries = [
    # Basic reads
    "MATCH (n) RETURN count(n) AS cnt",
    "MATCH (p:Person) RETURN p.name AS name, p.age AS age ORDER BY p.name",
    "MATCH (m:Movie) RETURN m.title AS title, m.year AS year",
    # Relationship traversal
    "MATCH (p:Person)-[:ACTED_IN]->(m:Movie) RETURN p.name AS person, m.title AS movie",
    "MATCH (p:Person)-[r:ACTED_IN]->(m:Movie) RETURN p.name, r.role, m.title",
    # Aggregation
    "MATCH (p:Person)-[:ACTED_IN]->(m:Movie) RETURN m.title AS movie, count(p) AS actors",
    "MATCH (p:Person)-[:ACTED_IN]->(m:Movie) RETURN p.name AS person, collect(m.title) AS movies",
    # Filtering
    "MATCH (p:Person) WHERE p.age > 26 RETURN p.name AS name",
    "MATCH (m:Movie) WHERE m.year < 1998 RETURN m.title AS title",
    # UNWIND + WITH
    "UNWIND [1,2,3] AS x RETURN x",
    "MATCH (p:Person) WITH p.name AS name, p.age AS age WHERE age > 20 RETURN name, age",
    # Path queries
    "MATCH path = (p:Person)-[:ACTED_IN]->(m:Movie) RETURN length(path) AS len LIMIT 5",
    # EXISTS / pattern comprehension
    "MATCH (p:Person) WHERE EXISTS { (p)-[:ACTED_IN]->() } RETURN p.name",
    # OPTIONAL MATCH
    "MATCH (p:Person) OPTIONAL MATCH (p)-[:DIRECTED]->(m:Movie) RETURN p.name, m.title",
    # DISTINCT
    "MATCH (p:Person)-[:ACTED_IN]->(m:Movie) RETURN DISTINCT m.title AS movie",
    # CASE
    "MATCH (p:Person) RETURN p.name, CASE WHEN p.age > 28 THEN 'senior' ELSE 'junior' END AS tier",
    # String functions
    "MATCH (p:Person) RETURN toUpper(p.name) AS upper_name",
    # CALL db.labels()
    "CALL db.labels() YIELD label RETURN label",
    # Schema
    "CALL db.schema.visualization()",
]

HashMap = jpype.JClass("java.util.HashMap")
empty_params = HashMap()

for i, q in enumerate(queries):
    try:
        tx = db.beginTx()
        result = tx.execute(q, empty_params)
        count = 0
        while result.hasNext():
            result.next()
            count += 1
        tx.commit()
        tx.close()
        print(f"  [{i+1}/{len(queries)}] OK ({count} rows): {q[:60]}")
    except Exception as e:
        print(f"  [{i+1}/{len(queries)}] FAIL: {q[:60]}")
        print(f"    {e}")
        try:
            tx.rollback()
            tx.close()
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Step 10: Shut down
# ---------------------------------------------------------------------------
print("\nShutting down Neo4j...")
mgmt.shutdown()
print("  Done.")

# ---------------------------------------------------------------------------
# Step 11: Parse class-load log → map to jars → emit REQUIRED_JARS
# ---------------------------------------------------------------------------
print(f"\nParsing class-load log ({CLASS_LOG})...")

# Format: [<uptime>s][info][class,load] <classname> source: <source>
# We care about classes loaded from our lib_dir
loaded_classes: set[str] = set()
with open(CLASS_LOG) as f:
    for line in f:
        # Extract class name from lines like:
        # [0.123s][info][class,load] com.foo.Bar source: file:/path/to/lib/bar.jar
        match = re.search(r'\[class,load\]\s+(\S+)', line)
        if match:
            loaded_classes.add(match.group(1))

print(f"  {len(loaded_classes)} classes loaded total")

# Map to jars
needed_jars: set[str] = set()
unmapped_neo4j_classes: list[str] = []
for cls in sorted(loaded_classes):
    jar = class_to_jar.get(cls)
    if jar:
        needed_jars.add(jar)
    elif cls.startswith(("org.neo4j.", "com.neo4j.")):
        unmapped_neo4j_classes.append(cls)

print(f"  {len(needed_jars)} jars needed (from {jar_count} total)")
if unmapped_neo4j_classes:
    print(f"  {len(unmapped_neo4j_classes)} unmapped neo4j classes (generated/inner)")

# ---------------------------------------------------------------------------
# Step 12: Output the REQUIRED_JARS frozenset
# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
print("REQUIRED_JARS = frozenset({")
for jar in sorted(needed_jars):
    print(f'    "{jar}",')
print("})")
print("=" * 70)

# Also write to a file for easy copy-paste
output_file = Path("/tmp/neo4j_required_jars.py")
with open(output_file, "w") as f:
    f.write("REQUIRED_JARS = frozenset({\n")
    for jar in sorted(needed_jars):
        f.write(f'    "{jar}",\n')
    f.write("})\n")
print(f"\nWritten to {output_file}")

# Report jar savings
all_jar_names = {j.name for j in lib_dir.glob("*.jar")}
removed = all_jar_names - needed_jars
print(f"\nJar reduction: {jar_count} → {len(needed_jars)} "
      f"(removed {len(removed)}, saved {len(removed)/jar_count*100:.0f}%)")

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
print(f"\nTemp dir: {tmpdir}")
print("Clean up with: rm -rf " + tmpdir)

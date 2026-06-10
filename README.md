# `neo4j-nano`: Embedded Virtual Graph Engine 
DataFrames in, Cypher out. [Neo4j Virtual Graphs](https://neo4j.com/docs/virtual-graph/) embedded directly in Python.

```python
import pandas as pd
from neo4j_nano import GraphEngine

engine = GraphEngine(accept_license=True)

# Load CSVs as DataFrames
movies = pd.read_csv("data/movies.csv")
people = pd.read_csv("data/people.csv")
acted_in = pd.read_csv("data/acted_in.csv")

# Build graph
engine.add_nodes(movies, label="Movie", id_column="movie_id")
engine.add_nodes(people, label="Person", id_column="person_id")
engine.add_relationships(acted_in, type="ACTED_IN",
    source_column="person_id", source_label="Person",
    target_column="movie_id", target_label="Movie")

engine.start()

results = engine.query("""
    MATCH (p:Person)-[:ACTED_IN]->(m:Movie)
    WHERE m.release_year > 2000
    RETURN p.name AS actor, m.title AS movie
""")
print(results)  # [{'actor': 'Keanu Reeves', 'movie': 'John Wick'}]

engine.stop()
```

## How it works

DataFrames are loaded into H2 in-memory, Neo4j Virtual Graphs maps them as a graph, Cypher queries are translated to SQL at query time.


- **Your DataFrames are loaded into H2**, a pure-Java in-memory database running inside the JVM. Neo4j's Virtual Graph engine connects to it via JDBC and exposes your tables as a graph — nodes, relationships, and properties, all queryable with Cypher. Zero disk I/O for your data.
- **JPype** bridges Python and the JVM at the native level (shared memory, no sockets). The entire Neo4j engine runs inside your Python process.
- **Minimal jar set** — Neo4j ships a lot of jars (~366MB). We traced actual class loading with `-Xlog:class+load` and kept only those needed for the Cypher engine + Virtual Graphs, getting the footprint down to **126MB**.
    - Future goal: download only required jar files directly instead of pruning the list.
- **No server, no ports, no network**: Minimal Neo4j config, everything runs in-process. Queries go through the JVM bridge, not over Bolt or HTTP.

## Prerequisites

- Python 3.10+
- Java 21+ 
- Accept Neo4j Enterprise Edition license

## Install & Run

```bash
pip install neo4j-nano
```

On first run, Neo4j jars are downloaded and cached at `~/.cache/neo4j-nano/`.
After this, it should take ±3 seconds to start up.

## Demo

Open `demo.ipynb` to see it in action: load CSVs, build a graph, and run Cypher queries interactively.

```bash
jupyter notebook demo.ipynb
```

## API

| Method | Description |
|--------|-------------|
| `GraphEngine(accept_license=True)` | Create engine — requires acknowledgement of owning a [Neo4j Enterprise Edition license](http://neo4j.com/legal-terms/) |
| `.add_nodes(data, label, id_column)` | Register nodes from a DataFrame or list of dicts |
| `.add_relationships(data, type, source_column, source_label, target_column, target_label)` | Register relationships (extra columns become properties) |
| `.start()` | Boot the JVM + Neo4j and load data |
| `.query(cypher, parameters=None)` | Run a Cypher query, returns list of dicts |
| `.stop()` | Shut down and clean up |

Also works as a context manager:

```python
with GraphEngine(accept_license=True) as engine:
    engine.add_nodes(df, label="Movie", id_column="id")
    engine.start()
    results = engine.query("MATCH (m:Movie) RETURN m.title")
```


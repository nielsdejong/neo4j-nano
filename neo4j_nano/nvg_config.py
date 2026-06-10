"""Generate Neo4j Virtual Graph configuration files (datasource.json, schema.json, secret.json)."""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class NodeMapping:
    label: str
    table_name: str
    id_column: str
    properties: list[str] = field(default_factory=list)
    property_types: dict[str, str] = field(default_factory=dict)


@dataclass
class RelationshipMapping:
    type: str
    table_name: str
    source_column: str
    source_label: str
    target_column: str
    target_label: str
    properties: list[str] = field(default_factory=list)
    property_types: dict[str, str] = field(default_factory=dict)


# Map pandas/python dtypes to NVG schema types
_DTYPE_MAP = {
    "int64": "INTEGER",
    "int32": "INTEGER",
    "float64": "FLOAT",
    "float32": "FLOAT",
    "object": "STRING",
    "str": "STRING",
    "bool": "BOOLEAN",
}


class NVGConfigGenerator:
    """Generates the NVG config directory from node/relationship mappings."""

    def __init__(self, catalog: str = "NEO4J_NANO", schema: str = "PUBLIC"):
        self._nodes: list[NodeMapping] = []
        self._relationships: list[RelationshipMapping] = []
        self._catalog = catalog
        self._schema = schema

    def add_node(self, mapping: NodeMapping):
        self._nodes.append(mapping)

    def add_relationship(self, mapping: RelationshipMapping):
        self._relationships.append(mapping)

    def write(self, config_dir: Path, jdbc_url: str = "jdbc:h2:mem:neo4j_nano;DB_CLOSE_DELAY=-1"):
        """Write datasource.json, secret.json, schema.json to config_dir."""
        config_dir.mkdir(parents=True, exist_ok=True)

        self._write_datasource(config_dir, jdbc_url)
        self._write_secret(config_dir)
        self._write_schema(config_dir)

    def _write_datasource(self, config_dir: Path, jdbc_url: str):
        datasource = {
            "type": "generic",
            "url": jdbc_url
        }
        (config_dir / "datasource.json").write_text(json.dumps(datasource, indent=2))

    def _write_secret(self, config_dir: Path):
        secret = {
            "type": "anonymous",
            "username": "",
            "password": ""
        }
        (config_dir / "secret.json").write_text(json.dumps(secret, indent=2))

    def _write_schema(self, config_dir: Path):
        schema = {
            "catalog": self._catalog,
            "schema": self._schema,
            "entities": {
                "nodes": self._build_node_schemas(),
                "relationships": self._build_relationship_schemas()
            }
        }
        (config_dir / "schema.json").write_text(json.dumps(schema, indent=2))

    def _build_node_schemas(self) -> list[dict]:
        nodes = []
        for n in self._nodes:
            props = []
            for prop in n.properties:
                if prop == n.id_column:
                    continue
                prop_type = n.property_types.get(prop, "STRING")
                props.append({
                    "name": prop,
                    "column": prop.upper(),
                    "type": prop_type,
                })

            node_schema = {
                "label": n.label,
                "table": n.table_name,
                "properties": props,
                "key": [{"column": n.id_column.upper()}],
            }
            nodes.append(node_schema)
        return nodes

    def _build_relationship_schemas(self) -> list[dict]:
        rels = []
        for r in self._relationships:
            props = []
            for prop in r.properties:
                if prop in (r.source_column, r.target_column):
                    continue
                prop_type = r.property_types.get(prop, "STRING")
                props.append({
                    "name": prop,
                    "column": prop.upper(),
                    "type": prop_type,
                })

            rel_schema = {
                "label": r.type,
                "table": r.table_name,
                "start": {
                    "targetEntity": r.source_label,
                    "keys": [{"nodeColumn": r.source_column.upper(), "relationshipColumn": r.source_column.upper()}],
                },
                "end": {
                    "targetEntity": r.target_label,
                    "keys": [{"nodeColumn": r.target_column.upper(), "relationshipColumn": r.target_column.upper()}],
                },
                "properties": props,
                "key": [{"column": r.source_column.upper()}, {"column": r.target_column.upper()}],
            }
            rels.append(rel_schema)
        return rels

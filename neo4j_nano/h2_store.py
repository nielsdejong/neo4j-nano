"""Write DataFrames into an H2 in-memory database for Neo4j Virtual Graphs to read via JDBC."""

from __future__ import annotations

from typing import Any

# JDBC URL used for both writing (here) and reading (by Virtual Graphs).
H2_JDBC_URL = "jdbc:h2:mem:neo4j_nano;DB_CLOSE_DELAY=-1"


class H2Store:
    """Manages writing node and relationship data into an H2 in-memory database via JDBC/JPype."""

    def __init__(self):
        import jpype
        DriverManager = jpype.JClass("java.sql.DriverManager")
        self._conn = DriverManager.getConnection(H2_JDBC_URL)

    def write_table(self, table_name: str, data: Any) -> dict[str, str]:
        """Write data to an H2 table.

        Returns a dict mapping column names (uppercase) to their inferred types.
        """
        table_name = table_name.upper()
        rows, columns = self._extract_rows_and_columns(data)
        if not rows:
            return {}

        upper_columns = [c.upper() for c in columns]

        # Infer types from first row
        dtypes = {}
        h2_types = {}
        for col, val in zip(upper_columns, rows[0]):
            if isinstance(val, int):
                dtypes[col] = "INTEGER"
                h2_types[col] = "BIGINT"
            elif isinstance(val, float):
                dtypes[col] = "REAL"
                h2_types[col] = "DOUBLE"
            else:
                dtypes[col] = "TEXT"
                h2_types[col] = "VARCHAR"

        stmt = self._conn.createStatement()

        # Create table
        col_defs = ", ".join(f"{c} {h2_types[c]}" for c in upper_columns)
        stmt.execute(f"DROP TABLE IF EXISTS {table_name}")
        stmt.execute(f"CREATE TABLE {table_name} ({col_defs})")
        stmt.close()

        # Insert rows using PreparedStatement
        placeholders = ", ".join("?" for _ in upper_columns)
        ps = self._conn.prepareStatement(f"INSERT INTO {table_name} VALUES ({placeholders})")

        for row in rows:
            for i, val in enumerate(row, 1):
                if val is None:
                    ps.setNull(i, 0)
                elif isinstance(val, int):
                    ps.setLong(i, val)
                elif isinstance(val, float):
                    ps.setDouble(i, val)
                else:
                    ps.setString(i, str(val))
            ps.addBatch()

        ps.executeBatch()
        self._conn.commit()
        ps.close()

        return dtypes

    def _extract_rows_and_columns(self, data: Any) -> tuple[list[tuple], list[str]]:
        """Extract rows and column names from various data types."""
        if hasattr(data, "iterrows") and hasattr(data, "columns"):
            columns = list(data.columns)
            rows = [tuple(row) for row in data.values]
            return rows, columns
        if hasattr(data, "to_pandas"):
            pdf = data.to_pandas()
            columns = list(pdf.columns)
            rows = [tuple(row) for row in pdf.values]
            return rows, columns
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
            columns = list(data[0].keys())
            rows = [tuple(d.get(c) for c in columns) for d in data]
            return rows, columns
        raise TypeError(f"Unsupported data type: {type(data)}. Pass a pandas/polars DataFrame or list of dicts.")

    def close(self):
        if self._conn:
            self._conn.close()

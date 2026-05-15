"""
SQLite-based structured memory storage
"""
import sqlite3
import hashlib
from pathlib import Path
from typing import List, Tuple, Optional
from datetime import datetime
from .config import get_config

class SQLiteMemoryStore:
    """Handle SQLite-based structured memory operations."""
    
    def __init__(self):
        self.config = get_config()
        db_path = self.config['settings']['memory']['long_term']['path']
        if not Path(db_path).is_absolute():
            db_path = self.config['workspace_dir'] / db_path
        
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        kg_path = self.config["settings"]["memory"].get("knowledge_graph", {}).get("db_path")
        self.kg_db_path = Path(kg_path) if kg_path else None
        self._kg_bridge_enabled = bool(self.kg_db_path and self.kg_db_path.exists())
        self._init_database()

    def _canonical_key(self, value: str) -> str:
        return hashlib.sha1(value.strip().lower().encode("utf-8")).hexdigest()

    def _mirror_entity_to_kg(self, name: str, entity_type: str = "unknown") -> None:
        if not self._kg_bridge_enabled or not self.kg_db_path:
            return
        now = datetime.now().isoformat()
        canonical = str(name or "").strip()
        if not canonical:
            return
        name_key = self._canonical_key(canonical)
        try:
            with sqlite3.connect(self.kg_db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT OR IGNORE INTO entities
                    (canonical_name, name_key, entity_type, importance, access_count, created_at, updated_at, source_memory_ref)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (canonical, name_key, entity_type, 0.5, 0, now, now, "sqlite_store_bridge"),
                )
                cur.execute(
                    "UPDATE entities SET updated_at = ?, entity_type = COALESCE(entity_type, ?) WHERE name_key = ?",
                    (now, entity_type, name_key),
                )
                conn.commit()
        except Exception:
            return

    def _mirror_relation_to_kg(self, subject_name: str, predicate: str, object_name: str, strength: float = 1.0) -> None:
        if not self._kg_bridge_enabled or not self.kg_db_path:
            return
        now = datetime.now().isoformat()
        try:
            self._mirror_entity_to_kg(subject_name, "unknown")
            self._mirror_entity_to_kg(object_name, "unknown")
            with sqlite3.connect(self.kg_db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT id FROM entities WHERE name_key = ?", (self._canonical_key(subject_name),))
                s = cur.fetchone()
                cur.execute("SELECT id FROM entities WHERE name_key = ?", (self._canonical_key(object_name),))
                o = cur.fetchone()
                if not s or not o:
                    return
                relation_type = str(predicate or "").strip() or "related_to"
                cur.execute(
                    """
                    SELECT id, strength FROM relations
                    WHERE subject_entity_id = ? AND object_entity_id = ? AND relation_type = ?
                    """,
                    (int(s[0]), int(o[0]), relation_type),
                )
                row = cur.fetchone()
                if row:
                    cur.execute(
                        """
                        UPDATE relations
                        SET strength = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (max(float(row[1] or 0.0), float(strength or 0.0)), now, int(row[0])),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO relations
                        (subject_entity_id, object_entity_id, relation_type, strength, confidence, access_count, created_at, updated_at, source_memory_ref)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (int(s[0]), int(o[0]), relation_type, float(strength or 0.0), 0.6, 0, now, now, "sqlite_store_bridge"),
                    )
                conn.commit()
        except Exception:
            return
    
    def _init_database(self):
        """Initialize SQLite database with required tables."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Create entities table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS entities (
                    id INTEGER PRIMARY KEY,
                    name TEXT UNIQUE,
                    type TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_accessed TIMESTAMP
                )
            ''')
            
            # Create relations table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS relations (
                    subject_id INTEGER,
                    predicate TEXT,
                    object_id INTEGER,
                    strength REAL DEFAULT 1.0,
                    FOREIGN KEY(subject_id) REFERENCES entities(id),
                    FOREIGN KEY(object_id) REFERENCES entities(id)
                )
            ''')
            
            # Create index for faster lookups
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_relations_subject ON relations(subject_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_relations_object ON relations(object_id)')
            
            conn.commit()
    
    def add_entity(self, name: str, entity_type: str = "unknown") -> int:
        """Add an entity to the database, return its ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO entities (name, type, last_accessed) VALUES (?, ?, ?)",
                    (name, entity_type, datetime.now().isoformat())
                )
                conn.commit()
                
                # Get the entity ID
                cursor.execute("SELECT id FROM entities WHERE name = ?", (name,))
                result = cursor.fetchone()
                self._mirror_entity_to_kg(name, entity_type)
                return result[0] if result else -1
                
            except sqlite3.Error as e:
                print(f"Error adding entity {name}: {e}")
                return -1
    
    def add_relation(self, subject_name: str, predicate: str, object_name: str, strength: float = 1.0) -> bool:
        """Add a relation between two entities."""
        subject_id = self.add_entity(subject_name)
        object_id = self.add_entity(object_name)
        
        if subject_id == -1 or object_id == -1:
            return False
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            try:
                # Check if relation already exists
                cursor.execute(
                    "SELECT strength FROM relations WHERE subject_id = ? AND predicate = ? AND object_id = ?",
                    (subject_id, predicate, object_id)
                )
                existing = cursor.fetchone()
                
                if existing:
                    # Update strength
                    new_strength = max(existing[0], strength)
                    cursor.execute(
                        "UPDATE relations SET strength = ? WHERE subject_id = ? AND predicate = ? AND object_id = ?",
                        (new_strength, subject_id, predicate, object_id)
                    )
                else:
                    # Insert new relation
                    cursor.execute(
                        "INSERT INTO relations (subject_id, predicate, object_id, strength) VALUES (?, ?, ?, ?)",
                        (subject_id, predicate, object_id, strength)
                    )
                
                conn.commit()
                self._mirror_relation_to_kg(subject_name, predicate, object_name, strength)
                return True
                
            except sqlite3.Error as e:
                print(f"Error adding relation {subject_name} {predicate} {object_name}: {e}")
                return False
    
    def get_entity_relations(self, entity_name: str) -> List[Tuple[str, str, float]]:
        """Get all relations for an entity."""
        aggregated: List[Tuple[str, str, float]] = []
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT e2.name, r.predicate, r.strength
                FROM relations r
                JOIN entities e1 ON r.subject_id = e1.id
                JOIN entities e2 ON r.object_id = e2.id
                WHERE e1.name = ?
                UNION
                SELECT e1.name, r.predicate, r.strength
                FROM relations r
                JOIN entities e1 ON r.subject_id = e1.id
                JOIN entities e2 ON r.object_id = e2.id
                WHERE e2.name = ?
            """, (entity_name, entity_name))
            
            results = cursor.fetchall()
            aggregated.extend([(obj_name, predicate, strength) for obj_name, predicate, strength in results])

        if self._kg_bridge_enabled and self.kg_db_path:
            try:
                with sqlite3.connect(self.kg_db_path) as conn:
                    cursor = conn.cursor()
                    key = self._canonical_key(entity_name)
                    cursor.execute(
                        """
                        SELECT e2.canonical_name, r.relation_type, r.strength
                        FROM relations r
                        JOIN entities e1 ON r.subject_entity_id = e1.id
                        JOIN entities e2 ON r.object_entity_id = e2.id
                        WHERE e1.name_key = ?
                        UNION
                        SELECT e1.canonical_name, r.relation_type, r.strength
                        FROM relations r
                        JOIN entities e1 ON r.subject_entity_id = e1.id
                        JOIN entities e2 ON r.object_entity_id = e2.id
                        WHERE e2.name_key = ?
                        """,
                        (key, key),
                    )
                    aggregated.extend([(obj_name, predicate, strength) for obj_name, predicate, strength in cursor.fetchall()])
            except Exception:
                pass

        dedup = {}
        for obj_name, predicate, strength in aggregated:
            dedup[(obj_name, predicate)] = max(float(dedup.get((obj_name, predicate), 0.0)), float(strength or 0.0))
        return [(obj, pred, val) for (obj, pred), val in dedup.items()]
    
    def update_entity_access_time(self, entity_name: str) -> bool:
        """Update the last_accessed timestamp for an entity."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "UPDATE entities SET last_accessed = ? WHERE name = ?",
                    (datetime.now().isoformat(), entity_name)
                )
                conn.commit()
                if self._kg_bridge_enabled and self.kg_db_path:
                    try:
                        with sqlite3.connect(self.kg_db_path) as kg_conn:
                            kg_cur = kg_conn.cursor()
                            kg_cur.execute(
                                "UPDATE entities SET access_count = access_count + 1, updated_at = ? WHERE name_key = ?",
                                (datetime.now().isoformat(), self._canonical_key(entity_name)),
                            )
                            kg_conn.commit()
                    except Exception:
                        pass
                return cursor.rowcount > 0
            except sqlite3.Error as e:
                print(f"Error updating access time for {entity_name}: {e}")
                return False
    
    def cleanup_old_entities(self, retention_days: int = 30) -> int:
        """Remove entities that haven't been accessed in retention_days."""
        cutoff_date = datetime.now().timestamp() - (retention_days * 24 * 3600)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            try:
                # Delete old relations first (to maintain referential integrity)
                cursor.execute("""
                    DELETE FROM relations 
                    WHERE subject_id IN (
                        SELECT id FROM entities 
                        WHERE last_accessed IS NOT NULL 
                        AND datetime(last_accessed) < datetime(?, 'unixepoch')
                    )
                    OR object_id IN (
                        SELECT id FROM entities 
                        WHERE last_accessed IS NOT NULL 
                        AND datetime(last_accessed) < datetime(?, 'unixepoch')
                    )
                """, (cutoff_date, cutoff_date))
                
                # Delete old entities
                cursor.execute("""
                    DELETE FROM entities 
                    WHERE last_accessed IS NOT NULL 
                    AND datetime(last_accessed) < datetime(?, 'unixepoch')
                """, (cutoff_date,))
                
                deleted_count = cursor.rowcount
                conn.commit()
                return deleted_count
                
            except sqlite3.Error as e:
                print(f"Error cleaning up old entities: {e}")
                return 0

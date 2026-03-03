"""
Repository pattern implementation for database operations.
Provides a clean abstraction layer between domain models and database.
"""
import sqlite3
import json
from typing import Optional, List, Any, Dict
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime

from domain.models import (
    Page, PageHeader, GenericTable, RawMaterialAndWeighingRecord,
    RawMaterialUsedSteps, ManufacturingInstruction, TemperatureRecord,
    TemperatureRecordTable, WeighingRecord, WeighingRecordTable,
    vaccume_record, VaccumeRecordTable
)


class DatabaseConnection:
    """Manages database connections with context manager support."""
    
    def __init__(self, db_path: str = None):
        """
        Initialize database connection manager.
        
        Args:
            db_path: Path to SQLite database. If None, uses default location.
        """
        if db_path is None:
            data_dir = Path(__file__).parent
            db_path = data_dir / "bpcr_data.db"
        
        self.db_path = str(db_path)
    
    @contextmanager
    def get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable column access by name
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise
        finally:
            conn.close()


class BaseRepository:
    """Base repository with common database operations."""
    
    def __init__(self, db_connection: DatabaseConnection):
        """Initialize repository with database connection."""
        self.db = db_connection
    
    def _execute_query(self, query: str, params: tuple = ()) -> List[sqlite3.Row]:
        """Execute a SELECT query and return results."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchall()
    
    def _execute_insert(self, query: str, params: tuple = ()) -> int:
        """Execute an INSERT query and return last row id."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.lastrowid
    
    def _execute_update(self, query: str, params: tuple = ()) -> int:
        """Execute an UPDATE query and return number of affected rows."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.rowcount
    
    def _execute_delete(self, query: str, params: tuple = ()) -> int:
        """Execute a DELETE query and return number of affected rows."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.rowcount


class DocumentRepository(BaseRepository):
    """Repository for document operations."""
    
    def create(self, document_name: str) -> int:
        """Create a new document record."""
        query = """
            INSERT INTO documents (document_name, created_at, updated_at)
            VALUES (?, ?, ?)
        """
        now = datetime.now().isoformat()
        return self._execute_insert(query, (document_name, now, now))
    
    def get_by_id(self, document_id: int) -> Optional[Dict]:
        """Get document by ID."""
        query = "SELECT * FROM documents WHERE id = ?"
        results = self._execute_query(query, (document_id,))
        return dict(results[0]) if results else None
    
    def get_by_name(self, document_name: str) -> Optional[Dict]:
        """Get document by name."""
        query = "SELECT * FROM documents WHERE document_name = ?"
        results = self._execute_query(query, (document_name,))
        return dict(results[0]) if results else None
    
    def get_or_create(self, document_name: str) -> int:
        """Get existing document or create new one."""
        doc = self.get_by_name(document_name)
        if doc:
            return doc['id']
        return self.create(document_name)
    
    def list_all(self) -> List[Dict]:
        """List all documents."""
        query = "SELECT * FROM documents ORDER BY created_at DESC"
        return [dict(row) for row in self._execute_query(query)]
    
    def delete(self, document_id: int) -> int:
        """Delete a document and all related data (CASCADE)."""
        query = "DELETE FROM documents WHERE id = ?"
        return self._execute_delete(query, (document_id,))


class PageRepository(BaseRepository):
    """Repository for page operations."""
    
    def create(self, page: Page, document_id: int) -> int:
        """Create a new page record."""
        query = """
            INSERT INTO pages (document_id, page_no, raw_text, footer_text, header_text, images)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        return self._execute_insert(query, (
            document_id,
            page.page_no,
            page.raw_text,
            json.dumps(page.footer_text) if page.footer_text else None,
            json.dumps(page.header_text) if page.header_text else None,
            json.dumps(page.images) if page.images else None
        ))
    
    def get_by_id(self, page_id: int) -> Optional[Dict]:
        """Get page by ID."""
        query = "SELECT * FROM pages WHERE id = ?"
        results = self._execute_query(query, (page_id,))
        if not results:
            return None
        
        page = dict(results[0])
        # Parse JSON fields
        if page['footer_text']:
            page['footer_text'] = json.loads(page['footer_text'])
        if page['header_text']:
            page['header_text'] = json.loads(page['header_text'])
        if page['images']:
            page['images'] = json.loads(page['images'])
        return page
    
    def get_by_document(self, document_id: int) -> List[Dict]:
        """Get all pages for a document."""
        query = "SELECT * FROM pages WHERE document_id = ? ORDER BY page_no"
        results = self._execute_query(query, (document_id,))
        pages = []
        for row in results:
            page = dict(row)
            # Parse JSON fields
            if page['footer_text']:
                page['footer_text'] = json.loads(page['footer_text'])
            if page['header_text']:
                page['header_text'] = json.loads(page['header_text'])
            if page['images']:
                page['images'] = json.loads(page['images'])
            pages.append(page)
        return pages


class PageHeaderRepository(BaseRepository):
    """Repository for page header operations."""
    
    def create(self, header: PageHeader, page_id: int) -> int:
        """Create a new page header record."""
        query = """
            INSERT INTO page_headers (page_id, page_type, product_name, bpcr_number, batch_number)
            VALUES (?, ?, ?, ?, ?)
        """
        return self._execute_insert(query, (
            page_id,
            header.page_type,
            header.product_name,
            header.bpcr_number,
            header.batch_number
        ))
    
    def get_by_page_id(self, page_id: int) -> Optional[Dict]:
        """Get page header by page ID."""
        query = "SELECT * FROM page_headers WHERE page_id = ?"
        results = self._execute_query(query, (page_id,))
        return dict(results[0]) if results else None
    
    def get_by_bpcr_number(self, bpcr_number: str) -> List[Dict]:
        """Get all page headers with specific BPCR number."""
        query = "SELECT * FROM page_headers WHERE bpcr_number = ?"
        return [dict(row) for row in self._execute_query(query, (bpcr_number,))]


class RawMaterialRepository(BaseRepository):
    """Repository for raw material operations."""
    
    def create_with_steps(self, material: RawMaterialAndWeighingRecord, page_id: int) -> int:
        """Create raw material record with its usage steps."""
        # Insert raw material
        query = """
            INSERT INTO raw_materials (page_id, s_no, raw_material_name_item_code, uom)
            VALUES (?, ?, ?, ?)
        """
        material_id = self._execute_insert(query, (
            page_id,
            material.s_no,
            material.raw_material_name_item_code,
            material.uom
        ))
        
        # Insert usage steps
        step_query = """
            INSERT INTO raw_material_steps 
            (raw_material_id, used_step_no, batch_no, standard_quantity, gross_quantity, 
             net_quantity, tare_quantity, operator_attested, reviewer_attested)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        
        for step in material.used_in_steps:
            self._execute_insert(step_query, (
                material_id,
                step.used_step_no,
                step.batch_no,
                step.standard_quantity,
                step.gross_quantity,
                step.net_quantity,
                step.tare_quantity,
                step.signatures.is_operator_attested,
                step.signatures.is_reviewer_attested
            ))
        
        return material_id
    
    def get_by_page_id(self, page_id: int) -> List[Dict]:
        """Get all raw materials for a page with their steps."""
        query = """
            SELECT rm.*, 
                   rms.id as step_id, rms.used_step_no, rms.batch_no, 
                   rms.standard_quantity, rms.gross_quantity, rms.net_quantity, 
                   rms.tare_quantity, rms.operator_attested, rms.reviewer_attested
            FROM raw_materials rm
            LEFT JOIN raw_material_steps rms ON rm.id = rms.raw_material_id
            WHERE rm.page_id = ?
            ORDER BY rm.s_no, rms.used_step_no
        """
        results = self._execute_query(query, (page_id,))
        
        # Group by raw material
        materials = {}
        for row in results:
            row_dict = dict(row)
            mat_id = row_dict['id']
            
            if mat_id not in materials:
                materials[mat_id] = {
                    'id': mat_id,
                    's_no': row_dict['s_no'],
                    'raw_material_name_item_code': row_dict['raw_material_name_item_code'],
                    'uom': row_dict['uom'],
                    'steps': []
                }
            
            if row_dict['step_id']:
                materials[mat_id]['steps'].append({
                    'used_step_no': row_dict['used_step_no'],
                    'batch_no': row_dict['batch_no'],
                    'standard_quantity': row_dict['standard_quantity'],
                    'gross_quantity': row_dict['gross_quantity'],
                    'net_quantity': row_dict['net_quantity'],
                    'tare_quantity': row_dict['tare_quantity'],
                    'operator_attested': bool(row_dict['operator_attested']),
                    'reviewer_attested': bool(row_dict['reviewer_attested'])
                })
        
        return list(materials.values())


class ManufacturingInstructionRepository(BaseRepository):
    """Repository for manufacturing instruction operations."""
    
    def create(self, instruction: ManufacturingInstruction, page_id: int) -> int:
        """Create a new manufacturing instruction record."""
        query = """
            INSERT INTO manufacturing_instructions 
            (page_id, step_no, sub_step_no, is_critical_step, operation, equipment_used,
             start_time, end_time, duration, remarks, operator_attested, reviewer_attested)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        return self._execute_insert(query, (
            page_id,
            instruction.step_no,
            instruction.sub_step_no,
            instruction.is_critical_step,
            instruction.operation,
            instruction.equipment_used,
            instruction.start_time,
            instruction.end_time,
            instruction.duration,
            instruction.remarks,
            instruction.signatures.is_operator_attested,
            instruction.signatures.is_reviewer_attested
        ))
    
    def get_by_page_id(self, page_id: int) -> List[Dict]:
        """Get all manufacturing instructions for a page."""
        query = """
            SELECT * FROM manufacturing_instructions 
            WHERE page_id = ? 
            ORDER BY step_no
        """
        return [dict(row) for row in self._execute_query(query, (page_id,))]
    
    def get_critical_steps(self, page_id: int) -> List[Dict]:
        """Get only critical manufacturing steps for a page."""
        query = """
            SELECT * FROM manufacturing_instructions 
            WHERE page_id = ? AND is_critical_step = 1
            ORDER BY step_no
        """
        return [dict(row) for row in self._execute_query(query, (page_id,))]


# Additional repository classes can be added as needed
class RepositoryFactory:
    """Factory to create repository instances with shared database connection."""
    
    def __init__(self, db_path: str = None):
        """Initialize factory with database connection."""
        self.db_connection = DatabaseConnection(db_path)
    
    @property
    def documents(self) -> DocumentRepository:
        """Get document repository."""
        return DocumentRepository(self.db_connection)
    
    @property
    def pages(self) -> PageRepository:
        """Get page repository."""
        return PageRepository(self.db_connection)
    
    @property
    def page_headers(self) -> PageHeaderRepository:
        """Get page header repository."""
        return PageHeaderRepository(self.db_connection)
    
    @property
    def raw_materials(self) -> RawMaterialRepository:
        """Get raw material repository."""
        return RawMaterialRepository(self.db_connection)
    
    @property
    def manufacturing_instructions(self) -> ManufacturingInstructionRepository:
        """Get manufacturing instruction repository."""
        return ManufacturingInstructionRepository(self.db_connection)


# Example usage
if __name__ == "__main__":
    # Initialize repository factory
    factory = RepositoryFactory()
    
    # Example: Create a document
    doc_id = factory.documents.get_or_create("test-document.pdf")
    print(f"Document ID: {doc_id}")
    
    # Example: List all documents
    docs = factory.documents.list_all()
    print(f"Total documents: {len(docs)}")
    for doc in docs:
        print(f"  - {doc['document_name']} (ID: {doc['id']})")

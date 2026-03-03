"""
Database initialization script for SQLite database.
Reads schema from schema.sql and creates all tables.
"""
import sqlite3
import os
from pathlib import Path


class DatabaseInitializer:
    """Handles SQLite database initialization."""
    
    def __init__(self, db_path: str = None):
        """
        Initialize the database initializer.
        
        Args:
            db_path: Path to the SQLite database file. 
                    If None, uses 'bpcr_data.db' in the data folder.
        """
        if db_path is None:
            data_dir = Path(__file__).parent
            db_path = data_dir / "bpcr_data.db"
        
        self.db_path = Path(db_path)
        self.schema_path = Path(__file__).parent / "schema.sql"
        
    def initialize_database(self, force_recreate: bool = False) -> None:
        """
        Initialize the database by creating all tables from schema file.
        
        Args:
            force_recreate: If True, drops existing database and recreates it.
        """
        if force_recreate and self.db_path.exists():
            print(f"Removing existing database: {self.db_path}")
            self.db_path.unlink()
        
        # Read schema file
        if not self.schema_path.exists():
            raise FileNotFoundError(f"Schema file not found: {self.schema_path}")
        
        with open(self.schema_path, 'r', encoding='utf-8') as f:
            schema_sql = f.read()
        
        # Create database and execute schema
        print(f"Initializing database: {self.db_path}")
        conn = sqlite3.connect(self.db_path)
        
        try:
            cursor = conn.cursor()
            cursor.executescript(schema_sql)
            conn.commit()
            print("Database initialized successfully!")
            
            # Print table names
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = cursor.fetchall()
            print(f"\nCreated {len(tables)} tables:")
            for table in tables:
                print(f"  - {table[0]}")
                
        except sqlite3.Error as e:
            print(f"Error initializing database: {e}")
            raise
        finally:
            conn.close()
    
    def verify_database(self) -> bool:
        """
        Verify that the database exists and has the expected tables.
        
        Returns:
            True if database is valid, False otherwise.
        """
        if not self.db_path.exists():
            print(f"Database not found: {self.db_path}")
            return False
        
        expected_tables = [
            'documents', 'pages', 'page_headers', 'generic_tables',
            'raw_materials', 'raw_material_steps', 'manufacturing_instructions',
            'temperature_records', 'weighing_records', 'vacuum_records'
        ]
        
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            existing_tables = [row[0] for row in cursor.fetchall()]
            
            missing_tables = set(expected_tables) - set(existing_tables)
            
            if missing_tables:
                print(f"Missing tables: {missing_tables}")
                return False
            
            print(f"Database verification successful. Found {len(existing_tables)} tables.")
            return True
            
        except sqlite3.Error as e:
            print(f"Error verifying database: {e}")
            return False
        finally:
            conn.close()


def main():
    """Main entry point for database initialization."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Initialize SQLite database for BPCR document processing')
    parser.add_argument(
        '--db-path',
        type=str,
        help='Path to the SQLite database file (default: data/bpcr_data.db)'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Force recreate database (drops existing database)'
    )
    parser.add_argument(
        '--verify',
        action='store_true',
        help='Only verify database without creating'
    )
    
    args = parser.parse_args()
    
    initializer = DatabaseInitializer(args.db_path)
    
    if args.verify:
        if initializer.verify_database():
            print("\n✓ Database is valid and ready to use")
        else:
            print("\n✗ Database verification failed")
            exit(1)
    else:
        initializer.initialize_database(force_recreate=args.force)
        initializer.verify_database()


if __name__ == "__main__":
    main()

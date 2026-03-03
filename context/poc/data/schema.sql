-- SQLite Schema for BPCR Document Processing

-- Documents table
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Pages table
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    page_no INTEGER NOT NULL,
    raw_text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
    UNIQUE(document_id, page_no)
);

-- Page headers table
CREATE TABLE IF NOT EXISTS page_headers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL,
    page_type TEXT NOT NULL,
    product_name TEXT,
    bpcr_number TEXT,
    batch_number TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
);

-- Generic tables extracted from pages
CREATE TABLE IF NOT EXISTS generic_tables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL,
    table_name TEXT NOT NULL,
    headers TEXT NOT NULL,  -- JSON array
    values TEXT NOT NULL,  -- JSON array of arrays
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
);

-- Raw materials table
CREATE TABLE IF NOT EXISTS raw_materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL,
    s_no INTEGER NOT NULL,
    raw_material_name_item_code TEXT NOT NULL,
    uom TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
);

-- Raw material usage steps
CREATE TABLE IF NOT EXISTS raw_material_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_material_id INTEGER NOT NULL,
    used_step_no TEXT NOT NULL,
    batch_no TEXT NOT NULL,
    standard_quantity TEXT NOT NULL,
    gross_quantity TEXT,
    net_quantity TEXT,
    tare_quantity TEXT,
    operator_attested BOOLEAN NOT NULL DEFAULT 0,
    reviewer_attested BOOLEAN NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (raw_material_id) REFERENCES raw_materials(id) ON DELETE CASCADE
);

-- Manufacturing instructions table
CREATE TABLE IF NOT EXISTS manufacturing_instructions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL,
    step_no TEXT NOT NULL,
    sub_step_no TEXT,
    is_critical_step BOOLEAN NOT NULL DEFAULT 0,
    operation TEXT NOT NULL,
    equipment_used TEXT,
    start_time TEXT,
    end_time TEXT,
    duration TEXT,
    remarks TEXT,
    operator_attested BOOLEAN NOT NULL DEFAULT 0,
    reviewer_attested BOOLEAN NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
);

-- Temperature records table
CREATE TABLE IF NOT EXISTS temperature_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL,
    previous_step TEXT NOT NULL,
    time TEXT NOT NULL,
    temperature TEXT NOT NULL,
    initials TEXT NOT NULL,
    distillate_vol TEXT,
    b_no TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
);

-- Weighing records table
CREATE TABLE IF NOT EXISTS weighing_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL,
    previous_step TEXT NOT NULL,
    container_no TEXT NOT NULL,
    tare_weight TEXT NOT NULL,
    gross_weight TEXT NOT NULL,
    net_weight TEXT NOT NULL,
    total_weight TEXT,
    weighing_balance_id TEXT,
    operator_attested BOOLEAN NOT NULL DEFAULT 0,
    reviewer_attested BOOLEAN NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
);

-- Vacuum records table
CREATE TABLE IF NOT EXISTS vacuum_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL,
    previous_step TEXT NOT NULL,
    time TEXT NOT NULL,
    temperature TEXT NOT NULL,
    vacuum TEXT NOT NULL,
    initials TEXT NOT NULL,
    distillate_vol TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
);

-- Create indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_pages_document_id ON pages(document_id);
CREATE INDEX IF NOT EXISTS idx_pages_page_no ON pages(page_no);
CREATE INDEX IF NOT EXISTS idx_page_headers_page_id ON page_headers(page_id);
CREATE INDEX IF NOT EXISTS idx_page_headers_bpcr_number ON page_headers(bpcr_number);
CREATE INDEX IF NOT EXISTS idx_generic_tables_page_id ON generic_tables(page_id);
CREATE INDEX IF NOT EXISTS idx_raw_materials_page_id ON raw_materials(page_id);
CREATE INDEX IF NOT EXISTS idx_raw_material_steps_raw_material_id ON raw_material_steps(raw_material_id);
CREATE INDEX IF NOT EXISTS idx_manufacturing_instructions_page_id ON manufacturing_instructions(page_id);
CREATE INDEX IF NOT EXISTS idx_temperature_records_page_id ON temperature_records(page_id);
CREATE INDEX IF NOT EXISTS idx_weighing_records_page_id ON weighing_records(page_id);
CREATE INDEX IF NOT EXISTS idx_vacuum_records_page_id ON vacuum_records(page_id);

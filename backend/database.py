import os
import re
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, Text, Float, Numeric, DateTime, JSON, Boolean, ForeignKey, text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/invoice_ocr")

# Normalize the legacy "postgres://" scheme — SQLAlchemy 2.x only loads the
# dialect for "postgresql://".
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]

Base = declarative_base()


class InvoiceTemplate(Base):
    """
    Stores learned invoice layout formats and dynamic extraction rules (not hardcoded values).
    """
    __tablename__ = "invoice_templates"

    id = Column(Integer, primary_key=True, index=True)
    template_name = Column(String(255), nullable=False)           # e.g. "Two-Column European VAT Template"
    format_type = Column(String(50), default="STANDARD_INVOICE")  # e.g. "STANDARD_INVOICE", "GST_TAX_INVOICE"
    status = Column(String(50), default="DRAFT", index=True)      # DRAFT, ACTIVE, DEPRECATED, QUARANTINED
    
    # Versioning & Lineage
    version = Column(Integer, default=1)
    vendor_key = Column(String(255), nullable=True, index=True)
    template_family_key = Column(String(255), nullable=True, index=True)
    supersedes_id = Column(Integer, nullable=True)

    # Structural Fingerprint
    layout_hash = Column(String(255), nullable=True, index=True)
    anchor_keywords = Column(JSON, nullable=False)                # ["TAX INVOICE", "DESCRIPTION OF GOODS", ...]
    header_geometry = Column(JSON, nullable=True)                 # { headerZone, itemsZone, summaryZone }
    column_boundaries = Column(JSON, nullable=True)               # { description: [0.07, 0.42], ... }
    row_detection_rules = Column(JSON, nullable=True)             # { strategy: "sno_and_amount", ... }
    extraction_rules = Column(JSON, nullable=False)               # { "mid_x_ratio": 0.48, "table_start": 0.32 }
    table_columns = Column(JSON, nullable=False)                  # ["sno", "description", "qty", "rate", "amount"]
    known_vendors = Column(JSON, default=list)                    # ["Hall-Boyd", "Acme Corp"]
    sample_preview_url = Column(Text, nullable=True)

    # Reliability & Confidence Tracking
    hit_count = Column(Integer, default=1)
    success_count = Column(Integer, default=0)
    failure_count = Column(Integer, default=0)
    avg_confidence = Column(Float, nullable=True)
    last_matched_at = Column(DateTime, nullable=True)
    last_failed_at = Column(DateTime, nullable=True)

    is_verified = Column(Boolean, default=False)
    verified_by = Column(String(255), nullable=True)
    verified_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    extraction_logs = relationship("ExtractionLog", back_populates="template", foreign_keys="[ExtractionLog.template_id]")


class ExtractionLog(Base):
    """
    Audit log & invoice document record of extractions in PostgreSQL.
    """
    __tablename__ = "extraction_logs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(100), nullable=True, index=True)
    template_id = Column(Integer, ForeignKey("invoice_templates.id"), nullable=True, index=True)
    match_strategy = Column(String(50), default="AI_FALLBACK")    # EXACT_HASH, ANCHOR_KEYWORDS, AI_FALLBACK
    layout_match_score = Column(Float, nullable=True)
    status = Column(String(50), default="NEEDS_REVIEW", index=True) # SUCCESS, PARTIAL, FAILED, NEEDS_REVIEW

    invoice_number = Column(String(100), nullable=True, index=True)
    invoice_date = Column(DateTime, nullable=True)
    format_type = Column(String(50), nullable=True)
    vendor_name = Column(String(255), nullable=True)
    vendor_gstin = Column(String(50), nullable=True, index=True)
    buyer_name = Column(String(255), nullable=True)
    buyer_gstin = Column(String(50), nullable=True, index=True)

    engine_used = Column(String(100), nullable=False)
    engine_version = Column(String(50), nullable=True)
    processing_time_ms = Column(Integer, default=0)

    overall_confidence = Column(Float, nullable=True)
    field_confidence = Column(JSON, nullable=True)
    low_confidence_fields = Column(JSON, nullable=True)

    extracted_data = Column(JSON, nullable=True)
    normalized_data = Column(JSON, nullable=True)
    raw_ocr_text = Column(Text, nullable=True)

    grand_total = Column(Numeric(18, 2), nullable=True)
    taxable_value = Column(Numeric(18, 2), nullable=True)
    total_tax = Column(Numeric(18, 2), nullable=True)
    currency = Column(String(10), default="INR")

    validation_passed = Column(Boolean, nullable=True)
    validation_errors = Column(JSON, nullable=True)

    is_duplicate = Column(Boolean, default=False)
    duplicate_of_id = Column(Integer, ForeignKey("extraction_logs.id"), nullable=True, index=True)

    source_file_url = Column(Text, nullable=True)
    source_file_hash = Column(String(255), nullable=True, index=True)
    original_filename = Column(String(255), nullable=True)
    source_mime_type = Column(String(100), nullable=True)
    source_page_count = Column(Integer, nullable=True)
    file_size_bytes = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    template = relationship("InvoiceTemplate", back_populates="extraction_logs")
    duplicate_of = relationship("ExtractionLog", remote_side=[id], backref="duplicates")


class ExtractionReview(Base):
    """
    Human-in-the-loop audit and correction log.
    """
    __tablename__ = "extraction_reviews"

    id = Column(Integer, primary_key=True, index=True)
    extraction_log_id = Column(Integer, nullable=False, index=True)
    action = Column(String(50), nullable=False)                    # APPROVED, CORRECTED, REJECTED
    corrected_fields = Column(JSON, nullable=True)
    reviewer_note = Column(Text, nullable=True)
    reviewed_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


engine = None
SessionLocal = None


def auto_create_pg_database():
    """
    Connects to default 'postgres' database and creates 'invoice_ocr' database if it doesn't exist yet.
    """
    try:
        match = re.match(r"(postgresql://[^/]+/)([^?]+)", DATABASE_URL)
        if match:
            base_url, db_name = match.group(1), match.group(2)
            default_pg_url = base_url + "postgres"
            
            temp_engine = create_engine(default_pg_url, isolation_level="AUTOCOMMIT")
            with temp_engine.connect() as conn:
                res = conn.execute(text(f"SELECT 1 FROM pg_database WHERE datname='{db_name}'"))
                if not res.scalar():
                    conn.execute(text(f'CREATE DATABASE "{db_name}"'))
                    print(f"✨ [PostgreSQL] Automatically created database: '{db_name}'")
            temp_engine.dispose()
    except Exception as e:
        print(f"Auto DB create check: {e}")


def run_schema_migrations(target_engine):
    """
    Ensures newly added columns exist in PostgreSQL without requiring manual migrations.
    """
    migration_statements = [
        # invoice_templates columns
        "ALTER TABLE invoice_templates ADD COLUMN IF NOT EXISTS status VARCHAR(50) DEFAULT 'DRAFT';",
        "ALTER TABLE invoice_templates ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1;",
        "ALTER TABLE invoice_templates ADD COLUMN IF NOT EXISTS vendor_key VARCHAR(255);",
        "ALTER TABLE invoice_templates ADD COLUMN IF NOT EXISTS template_family_key VARCHAR(255);",
        "ALTER TABLE invoice_templates ADD COLUMN IF NOT EXISTS supersedes_id INTEGER;",
        "ALTER TABLE invoice_templates ADD COLUMN IF NOT EXISTS layout_hash VARCHAR(255);",
        "ALTER TABLE invoice_templates ADD COLUMN IF NOT EXISTS header_geometry JSONB;",
        "ALTER TABLE invoice_templates ADD COLUMN IF NOT EXISTS column_boundaries JSONB;",
        "ALTER TABLE invoice_templates ADD COLUMN IF NOT EXISTS row_detection_rules JSONB;",
        "ALTER TABLE extraction_logs ALTER COLUMN match_strategy TYPE VARCHAR(50) USING match_strategy::text;",
        "ALTER TABLE invoice_templates ADD COLUMN IF NOT EXISTS success_count INTEGER DEFAULT 0;",
        "ALTER TABLE invoice_templates ADD COLUMN IF NOT EXISTS failure_count INTEGER DEFAULT 0;",
        "ALTER TABLE invoice_templates ADD COLUMN IF NOT EXISTS avg_confidence DOUBLE PRECISION;",
        "ALTER TABLE invoice_templates ADD COLUMN IF NOT EXISTS last_matched_at TIMESTAMP;",
        "ALTER TABLE invoice_templates ADD COLUMN IF NOT EXISTS last_failed_at TIMESTAMP;",
        "ALTER TABLE invoice_templates ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE;",
        "ALTER TABLE invoice_templates ADD COLUMN IF NOT EXISTS verified_by VARCHAR(255);",
        "ALTER TABLE invoice_templates ADD COLUMN IF NOT EXISTS verified_at TIMESTAMP;",
        "ALTER TABLE invoice_templates ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();",

        # extraction_logs columns
        "ALTER TABLE extraction_logs ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(100);",
        "ALTER TABLE extraction_logs ADD COLUMN IF NOT EXISTS buyer_name VARCHAR(255);",
        "ALTER TABLE extraction_logs ADD COLUMN IF NOT EXISTS buyer_gstin VARCHAR(50);",
        "ALTER TABLE extraction_logs ADD COLUMN IF NOT EXISTS field_confidence JSONB;",
        "ALTER TABLE extraction_logs ADD COLUMN IF NOT EXISTS low_confidence_fields JSONB;",
        "ALTER TABLE extraction_logs ADD COLUMN IF NOT EXISTS normalized_data JSONB;",
        "ALTER TABLE extraction_logs ADD COLUMN IF NOT EXISTS validation_passed BOOLEAN;",
        "ALTER TABLE extraction_logs ADD COLUMN IF NOT EXISTS validation_errors JSONB;",
        "ALTER TABLE extraction_logs ADD COLUMN IF NOT EXISTS is_duplicate BOOLEAN DEFAULT FALSE;",
        "ALTER TABLE extraction_logs ADD COLUMN IF NOT EXISTS duplicate_of_id INTEGER;",
        "ALTER TABLE extraction_logs ADD COLUMN IF NOT EXISTS source_file_hash VARCHAR(255);",
        "ALTER TABLE extraction_logs ADD COLUMN IF NOT EXISTS original_filename VARCHAR(255);",
        "ALTER TABLE extraction_logs ADD COLUMN IF NOT EXISTS source_mime_type VARCHAR(100);",
        "ALTER TABLE extraction_logs ADD COLUMN IF NOT EXISTS source_page_count INTEGER;",
        "ALTER TABLE extraction_logs ADD COLUMN IF NOT EXISTS file_size_bytes INTEGER;",
        "ALTER TABLE extraction_logs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();",
    ]
    try:
        with target_engine.connect() as conn:
            for stmt in migration_statements:
                try:
                    conn.execute(text(stmt))
                except Exception:
                    pass
            conn.commit()
    except Exception as e:
        print(f"Migration notice: {e}")


def init_db():
    global engine, SessionLocal
    auto_create_pg_database()
    try:
        engine = create_engine(
            DATABASE_URL,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20
        )
        Base.metadata.create_all(bind=engine)
        run_schema_migrations(engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        print(f"✅ Successfully connected to PostgreSQL: {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL}")
        return True, "Connected to PostgreSQL"
    except Exception as e:
        print(f"⚠️ PostgreSQL connection warning: {e}")
        sqlite_url = "sqlite:///./templates.db"
        engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        return False, f"PostgreSQL unavailable ({str(e)}). Running on local backup DB."


def get_db():
    global SessionLocal
    if SessionLocal is None:
        init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


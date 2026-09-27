import pandas as pd
import json
import re
import docx
import pdfplumber
from pathlib import Path

def parse_csv(file_or_path) -> pd.DataFrame:
    """Read CSV with pandas, handle encoding issues."""
    encodings = ['utf-8', 'latin-1', 'cp1252']
    
    for enc in encodings:
        try:
            # If it's a file-like object, reset pointer
            if hasattr(file_or_path, 'seek'):
                file_or_path.seek(0)
            return pd.read_csv(file_or_path, encoding=enc)
        except UnicodeDecodeError:
            continue
        except Exception as e:
            raise ValueError(f"Error parsing CSV: {e}")
            
    raise ValueError("Could not decode CSV file with provided encodings.")

def parse_excel(file_or_path) -> pd.DataFrame:
    """Read Excel with openpyxl engine."""
    try:
        if hasattr(file_or_path, 'seek'):
            file_or_path.seek(0)
        return pd.read_excel(file_or_path, engine='openpyxl')
    except Exception as e:
        raise ValueError(f"Error parsing Excel: {e}")

def parse_json(file_or_path) -> pd.DataFrame:
    """Handle flat arrays and nested JSON."""
    try:
        if hasattr(file_or_path, 'seek'):
            file_or_path.seek(0)
            data = json.load(file_or_path)
        else:
            with open(file_or_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
        # If it's a list, just normalize it
        if isinstance(data, list):
            return pd.json_normalize(data)
        elif isinstance(data, dict):
            # Attempt to find the key that holds the list
            for key, val in data.items():
                if isinstance(val, list):
                    return pd.json_normalize(val)
            # If no list found, normalize the dict itself
            return pd.json_normalize([data])
        else:
            raise ValueError("Unsupported JSON structure.")
    except Exception as e:
        raise ValueError(f"Error parsing JSON: {e}")

def _extract_references_and_dates(text: str) -> dict:
    """Helper to extract referenced IDs and basic dates."""
    id_pattern = r'\b(?:INC|ALT|CASE|ALERT)-\d+\b'
    date_pattern = r'\b\d{4}-\d{2}-\d{2}\b' # simplistic YYYY-MM-DD
    # simplistic analyst names finding not fully possible with regex, skip or fake
    
    referenced_ids = list(set(re.findall(id_pattern, text)))
    dates_found = list(set(re.findall(date_pattern, text)))
    
    return {
        "referenced_ids": referenced_ids,
        "dates_found": dates_found,
        "analyst_names_found": []
    }

def parse_docx(file_or_path) -> dict:
    """Extract text from .docx using python-docx."""
    try:
        if hasattr(file_or_path, 'seek'):
            file_or_path.seek(0)
        
        doc = docx.Document(file_or_path)
        full_text = "\n".join([para.text for para in doc.paragraphs])
        
        extracted = _extract_references_and_dates(full_text)
        extracted["full_text"] = full_text
        
        return extracted
    except Exception as e:
        raise ValueError(f"Error parsing DOCX: {e}")

def parse_pdf(file_or_path) -> dict:
    """Extract text using pdfplumber. Also extract tables if present."""
    try:
        if hasattr(file_or_path, 'seek'):
            file_or_path.seek(0)
            
        full_text = ""
        tables = []
        
        with pdfplumber.open(file_or_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    full_text += page_text + "\n"
                    
                page_tables = page.extract_tables()
                for table in page_tables:
                    # Convert to dataframe
                    # Ensure all rows have same length by padding
                    if not table:
                        continue
                    max_cols = max(len(row) for row in table if row)
                    padded_table = [row + [''] * (max_cols - len(row)) if row else [''] * max_cols for row in table]
                    
                    df = pd.DataFrame(padded_table[1:], columns=padded_table[0]) if len(padded_table) > 1 else pd.DataFrame(padded_table)
                    tables.append(df)
                    
        extracted = _extract_references_and_dates(full_text)
        extracted["full_text"] = full_text
        extracted["tables"] = tables
        
        return extracted
    except Exception as e:
        raise ValueError(f"Error parsing PDF: {e}")

def detect_file_type(filename: str) -> str:
    """Return file type based on extension."""
    ext = Path(filename).suffix.lower()
    
    mapping = {
        '.csv': 'csv',
        '.xlsx': 'excel',
        '.xls': 'excel',
        '.json': 'json',
        '.docx': 'docx',
        '.pdf': 'pdf'
    }
    
    return mapping.get(ext, 'unknown')

def parse_file(file_or_path, filename: str):
    """Auto-detect and parse."""
    file_type = detect_file_type(filename)
    
    if file_type == 'csv':
        return parse_csv(file_or_path)
    elif file_type == 'excel':
        return parse_excel(file_or_path)
    elif file_type == 'json':
        return parse_json(file_or_path)
    elif file_type == 'docx':
        return parse_docx(file_or_path)
    elif file_type == 'pdf':
        return parse_pdf(file_or_path)
    else:
        raise ValueError(f"Unsupported file type for {filename}")

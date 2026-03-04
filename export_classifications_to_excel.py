#!/usr/bin/env python3
"""
Export email classifications from LangGraph to Excel.
"""

import httpx
import json
from datetime import datetime
from pathlib import Path
import pandas as pd
from typing import List, Dict, Any


def get_all_threads(api_url: str = "http://localhost:8123") -> List[Dict[str, Any]]:
    """Fetch all processed threads from LangGraph API."""
    try:
        response = httpx.post(
            f"{api_url}/threads/search",
            json={},
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching threads: {e}")
        return []


def extract_email_data(thread: Dict[str, Any]) -> Dict[str, Any]:
    """Extract email and classification data from a thread."""
    values = thread.get("values") or {}
    classification = values.get("classification") or {}
    
    return {
        "Thread ID": thread.get("thread_id", ""),
        "Created At": thread.get("created_at", ""),
        "Email ID": values.get("email_id", "")[:50] + "..." if values.get("email_id") else "",
        "Sender": values.get("sender", ""),
        "Email Subject": extract_subject(values.get("email_content", "")),
        "Action": classification.get("action", ""),
        "Company Name": classification.get("company_name", ""),
        "Company Type": classification.get("company_type", ""),
        "Contact Name": classification.get("contact_name", ""),
        "Contact Last Name": classification.get("contact_last_name", ""),
        "Contact Email": classification.get("email", ""),
        "Salesperson": classification.get("salesperson", ""),
        "Confidence": classification.get("confidence", 0),
        "Date of Contact": classification.get("date_of_contact", ""),
        "Operation Countries": ", ".join(classification.get("operation_countries", [])),
        "Company Presence": ", ".join(classification.get("company_presence", [])),
        "Current Projects": ", ".join(classification.get("current_projects", [])),
        "Source": classification.get("source", "")[:100],  # Truncate long source
        "Status": values.get("status", ""),
    }


def extract_subject(email_content: str) -> str:
    """Extract subject line from email content."""
    if email_content.startswith("Subject:"):
        end_idx = email_content.find("\n\n")
        if end_idx != -1:
            subject = email_content[8:end_idx].strip()
            return subject[:100]  # Truncate if too long
    return ""


def create_excel_file(
    threads: List[Dict[str, Any]],
    output_file: str = "email_classifications.xlsx",
) -> str:
    """Create Excel file with email classifications."""
    if not threads:
        print("No threads found to export")
        return ""
    
    # Extract data from all threads
    data = [extract_email_data(thread) for thread in threads]
    
    # Create DataFrame
    df = pd.DataFrame(data)
    
    # Convert timezone-aware datetime to timezone-naive
    if "Created At" in df.columns:
        df["Created At"] = pd.to_datetime(df["Created At"], utc=True).dt.tz_localize(None)
        df = df.sort_values("Created At", ascending=False)
    
    # Create Excel file with formatting
    output_path = Path(output_file)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Classifications", index=False)
        
        # Get the workbook and worksheet
        workbook = writer.book
        worksheet = writer.sheets["Classifications"]
        
        # Auto-adjust column widths
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)  # Cap at 50
            worksheet.column_dimensions[column_letter].width = adjusted_width
        
        # Freeze header row
        worksheet.freeze_panes = "A2"
    
    return str(output_path)


def main():
    """Main function."""
    print("Fetching email classifications from LangGraph API...")
    threads = get_all_threads()
    
    if not threads:
        print("No threads found")
        return
    
    print(f"Found {len(threads)} processed emails")
    
    # Use a single Excel file instead of timestamped files
    output_dir = Path("./output")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = str(output_dir / "email_classifications.xlsx")
    
    excel_path = create_excel_file(threads, output_file)
    
    if excel_path:
        print(f"✅ Excel file created: {excel_path}")
        print(f"   Location: {Path(excel_path).absolute()}")
    else:
        print("❌ Failed to create Excel file")


if __name__ == "__main__":
    main()

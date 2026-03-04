"""
Module for handling Excel operations for email classifications.
Automatically appends processed emails to an Excel file.
"""

import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
from openpyxl import load_workbook


class EmailClassificationExcelTracker:
    """Handles appending email classifications to an Excel file."""
    
    def __init__(
        self,
        excel_file: Optional[str] = None,
        sheet_name: str = "Classifications"
    ):
        if excel_file is None:
            # Write to output subdirectory
            # In Docker: /app/output maps to workspace root, so use /app/output/output
            # Locally: use ./output
            if Path("/app").exists():
                base_dir = Path("/app/output/output")
            else:
                base_dir = Path("./output")
            excel_file = str(base_dir / "email_classifications.xlsx")
        self.excel_file = Path(excel_file)
        self.sheet_name = sheet_name
        self.columns = [
            "Thread ID",
            "Created At",
            "Email ID",
            "Sender",
            "Email Subject",
            "Action",
            "Company Name",
            "Company Type",
            "Contact Name",
            "Contact Last Name",
            "Contact Email",
            "Salesperson",
            "Confidence",
            "Date of Contact",
            "Operation Countries",
            "Company Presence",
            "Current Projects",
            "Source",
            "Status",
        ]
        self._ensure_file_exists()
    
    def _ensure_file_exists(self):
        """Create the Excel file with headers if it doesn't exist."""
        # Ensure parent directory exists
        self.excel_file.parent.mkdir(parents=True, exist_ok=True)
        
        if not self.excel_file.exists():
            df = pd.DataFrame(columns=self.columns)
            df.to_excel(self.excel_file, sheet_name=self.sheet_name, index=False)
    
    def append_email(
        self,
        thread_id: str,
        created_at: str,
        email_id: str,
        sender: str,
        email_content: str,
        classification: Dict[str, Any],
        status: str,
    ) -> bool:
        """
        Append a processed email to the Excel file.
        
        Args:
            thread_id: LangGraph thread ID
            created_at: When the email was processed
            email_id: Email message ID
            sender: Email sender
            email_content: Full email content
            classification: Classification result dictionary
            status: Processing status
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Extract subject from email content
            subject = self._extract_subject(email_content)
            
            # Prepare row data
            row_data = {
                "Thread ID": thread_id,
                "Created At": created_at,
                "Email ID": email_id[:50] + "..." if email_id and len(email_id) > 50 else email_id,
                "Sender": sender or "",
                "Email Subject": subject,
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
                "Source": classification.get("source", "")[:100] if classification.get("source") else "",
                "Status": status,
            }
            
            # Read existing data (handle missing/corrupted/empty sheet gracefully)
            try:
                df = pd.read_excel(self.excel_file, sheet_name=self.sheet_name)
            except Exception:
                df = pd.DataFrame(columns=self.columns)
            
            # Ensure expected schema exists and preserve column order
            for column in self.columns:
                if column not in df.columns:
                    df[column] = ""
            df = df[self.columns]
            
            # Backward compatibility: remove legacy placeholder first row if it is fully empty
            if not df.empty and df.iloc[0].fillna("").astype(str).str.strip().eq("").all():
                df = df.iloc[1:].reset_index(drop=True)
            
            # Append new row
            new_row = pd.DataFrame([row_data])
            df = pd.concat([df, new_row], ignore_index=True)
            
            # Convert datetime columns to timezone-naive
            if "Created At" in df.columns:
                parsed_created_at = pd.Series(
                    pd.to_datetime(df["Created At"], utc=True, errors="coerce"),
                    index=df.index,
                )
                df["Created At"] = parsed_created_at.apply(
                    lambda value: value.tz_localize(None) if pd.notna(value) else value
                )
            
            # Write back to Excel
            df.to_excel(self.excel_file, sheet_name=self.sheet_name, index=False)
            
            # Auto-adjust column widths
            self._adjust_column_widths()
            
            return True
        except Exception as e:
            print(f"Error appending to Excel file: {e}")
            return False
    
    def _extract_subject(self, email_content: str) -> str:
        """Extract subject line from email content."""
        if email_content and email_content.startswith("Subject:"):
            end_idx = email_content.find("\n\n")
            if end_idx != -1:
                subject = email_content[8:end_idx].strip()
                return subject[:100]  # Truncate if too long
        return ""
    
    def _adjust_column_widths(self):
        """Adjust column widths for better readability."""
        try:
            workbook = load_workbook(self.excel_file)
            worksheet = workbook[self.sheet_name]
            
            for column in worksheet.columns:
                max_length = 0
                column_letter = None
                for cell in column:
                    if column_letter is None:
                        try:
                            column_letter = cell.column_letter  # type: ignore
                        except AttributeError:
                            continue
                    try:
                        if len(str(cell.value or "")) > max_length:
                            max_length = len(str(cell.value or ""))
                    except:
                        pass
                if column_letter:
                    adjusted_width = min(max_length + 2, 50)  # Cap at 50
                    worksheet.column_dimensions[column_letter].width = adjusted_width
            
            # Freeze header row
            worksheet.freeze_panes = "A2"
            
            workbook.save(self.excel_file)
        except Exception as e:
            print(f"Error adjusting column widths: {e}")

"""Professional email report generator for lead qualification."""

from datetime import datetime


def generate_report_html(classification: dict, sender: str, email_content: str) -> str:
    """Generate a professional HTML report with classification details.
    
    Args:
        classification: Dictionary containing email classification results
        sender: Sender email address or domain
        email_content: Original email content (for reference)
        
    Returns:
        HTML string formatted as a professional report
    """
    action = classification.get("action", "unknown")
    status_color = "#28a745" if action == "qualify" else "#dc3545"
    status_badge = "QUALIFIED" if action == "qualify" else "DISQUALIFIED"
    
    # Extract contact and company information
    contact_name = classification.get("contact_name", "N/A")
    contact_last_name = classification.get("contact_last_name", "")
    full_name = f"{contact_name} {contact_last_name}".strip() if contact_name != "N/A" else "N/A"
    contact_email = classification.get("email", "N/A")
    company_name = classification.get("company_name", "N/A")
    company_type = classification.get("company_type", "unknown")
    confidence = classification.get("confidence", 0.0)
    date_of_contact = classification.get("date_of_contact", "N/A")
    salesperson = classification.get("salesperson", "N/A")
    
    # Format lists
    operation_countries = ", ".join(classification.get("operation_countries", [])) or "N/A"
    company_presence = ", ".join(classification.get("company_presence", [])) or "N/A"
    projects = "<br>".join([f"• {p}" for p in classification.get("current_projects", [])]) or "N/A"
    
    # Determine rejection reason if disqualified
    rejection_reason = ""
    if action == "disqualify":
        email_domain = contact_email.split("@")[1] if "@" in contact_email else ""
        if email_domain in ["gmail.com", "outlook.com", "yahoo.com", "hotmail.com"]:
            rejection_reason = "Personal email domain (non-corporate account)"
        else:
            rejection_reason = "Failed qualification criteria"
    
    html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.5;
            color: #333;
            background-color: #f9f9f9;
            margin: 0;
            padding: 0;
        }}
        .container {{
            max-width: 600px;
            margin: 10px auto;
            background-color: #ffffff;
            border-radius: 6px;
            box-shadow: 0 1px 4px rgba(0,0,0,0.08);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px 25px;
            text-align: center;
        }}
        .header h1 {{
            margin: 0;
            font-size: 22px;
            font-weight: 600;
        }}
        .header p {{
            margin: 4px 0 0 0;
            font-size: 12px;
            opacity: 0.95;
        }}
        .status-badge {{
            display: inline-block;
            background-color: {status_color};
            color: white;
            padding: 6px 14px;
            border-radius: 3px;
            font-weight: 600;
            font-size: 12px;
            margin-top: 10px;
        }}
        .content {{
            padding: 20px 25px;
        }}
        .section {{
            margin-bottom: 16px;
        }}
        .section:last-of-type {{
            margin-bottom: 0;
        }}
        .section-title {{
            font-size: 13px;
            font-weight: 700;
            color: #667eea;
            border-bottom: 1px solid #e8e8e8;
            padding-bottom: 8px;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 0.4px;
        }}
        .info-row {{
            display: flex;
            justify-content: space-between;
            padding: 6px 0;
            border-bottom: 1px solid #f0f0f0;
            font-size: 13px;
        }}
        .info-row:last-child {{
            border-bottom: none;
        }}
        .info-label {{
            font-weight: 600;
            color: #666;
            margin-right: 10px;
            flex: 0 0 auto;
        }}
        .info-value {{
            color: #333;
            text-align: right;
            flex: 1;
            word-break: break-word;
        }}
        .confidence-bar {{
            width: 100%;
            height: 4px;
            background-color: #e0e0e0;
            border-radius: 2px;
            overflow: hidden;
            margin-top: 6px;
        }}
        .confidence-fill {{
            height: 100%;
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            width: {confidence * 100}%;
        }}
        .reason-text {{
            color: #d9534f;
            font-size: 13px;
            padding: 8px 0;
        }}
        .footer {{
            background-color: #f5f5f5;
            padding: 12px 25px;
            text-align: center;
            border-top: 1px solid #e8e8e8;
            font-size: 11px;
            color: #999;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Lead Qualification Report</h1>
            <p>Automated Classification & Analysis</p>
            <div class="status-badge">{status_badge}</div>
        </div>
        
        <div class="content">
            <div class="section">
                <div class="section-title">Contact</div>
                <div class="info-row">
                    <span class="info-label">Name:</span>
                    <span class="info-value">{full_name}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Email:</span>
                    <span class="info-value">{contact_email}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Date:</span>
                    <span class="info-value">{date_of_contact}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Source:</span>
                    <span class="info-value">{sender}</span>
                </div>
            </div>
            
            <div class="section">
                <div class="section-title">Company</div>
                <div class="info-row">
                    <span class="info-label">Name:</span>
                    <span class="info-value">{company_name}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Type:</span>
                    <span class="info-value">{company_type}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">HQ / Offices:</span>
                    <span class="info-value">{operation_countries}</span>
                </div>
            </div>
            
            <div class="section">
                <div class="section-title">Result</div>
                <div class="info-row">
                    <span class="info-label">Decision:</span>
                    <span class="info-value" style="color: {status_color}; font-weight: 700;">{action.upper()}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Confidence:</span>
                    <span class="info-value">{confidence:.0%}</span>
                </div>
                <div class="confidence-bar">
                    <div class="confidence-fill"></div>
                </div>
                <div class="info-row">
                    <span class="info-label">Assigned:</span>
                    <span class="info-value">{salesperson}</span>
                </div>
            </div>
            
            {f'<div class="section"><div class="section-title">Note</div><div class="reason-text">{rejection_reason}</div></div>' if action == "disqualify" else ''}
        </div>
        
        <div class="footer">
            <p>Generated on {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}</p>
        </div>
    </div>
</body>
</html>
"""
    return html

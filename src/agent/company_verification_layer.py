import re
from typing import Dict, Optional

from openai import AsyncOpenAI

from src.agent.config import Config
from src.agent.graph_schemas import CompanyVerificationResult, EmailClassificationState
from src.agent.logger import get_logger

logger = get_logger(__name__)
config = Config()

client = AsyncOpenAI(
    base_url=config.OPENAI_BASE_URL,
    api_key=config.OPENROUTER_API_KEY,
)

COMMON_PERSONAL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "icloud.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
}


EMAIL_REGEX = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


def _extract_email_domain(email: Optional[str]) -> str:
    if not email or "@" not in email:
        return ""
    return email.split("@")[-1].strip().lower()


def _extract_form_email(email_subject: str, email_body: str) -> str:
    text = "\n".join([email_subject or "", email_body or ""]).strip()
    if not text:
        return ""

    json_key_patterns = [
        r'"(?:email|e-mail|work_email|business_email|contact_email)"\s*:\s*"([^"\s]+@[^"\s]+)"',
        r"'(?:email|e-mail|work_email|business_email|contact_email)'\s*:\s*'([^'\s]+@[^'\s]+)'",
    ]
    for pattern in json_key_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().lower()

    labeled_patterns = [
        r"\b(?:email|e-mail|work email|business email|contact email)\b\s*[:=]\s*([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})",
    ]
    for pattern in labeled_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().lower()

    generic_match = EMAIL_REGEX.search(text)
    if generic_match:
        return generic_match.group(0).strip().lower()

    return ""


def _extract_company_name_from_text(email_subject: str, email_body: str) -> str:
    text = "\n".join([email_subject or "", email_body or ""]).strip()
    if not text:
        return ""

    patterns = [
        r"\bfrom\s+([A-Z][A-Za-z0-9&\-. ]{1,60})",
        r"\bat\s+([A-Z][A-Za-z0-9&\-. ]{1,60})",
        r"\bcompany\s*:\s*([A-Z][A-Za-z0-9&\-. ]{1,60})",
        r"\borganization\s*:\s*([A-Z][A-Za-z0-9&\-. ]{1,60})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip(" .,-")

    return ""


async def verify_company_identity(state: EmailClassificationState) -> Dict[str, object]:
    form_email = _extract_form_email(
        email_subject=state.email_subject or "",
        email_body=state.email_body or "",
    )
    return await run_company_verification(
        form_email=form_email,
        email_subject=state.email_subject or "",
        email_body=state.email_body or "",
    )


async def run_company_verification(
    form_email: Optional[str],
    email_subject: str,
    email_body: str,
) -> Dict[str, object]:
    resolved_form_email = (form_email or _extract_form_email(email_subject, email_body)).strip().lower()
    form_domain = _extract_email_domain(resolved_form_email)
    company_name = _extract_company_name_from_text(email_subject, email_body)

    if not form_domain:
        fallback = CompanyVerificationResult(
            is_corporate_email=False,
            is_legit_company=False,
            company_type="unknown",
            company_name=company_name or None,
            sender_domain="",
            reason="verification_skipped: missing form email domain",
        )
        return {
            "company_verification": fallback.model_dump(),
            "status": "company verification skipped: missing form email domain",
        }

    if form_domain in COMMON_PERSONAL_DOMAINS:
        fallback = CompanyVerificationResult(
            is_corporate_email=False,
            is_legit_company=False,
            company_type="unknown",
            company_name=company_name or None,
            sender_domain=form_domain,
            reason="form email domain is a known personal email provider",
        )
        return {
            "company_verification": fallback.model_dump(),
            "status": "company verification complete",
        }

    system_prompt = (
        "You verify whether a form submitter email likely represents a real company. "
        "Return strict JSON matching the schema. "
        "Evaluate: corporate-domain likelihood, company legitimacy, and company type category."
    )

    user_prompt = (
        "Analyze the form submitter email and inferred company information.\n\n"
        f"Form email: {resolved_form_email or 'unknown'}\n"
        f"Form email domain: {form_domain}\n"
        f"Inferred company name: {company_name or 'unknown'}\n"
        f"Email subject: {email_subject or ''}\n"
        f"Email body: {email_body or ''}\n\n"
        "Set company_type to a concise category (telecom, finance, healthcare, software, manufacturing, etc), "
        "or unknown if unclear."
    )

    try:
        response = await client.beta.chat.completions.parse(
            model=config.LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=CompanyVerificationResult,
        )
        parsed = response.choices[0].message.parsed
        if not parsed:
            raise ValueError("No parsed response returned by model")

        result = parsed.model_dump()
        if not result.get("sender_domain"):
            result["sender_domain"] = form_domain
        if not result.get("company_name") and company_name:
            result["company_name"] = company_name

        return {
            "company_verification": result,
            "status": "company verification complete",
        }
    except Exception as exc:
        logger.error("Company verification failed: %s", exc)
        fallback = CompanyVerificationResult(
            is_corporate_email=True,
            is_legit_company=False,
            company_type="unknown",
            company_name=company_name or None,
            sender_domain=form_domain,
            reason=f"verification_failed: {exc}",
        )
        return {
            "company_verification": fallback.model_dump(),
            "status": "company verification failed; fallback returned",
        }

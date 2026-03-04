from pydantic import BaseModel, Field
from typing import Annotated, Dict, Optional, List, Any, Literal, Tuple
from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage
from langchain_core.documents import Document

# for RAG
class ChatOutput(BaseModel):
    """Output format for the chat response.

    Defines the structure for returning an answer.

    Attributes:
        LLM_msg (str): The generated answer to the user's question.
    """

    LLM_msg: Optional[str] = Field(default="", description="The output from the LLM.")
    retrieved_documents: list = Field(
        default=[],
        description="List of references retrieved for the question"
    )
    messages: list = Field(
        default=[],
        description= "List of messages exchanged during the conversation. This always needs to be returned."
    )

class ChatRequest(BaseModel):
    """Input format for a chat request.

    Defines the structure for incoming chat requests.

    Attributes:
        user_input (str): The user's request.
        conversation_id (str): The conversation ID.
    """

    user_input: Optional[str] = Field(default="", description="The user's request.")
    conversation_id: str = Field(
        default="default", 
        description="Optional conversation ID for chat history"
    )
    
class RAGState(ChatRequest, ChatOutput):
    """State class for the LangGraph retrieval workflow."""
    history_rewritten_question: str = ""
    alternative_queries: List[str] = Field(default_factory=list, description="List of alternative queries")
    status: str = ""

    
class AlternativeQueries(BaseModel):
    queries: List[str] = Field(description="List of alternative query variations")

class RewrittenQuestion(BaseModel):
    rewritten_question: str = Field(description="The rewritten question based on conversation history")

class FinalResponse(BaseModel):
    response: str = Field(description="The final response to the user's question based on retrieved context")
    
 # for Document Ingestion
class FileData(BaseModel):
    """Model for file data with metadata."""
    filename: str = Field(description="The original filename of the uploaded file")
    content: str = Field(description="Base64 encoded content of the file")
    size: int = Field(description="Size of the file in bytes")
    content_type: str = Field(description="MIME type of the file")
    file_hash: Optional[str] = Field(default=None, description="SHA-256 hash of the file content for duplicate detection")

class DocIngestionOutput(BaseModel):
    """Output format for the Document ingestion response.

    Defines the structure for returning an answer.

    Attributes:
        status (str): Status message of the ingestion process.
        combined_story (str): The combined success story generated from the content.
        success_ingestion (bool): Whether the ingestion was successful.
    """

    status: Optional[str] = Field(default="", description="Status message of the ingestion process")
    combined_story: Optional[str] = Field(default="", description="The combined success story generated from the content")
    success_ingestion: Optional[bool] = Field(default=False, description="Whether the ingestion was successful")
    
    
class DocIngestionRequest(BaseModel):
    """Input format for a document ingestion request.

    Defines the structure for incoming document ingestion requests.

    Attributes:
        conversation_id (str): The conversation ID.
        files (list): Optional list of uploaded files.
        pdf_path (str): Optional path to the PDF file.
    """
    additional_context: Optional[str] = Field(
        default=None, 
        description="Optional additional context to guide the ingestion process."
    )
    conversation_id: str = Field(
        default="default", 
        description="Optional conversation ID for chat history"
    )
    files: Optional[List[FileData]] = Field(
        default=None, 
        description="Optional list of uploaded file objects with metadata."
    )
    pdf_path: Optional[str] = Field(
        default=None,
        description="Optional path to the email PDF file in MinIO storage."
    )
    
class IngestionState(DocIngestionRequest, DocIngestionOutput):
    """State class for the LangGraph Ingestion workflow."""
    image_content: Optional[bytes] = None
    llm_response: Optional[str] = None
    ppt_path: Optional[str] = None
    #pdf_path: Optional[str] = None
    source_document_link: Optional[str] = None
    doc_path: Optional[str] = None
    image_paths_list: Optional[List[Tuple[str, int]]] = None
    text_and_notes: Optional[List[Dict[str, str]]] = None
    ExtractedData_list: Optional[List[Dict[str, Any]]] = None
    star_story: Optional[str] = None
    hero_story: Optional[str] = None
    pas_story: Optional[str] = None
    combined_story: Optional[str] = None
    success_stories_list: Optional[List[str]] = Field(default_factory=list, description="List of unique success stories")
    success_ingestion: Optional[bool] = None
    
    def __init__(self, **data):
        super().__init__(**data)
        # Don't pre-set ppt_path here - let process_uploaded_files handle it
        # This ensures the correct MinIO object path is used (e.g., presentations/filename.pptx)
        # instead of just the filename
        pass
        
class ExtractData(BaseModel):
    # Success Story Data Elements (optimized for both customer and product success stories)
    story_type: Optional[str] = Field(default=None, description="Type of success story: 'customer', 'product', 'internal', or 'partnership'")
    subject_name: str = Field(description="The name of the customer/client organization, product name, or subject of the success story")
    business_pain_points: Optional[str] = Field(default=None, description="Business challenges, operational issues, and strategic pain points experienced before the solution/intervention")
    technical_pain_points: Optional[str] = Field(default=None, description="Technical challenges, system limitations, infrastructure constraints, and technology gaps that needed to be addressed")
    solution_scope: Optional[str] = Field(default=None, description="The boundaries, scale, and extent of the solution provided - what was included and excluded from the engagement")
    solution_delivered: Optional[str] = Field(default=None, description="Concrete deliverables, implementations, outcomes, or product features that addressed the identified needs")
    solution_capabilities: Optional[str] = Field(default=None, description="Key capabilities, functionalities, and core competencies that the solution provides or enables")
    solution_features: Optional[str] = Field(default=None, description="Specific features, components, modules, or functionality elements that comprise the solution")
    delivered_business_value: Optional[str] = Field(default=None, description="Measurable business outcomes and quantifiable value delivered such as revenue increase, cost reduction, ROI, time savings, or performance improvements")
    benefits: Optional[str] = Field(default=None, description="Overall benefits achieved including qualitative improvements, strategic advantages, competitive benefits, user satisfaction, and long-term value creation")
    technology_stack: Optional[str] = Field(default=None, description="Specific technologies, tools, platforms, software, and technical components used in the solution delivery or product")
    technical_capabilities: Optional[str] = Field(default=None, description="Technical competencies, expertise areas, specialized skills, or product capabilities demonstrated")
    related_network_elements: Optional[str] = Field(default=None, description="Network infrastructure components, telecommunications elements, vendor partnerships, or integrations involved")
    delivery_challenges: Optional[str] = Field(default=None, description="Up to 5 significant problems, obstacles, or complications encountered during delivery/development and how they were resolved")
    delivery_duration: Optional[str] = Field(default=None, description="Total time from project/product initiation to completion, including key milestones and phases")
    team_size: Optional[str] = Field(default=None, description="Number of team members involved in the project delivery or product development, including roles and expertise areas")
    project_evolution: Optional[str] = Field(default=None, description="How the project scope, timeline, team composition, requirements, or product features evolved during implementation")
    team_dynamics: Optional[str] = Field(default=None, description="Collaboration patterns, working relationships, communication approaches, and coordination methods used")
    key_contacts: Optional[str] = Field(default=None, description="Key stakeholders, decision makers, sponsors, technical contacts, or product champions who can serve as references")
    keywords: Optional[List[str]] = Field(default=None, description="Key terms, concepts, technologies, and important phrases extracted from the success story for searchability and categorization")
    glossary: Optional[Dict[str, str]] = Field(default=None, description="Dictionary of acronyms, technical terms, and specialized terminology with their definitions found in the success story")
    #source_document_link: Optional[str] = Field(default=None, description="Direct link or reference to the source PowerPoint presentation, document, or file containing the original success story")
    
class DataExtraction(BaseModel):
    ExtractedData: List[ExtractData] = Field(description="A list of extracted Data each one represent a slide")

class SuccessStories(BaseModel):
    SuccessStories: List[str] = Field(description="List of success stories extracted from the document")



#for email ingestion

class EmailIngestionOutput(BaseModel):
    """Output format for the email ingestion response.

    Defines the structure for returning an answer.

    Attributes:
        LLM_msg (str): The generated answer to the user's question.
    """
    status: Optional[str] = Field(default="", description="Status message of the ingestion process")
    email_content: Optional[str] = Field(default="", description="Combined email body and extracted attachment text")
    sender: Optional[str] = Field(default=None, description="Sender email address")
    company_verification: Dict[str, Any] = Field(default_factory=dict, description="Company/domain verification JSON")
    classification: Dict[str, Any] = Field(default_factory=dict, description="Structured email classification output")
    

class EmailIngestionRequest(BaseModel):
    """Input format for an email ingestion request.

    Defines the structure for incoming email ingestion requests.

    Attributes:
        email_id (str): The email ID.
        conversation_id (str): The conversation ID.
    """
    email_id: str = Field(default="", description="email ID")
    conversation_id: str = Field(
        default="default", 
        description="Optional conversation ID for chat history"
    )
        
class EmailIngestionState(EmailIngestionRequest, ChatOutput):
    """State class for the LangGraph Ingestion workflow."""
    sender: Optional[str] = None
    hasAttachments: Optional[bool] = None
    email_content: Optional[str] = None
    attachment_files: Optional[List[Tuple[str, str]]] = None
    pdf_path: Optional[str] = None
    combined_story: Optional[str] = None
    success_stories_list: Optional[List[str]] = Field(default_factory=list, description="List of unique success stories")
    success_ingestion: Optional[bool] = None
    company_verification: Dict[str, Any] = Field(default_factory=dict, description="Company/domain verification JSON")
    classification: Dict[str, Any] = Field(default_factory=dict, description="Structured email classification output")


class EmailClassificationRequest(BaseModel):
    email_subject: Optional[str] = Field(default="", description="Email subject line")
    email_body: Optional[str] = Field(default="", description="Email body content")
    attachment_text: Optional[str] = Field(default="", description="Extracted text from attachments")
    sender_email: Optional[str] = Field(default="", description="Sender email address")
    conversation_id: str = Field(default="default", description="Conversation ID")


class CompanyVerificationResult(BaseModel):
    is_corporate_email: bool = Field(description="Whether form email appears to be a corporate domain (not a personal email provider)")
    is_legit_company: bool = Field(description="Whether company appears to be a legitimate organization")
    company_type: str = Field(description="Company category such as telecom, finance, healthcare, etc")
    company_name: Optional[str] = Field(default=None, description="Detected or inferred company name")
    sender_domain: str = Field(description="Domain of the email from the form submission (extracted from email body, not the sender)")
    reason: str = Field(description="Short rationale for the verification result")


class EmailClassificationResult(BaseModel):
    date_of_contact: str = Field(description="Form submission date in YYYY-MM-DD format")
    action: Literal["qualify", "disqualify"] = Field(description="Qualification decision")
    company_name: str = Field(description="Company name extracted from form submission")
    company_type: Literal["Education", "Enterprise", "Telco", "Telco Vendor", "unknown"] = Field(
        description="Normalized company type"
    )
    operation_countries: List[str] = Field(default_factory=list, description="Countries where company has HQ/offices")
    company_presence: List[str] = Field(default_factory=list, description="Countries where company operates commercially")
    current_projects: List[str] = Field(default_factory=list, description="Relevant current initiatives (1-5 when available)")
    source: str = Field(description="Exact source line containing whitepages from the form")
    email: str = Field(description="Email of the person who filled the form")
    contact_name: str = Field(description="Contact first name from form")
    contact_last_name: str = Field(description="Contact last name from form")
    salesperson: Literal["Victoria", "Edu", "Casen", "Javi", "Ziad", "none"] = Field(
        description="Assigned salesperson based on HQ geography"
    )
    confidence: float = Field(description="Confidence score between 0.0 and 1.0")


class EmailClassificationOutput(BaseModel):
    status: str = Field(default="", description="Status message for the classification process")
    classification: Dict[str, Any] = Field(default_factory=dict, description="Structured classification JSON")
    company_verification: Dict[str, Any] = Field(default_factory=dict, description="Company/domain verification JSON")
    retrieved_context: str = Field(default="", description="RAG context used for classification")


class EmailClassificationState(EmailClassificationRequest, EmailClassificationOutput):
    query_text: str = Field(default="", description="Combined text used for retrieval and classification")
    

    
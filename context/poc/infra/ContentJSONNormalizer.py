"""
Content JSON Normalizer implementation with dependency injection for LLM.
"""
from typing import Type
from pydantic import BaseModel

from infra.BaseContentJSONNormalizer import BaseContentJSONNormalizer, T
from infra.IStructuredOutputLLM import IStructuredOutputLLM


# Prompt template constant focused on effective markdown extraction
EXTRACTION_PROMPT_TEMPLATE = """You are an expert data extraction assistant specialized in analyzing markdown-formatted documents and converting them into structured JSON data.

Your task is to carefully analyze the provided markdown content and extract ALL relevant information according to the defined schema. Follow these guidelines:

1. **Accuracy**: Extract information exactly as it appears in the document. Do not infer, guess, or add information that is not present.

2. **Completeness**: Ensure you capture all data points specified in the schema. If a field is optional and no data is found, set it to null (None).

3. **Structure Preservation**: Maintain the hierarchical relationships and nested structures as they appear in the document.

4. **Tables & Lists**: Pay special attention to tables, lists, and structured data - ensure all rows, columns, and items are captured.

5. **Formatting Cues**: Look for bold, underline, or emphasized text as they often indicate critical information.

6. **Signatures & Attestations**: Check for signature blocks, dates, and attestation markers (initials, stamps, or "-" indicating absence).

7. **Validation**: If values represent measurements, ensure units (UOM) are captured alongside quantities.

8. **Schema Compliance**: CRITICAL - You MUST follow the exact schema structure provided. Do not add extra fields or change field names. Use only the fields defined in the schema.

Markdown Content to Extract:
---
{content}
---

Extract the information from the above markdown content into the structured format defined by the schema. Return ONLY the fields defined in the schema - do not invent new fields.
"""


class ContentJSONNormalizer(BaseContentJSONNormalizer[T]):
    """
    Implementation of BaseContentJSONNormalizer using dependency injection.
    
    This class converts markdown text content into Pydantic models using
    any LLM implementation that conforms to IStructuredOutputLLM interface.
    
    Follows SOLID principles:
    - Single Responsibility: Only handles prompt formatting and normalization logic
    - Open/Closed: Open for extension (new LLM providers) without modification
    - Dependency Inversion: Depends on IStructuredOutputLLM abstraction, not concrete implementations
    """
    
    def __init__(self, llm: IStructuredOutputLLM):
        """
        Initialize the ContentJSONNormalizer with an LLM instance.
        
        Args:
            llm: An implementation of IStructuredOutputLLM interface
        """
        self._llm = llm
    
    def normalize(self, content: str) -> T:
        """
        Normalize markdown content into a structured Pydantic model.
        
        Args:
            content: The markdown text content to be normalized
            
        Returns:
            A Pydantic model instance containing the structured data
            
        Raises:
            ValueError: If the content is empty or cannot be normalized
            Exception: For LLM invocation or parsing errors
        """
        if not content or not content.strip():
            raise ValueError("Content cannot be empty")
        
        try:
            # Format the prompt with the input content
            prompt = EXTRACTION_PROMPT_TEMPLATE.format(content=content)
            
            # Invoke the LLM via the interface
            result = self._llm.invoke(prompt)
            
            return result
            
        except Exception as e:
            raise Exception(f"Failed to normalize content: {str(e)}") from e
    
    def normalize_with_custom_prompt(self, content: str, custom_prompt: str) -> T:
        """
        Normalize content using a custom prompt template.
        
        Useful for specialized extraction scenarios that require different
        instructions or context.
        
        Args:
            content: The markdown text content to be normalized
            custom_prompt: Custom prompt template with {content} placeholder
            
        Returns:
            A Pydantic model instance containing the structured data
            
        Raises:
            ValueError: If content is empty or prompt doesn't contain {content} placeholder
            Exception: For LLM invocation or parsing errors
        """
        if not content or not content.strip():
            raise ValueError("Content cannot be empty")
        
        if "{content}" not in custom_prompt:
            raise ValueError("Custom prompt must contain {content} placeholder")
        
        try:
            prompt = custom_prompt.format(content=content)
            result = self._llm.invoke(prompt)
            
            return result
            
        except Exception as e:
            raise Exception(f"Failed to normalize content with custom prompt: {str(e)}") from e


# Factory function for convenient instantiation with Azure OpenAI
def create_normalizer_with_azure(
    output_model: Type[T],
    azure_endpoint: str = None,
    api_key: str = None,
    api_version: str = "2024-12-01-preview",
    deployment_name: str = None,
    model_name: str = "gpt-4.1-mini",
    temperature: float = 0.0
) -> ContentJSONNormalizer[T]:
    """
    Factory function to create a ContentJSONNormalizer with Azure OpenAI.
    
    This is a convenience function that creates both the Azure OpenAI LLM
    and the normalizer. For more control, create the LLM separately and
    inject it into ContentJSONNormalizer.
    
    If parameters are not provided, they will be read from environment variables:
    - AZURE_OPENAI_ENDPOINT
    - AZURE_OPENAI_API_KEY
    - AZURE_OPENAI_API_VERSION (optional, defaults to "2025-04-14")
    - AZURE_OPENAI_DEPLOYMENT_NAME
    
    Args:
        output_model: Pydantic model class for structured output
        azure_endpoint: Azure OpenAI endpoint (or set AZURE_OPENAI_ENDPOINT env var)
        api_key: Azure OpenAI API key (or set AZURE_OPENAI_API_KEY env var)
        api_version: Azure OpenAI API version (default: "2025-03-01-preview")
        deployment_name: Deployment name (or set AZURE_OPENAI_DEPLOYMENT_NAME env var)
        model_name: Model name (default: "gpt-4")
        temperature: Sampling temperature (default: 0.0)
        
    Returns:
        ContentJSONNormalizer instance configured with Azure OpenAI
        
    Raises:
        ValueError: If required parameters are missing
    """
    from infra.AzureOpenAIStructuredLLM import AzureOpenAIStructuredLLM
    import os
    from dotenv import load_dotenv
    
    # Load environment variables from .env file
    load_dotenv()
    
    # Read from environment if not provided
    azure_endpoint = azure_endpoint or os.getenv("AZURE_OPENAI_ENDPOINT")
    api_key = api_key or os.getenv("AZURE_OPENAI_API_KEY")
    api_version = api_version or os.getenv("AZURE_OPENAI_API_VERSION")
    deployment_name = deployment_name or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
    
    # Validate required parameters
    if not azure_endpoint:
        raise ValueError("azure_endpoint must be provided or AZURE_OPENAI_ENDPOINT env var must be set")
    if not api_key:
        raise ValueError("api_key must be provided or AZURE_OPENAI_API_KEY env var must be set")
    if not deployment_name:
        raise ValueError("deployment_name must be provided or AZURE_OPENAI_DEPLOYMENT_NAME env var must be set")
    
    # Create Azure OpenAI LLM
    llm = AzureOpenAIStructuredLLM(
        azure_endpoint=azure_endpoint,
        api_key=api_key,
        api_version=api_version,
        deployment_name=deployment_name,
        model_name=model_name,
        output_model=output_model,
        temperature=temperature
    )
    
    # Create and return normalizer
    return ContentJSONNormalizer(llm=llm)
"""
Azure OpenAI implementation of structured output LLM.
"""
from typing import Type
from pydantic import BaseModel
from langchain_openai import AzureChatOpenAI

from infra.IStructuredOutputLLM import IStructuredOutputLLM


class AzureOpenAIStructuredLLM(IStructuredOutputLLM):
    """
    Azure OpenAI implementation of IStructuredOutputLLM.
    
    This class encapsulates the Azure OpenAI configuration and
    structured output capability using LangChain.
    """
    
    def __init__(
        self,
        azure_endpoint: str,
        api_key: str,
        deployment_name: str,
        output_model: Type[BaseModel],
        api_version: str,
        model_name: str,
        temperature: float = 0.0,
        max_retries: int = 2
    ):
        """
        Initialize Azure OpenAI structured LLM.
        
        Args:
            azure_endpoint: Azure OpenAI endpoint URL
            api_key: Azure OpenAI API key
            deployment_name: Azure OpenAI deployment name
            output_model: Pydantic model class for structured output
            api_version: Azure OpenAI API version (default: "2025-03-01-preview")
            model_name: Model name (default: "gpt-4")
            temperature: Sampling temperature (default: 0.0)
            max_retries: Maximum number of retries on failure (default: 2)
        """
        self.azure_endpoint = azure_endpoint
        self.api_key = api_key
        self.api_version = api_version
        self.deployment_name = deployment_name
        self.model_name = model_name
        self.output_model = output_model
        self.temperature = temperature
        self.max_retries = max_retries
        
        # Initialize Azure OpenAI chat model
        self._llm = AzureChatOpenAI(
            azure_endpoint=self.azure_endpoint,
            api_key=self.api_key,
            api_version=self.api_version,
            azure_deployment=self.deployment_name,
            model=self.model_name,
            temperature=self.temperature,
            max_retries=self.max_retries
        )
        
        # Create structured output model with strict JSON schema enforcement
        self._structured_llm = self._llm.with_structured_output(
            schema=self.output_model,
            method="json_schema",
            strict=True
        )
    
    def invoke(self, prompt: str) -> BaseModel:
        """
        Invoke Azure OpenAI LLM with structured output.
        
        Args:
            prompt: The input prompt/text for the LLM
            
        Returns:
            A Pydantic model instance containing the structured response
            
        Raises:
            Exception: For LLM invocation or parsing errors
        """
        try:
            result = self._structured_llm.invoke(prompt)
            
            if not isinstance(result, self.output_model):
                raise ValueError(
                    f"Expected output of type {self.output_model.__name__}, "
                    f"but got {type(result).__name__}"
                )
            
            return result
            
        except Exception as e:
            raise Exception(f"Azure OpenAI invocation failed: {str(e)}") from e
    
    def get_output_model(self) -> Type[BaseModel]:
        """
        Get the Pydantic model class used for structured output.
        
        Returns:
            The Pydantic model class
        """
        return self.output_model


def create_azure_llm_from_env(
    output_model: Type[BaseModel],
    model_name: str,
    temperature: float = 0.0
) -> AzureOpenAIStructuredLLM:
    """
    Factory function to create AzureOpenAIStructuredLLM from environment variables.
    
    Required environment variables:
    - AZURE_OPENAI_ENDPOINT
    - AZURE_OPENAI_API_KEY
    - AZURE_OPENAI_DEPLOYMENT_NAME
    
    Optional environment variables:
    - AZURE_OPENAI_API_VERSION (default: "2025-03-01-preview")
    
    Args:
        output_model: Pydantic model class for structured output
        model_name: Model name (default: "gpt-4")
        temperature: Sampling temperature (default: 0.0)
        
    Returns:
        AzureOpenAIStructuredLLM instance
        
    Raises:
        ValueError: If required environment variables are missing
    """
    import os
    
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-03-01-preview")
    deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
    
    if not azure_endpoint:
        raise ValueError("AZURE_OPENAI_ENDPOINT environment variable must be set")
    if not api_key:
        raise ValueError("AZURE_OPENAI_API_KEY environment variable must be set")
    if not deployment_name:
        raise ValueError("AZURE_OPENAI_DEPLOYMENT_NAME environment variable must be set")
    
    return AzureOpenAIStructuredLLM(
        azure_endpoint=azure_endpoint,
        api_key=api_key,
        deployment_name=deployment_name,
        output_model=output_model,
        api_version=api_version,
        model_name=model_name,
        temperature=temperature
    )

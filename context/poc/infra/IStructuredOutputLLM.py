"""
Interface for LLM with structured output capability.
"""
from abc import ABC, abstractmethod
from typing import TypeVar
from pydantic import BaseModel


# Generic type for Pydantic model output
T = TypeVar('T', bound=BaseModel)


class IStructuredOutputLLM(ABC):
    """
    Abstract interface for Language Models that support structured output.
    
    This interface defines the contract for LLMs that can generate
    responses conforming to a specific Pydantic model schema.
    """
    
    @abstractmethod
    def invoke(self, prompt: str) -> BaseModel:
        """
        Invoke the LLM with a prompt and return structured output.
        
        Args:
            prompt: The input prompt/text for the LLM
            
        Returns:
            A Pydantic model instance containing the structured response
            
        Raises:
            Exception: For LLM invocation or parsing errors
        """
        pass
    
    @abstractmethod
    def get_output_model(self) -> type[BaseModel]:
        """
        Get the Pydantic model class used for structured output.
        
        Returns:
            The Pydantic model class
        """
        pass

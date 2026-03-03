"""
Base interface for content normalization to structured JSON/Pydantic models.
"""
from abc import ABC, abstractmethod
from typing import TypeVar, Generic
from pydantic import BaseModel


# Generic type for Pydantic model output
T = TypeVar('T', bound=BaseModel)


class BaseContentJSONNormalizer(ABC, Generic[T]):
    """
    Abstract base class for normalizing text/string content into structured Pydantic models.
    
    This interface defines a contract for converting unstructured text input
    into structured JSON output represented by Pydantic models.
    
    Type Parameters:
        T: The Pydantic model type that will be returned
    """
    
    @abstractmethod
    def normalize(self, content: str) -> T:
        """
        Normalize unstructured text content into a structured Pydantic model.
        
        Args:
            content: The raw text/string content to be normalized
            
        Returns:
            A Pydantic model instance containing the structured data
            
        Raises:
            ValueError: If the content cannot be normalized
            Exception: For other processing errors
        """
        pass

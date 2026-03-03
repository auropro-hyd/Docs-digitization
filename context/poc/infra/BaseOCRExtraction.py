from abc import ABC, abstractmethod
from typing import Union, TypeVar
from pathlib import Path
from pydantic import BaseModel

T = TypeVar('T', bound=BaseModel)


class BaseOCRExtraction(ABC):
    """
    Abstract base class for extraction operations.
    Provides contracts for parsing PDF documents and extracting structured data.
    """

    @abstractmethod
    async def parse(self, pdf_path: Union[str, Path]) -> Union[str,dict[str,any]]:
        """
        Parse a PDF document into markdown format.

        Args:
            pdf_path: Path to the input PDF document

        Returns:
            Union[str,list[dict[str,any]]]: Markdown representation of the PDF content or list of extracted data dictionaries
        """
        pass

    
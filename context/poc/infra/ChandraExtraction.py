from typing import Union, TypeVar
from pathlib import Path
from pydantic import BaseModel, ValidationError
import os
from datalab_sdk import AsyncDatalabClient, ConvertOptions
from datalab_sdk.exceptions import (
    DatalabAPIError,
    DatalabTimeoutError,
    DatalabFileError,
    DatalabValidationError,
)
import logging
from .BaseOCRExtraction import BaseOCRExtraction

T = TypeVar('T', bound=BaseModel)
logger = logging.getLogger(__name__)

class ChandraExtraction(BaseOCRExtraction):
    """
    Chandra implementation of the BaseExtraction interface.
    Provides PDF parsing and data extraction capabilities.
    """

    def __init__(self):
        """Initialize the ChandraExtraction instance."""
        #add logic to get the api key, base url & timeout from env variables
        self.apiKey = os.getenv("DATALAB_API_KEY", "")
        self.baseUrl = os.getenv("DATALAB_BASE_URL", "")
        self.timeout = int(os.getenv("DATALAB_TIMEOUT", "300"))
        self.parseOutput = {}
        if(self.apiKey == "" or self.baseUrl == "" or self.timeout == 0):
            raise ValueError("DATALAB_API_KEY, DATALAB_BASE_URL and DATALAB_TIMEOUT environment variables must be set")
        self.async_client = AsyncDatalabClient(
            api_key=self.apiKey,
            base_url=self.baseUrl,
            timeout=self.timeout,
        )

    async def parse(self, pdf_path: Union[str, Path]) -> Union[str,dict[str,any]]:
        """
        Parse a PDF document into markdown format.

        Args:
            pdf_path: Path to the input PDF document

        Returns:
            Union[str,list[dict[str,any]]]: Markdown representation of the PDF content or list of extracted data dictionaries

        Raises:
            FileNotFoundError: If the PDF file doesn't exist
        """
        pdf_path = Path(pdf_path)
        options = ConvertOptions(
            mode="accurate",              
            paginate=True,                
            max_pages=5,
            extras= "chart_understanding"
        )
        
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
        try:
            result = await self.async_client.convert(file_path=str(pdf_path), options=options)
            self.parseOutput[pdf_path] = result
            # logger.info(f"chunks - {result.chunks}")
            # logger.info(f"quality - {result.parse_quality_score}")
            # logger.info(f"cost - {result.cost_breakdown} cents")
            return result.markdown,result.images
        
        except DatalabAPIError as e:
            print(f"API error {e.status_code}: {e.response_data}")
        except DatalabTimeoutError:
            print("Request timed out")
        except DatalabFileError as e:
            print(f"File error: {e}")
        except DatalabValidationError as e:
            print(f"Invalid input: {e}")
        except Exception as e:
            print(f"An unexpected error occurred: {e}")

    
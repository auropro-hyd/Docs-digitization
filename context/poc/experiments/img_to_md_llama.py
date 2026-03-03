"""
Image to Markdown Extraction using Azure AI Inference SDK

This script extracts text from images and converts it to Markdown format using
Azure AI vision models (e.g., Llama-3.2-11B-vision-instruct) hosted on Azure Foundry.

SDK: azure-ai-inference (v1.0.0b9+)
Implementation follows the official Azure AI Inference SDK patterns:
- Uses ImageUrl.load() for efficient image loading
- Supports multiple image formats (.jpeg, .jpg, .png, etc.)
- Includes ImageDetailLevel control for processing quality
- Properly manages client lifecycle with cleanup

Requirements:
    - azure-ai-inference>=1.0.0b9
    - python-dotenv
    - Azure Foundry endpoint with vision model deployment

Environment Variables (.env):
    - AZURE_AI_ENDPOINT: Your Azure Foundry endpoint URL
    - AZURE_AI_API_KEY: Your API key
    - AZURE_AI_MODEL_NAME: Model name (default: llama-3.2-11B-vision-instruct)
"""

import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv
from azure.ai.inference import ChatCompletionsClient
from azure.ai.inference.models import (
    SystemMessage,
    UserMessage,
    ImageContentItem,
    ImageUrl,
    ImageDetailLevel,
    TextContentItem,
)
from azure.core.credentials import AzureKeyCredential

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
logger.info("Environment variables loaded")


def get_image_format(image_path: str) -> str:
    """
    Get the image format from file extension.
    
    Args:
        image_path: Path to the image file
        
    Returns:
        Image format string (e.g., 'jpeg', 'png', 'jpg')
    """
    extension = Path(image_path).suffix.lower().lstrip('.')
    # Normalize 'jpg' to 'jpeg'
    return 'jpeg' if extension == 'jpg' else extension


def extract_markdown_from_image(
    image_path: str,
    page_no: int = None,
    page_type: str = None,
    additional_metadata: dict = None,
    detail_level: ImageDetailLevel = ImageDetailLevel.AUTO
) -> dict:
    """
    Extract text from an image as markdown using Azure AI Llama Vision model.
    
    Args:
        image_path: Path to the input image file (.jpeg, .jpg, .png, etc.)
        page_no: Optional page number for metadata
        page_type: Optional page type (e.g., 'cover', 'content', 'table', etc.)
        additional_metadata: Optional dictionary for additional metadata
        detail_level: Image detail level (LOW, HIGH, or AUTO). Default is AUTO.
        
    Returns:
        Dictionary containing extracted markdown and metadata
    """
    # Get Azure credentials from environment
    endpoint = os.getenv("AZURE_AI_ENDPOINT")
    api_key = os.getenv("AZURE_AI_API_KEY")
    model_name = os.getenv("AZURE_AI_MODEL_NAME", "llama-3.2-11B-vision-instruct")
    
    logger.info(f"Endpoint: {endpoint[:50]}..." if endpoint else "Endpoint: None")
    logger.info(f"API Key: {'*' * 10 if api_key else 'None'}")
    logger.info(f"Model Name: {model_name}")
    
    if not endpoint or not api_key:
        raise ValueError("AZURE_AI_ENDPOINT and AZURE_AI_API_KEY must be set in .env file")
    
    # Initialize the client
    logger.info("Initializing ChatCompletionsClient...")
    client = ChatCompletionsClient(
        endpoint=endpoint,
        credential=AzureKeyCredential(api_key),
        api_version="2024-05-01-preview"
    )
    logger.info("Client initialized successfully")
    
    # Get image format and load image using SDK's built-in method
    image_format = get_image_format(image_path)
    logger.info(f"Detected image format: {image_format}")
    
    # Load image using ImageUrl.load() - recommended approach
    logger.info(f"Loading image from: {image_path}")
    logger.info(f"Detail level: {detail_level}")
    image_url = ImageUrl.load(
        image_file=image_path,
        image_format=image_format,
        detail=detail_level
    )
    logger.info("Image loaded successfully")
    
    # Build metadata context for the prompt
    metadata_context = []
    if page_no is not None:
        metadata_context.append(f"Page Number: {page_no}")
    if page_type is not None:
        metadata_context.append(f"Page Type: {page_type}")
    if additional_metadata:
        for key, value in additional_metadata.items():
            metadata_context.append(f"{key}: {value}")
    
    metadata_str = "\n".join(metadata_context) if metadata_context else "No metadata provided"
    
    # Create the prompt with placeholders for metadata
    system_prompt = """You are a document transcription engine.

Extract text EXACTLY as it appears in the image.
Do not normalize, summarize, merge, or infer structure.

Rules:
- Preserve line breaks exactly
- Preserve column order using | separators only when clearly visible
- If a cell is blank, leave it blank
- Do NOT guess missing values
- Do NOT reformat into Markdown tables
- Handwritten text: transcribe verbatim or write [illegible]
- for signatures in done by & checked by cells, return x if they are signed, if not leave it blank

Output plain text only.
"""

    user_prompt = f"""Please extract all text from this image."""

    # Create the messages
    messages = [
        SystemMessage(content=system_prompt),
        UserMessage(
            content=[
                TextContentItem(text=user_prompt),
                ImageContentItem(image_url=image_url)
            ]
        )
    ]
    
    try:
        # Call the API
        # For serverless endpoints with /models/chat/completions in URL, don't pass model parameter
        logger.info("Preparing to call API...")
        logger.debug(f"Number of messages: {len(messages)}")
        logger.debug(f"Temperature: 0.1, Max tokens: 4096")
        
        logger.info("Calling client.complete()...")
        response = client.complete(
            messages=messages,
            model=model_name,
            temperature=0.1,
            max_tokens=4096
        )
        logger.info("API call completed successfully")
        
        # Extract the markdown content
        logger.info("Extracting markdown content from response...")
        markdown_content = response.choices[0].message.content
        logger.info(f"Extracted markdown length: {len(markdown_content)} characters")
        
        # Return result with metadata
        result = {
            "markdown": markdown_content,
            "metadata": {
                "image_path": image_path,
                "page_no": page_no,
                "page_type": page_type,
            },
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            } if response.usage else None
        }
        
        if additional_metadata:
            result["metadata"].update(additional_metadata)
        
        logger.info("Successfully processed image and extracted markdown")
        return result
    except Exception as e:
        logger.error(f"Error during API call or processing: {type(e).__name__}: {str(e)}")
        raise
    finally:
        # Always close the client to free resources
        logger.info("Closing client...")
        client.close()


def save_markdown(output_path: str, content: str):
    """
    Save markdown content to a file.
    
    Args:
        output_path: Path to save the markdown file
        content: Markdown content to save
    """
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Markdown saved to: {output_path}")


def main():
    """
    Main function to demonstrate usage.
    """
    logger.info("=" * 60)
    logger.info("Starting Image to Markdown extraction...")
    logger.info("=" * 60)
    
    # Get image path from command line or use default
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
        logger.info(f"Using image path from command line: {image_path}")
    else:
        image_path = "D:\\Workspace\\apitoria-use-case\\output_img\\2538104192-EHSII03-page-3.jpg"  # Replace with your image path
        logger.warning(f"No image path provided. Using default: {image_path}")
        logger.warning(f"Usage: python {sys.argv[0]} <image_path>")
    
    # Check if file exists
    logger.info(f"Checking if image exists: {image_path}")
    if not Path(image_path).exists():
        logger.error(f"Image file '{image_path}' not found")
        print(f"Error: Image file '{image_path}' not found")
        print(f"\nUsage: python {sys.argv[0]} <image_path>")
        print(f"Example: python {sys.argv[0]} my_document.jpg")
        return
    
    logger.info(f"Image file found: {image_path}")
    
    try:
        logger.info("Starting extraction process...")
        # Extract markdown with metadata
        # You can specify detail_level: ImageDetailLevel.LOW, HIGH, or AUTO (default)
        result = extract_markdown_from_image(
            image_path=image_path,
            page_no=1,
            page_type="content",
            additional_metadata={
                "document_name": "Sample Document",
                "section": "Introduction"
            },
            detail_level=ImageDetailLevel.HIGH  # Optional: Use HIGH for better accuracy
        )
        
        # Print results
        print("=" * 50)
        print("EXTRACTED MARKDOWN:")
        print("=" * 50)
        print(result["markdown"])
        print("\n" + "=" * 50)
        print("METADATA:")
        print("=" * 50)
        for key, value in result["metadata"].items():
            print(f"{key}: {value}")
        
        # Print token usage if available
        if result.get("usage"):
            print("\n" + "=" * 50)
            print("TOKEN USAGE:")
            print("=" * 50)
            for key, value in result["usage"].items():
                print(f"{key}: {value}")
        
        # Optionally save to file
        output_filename = Path(image_path).stem + "_output.md"
        save_markdown(output_filename, result["markdown"])
        
    except Exception as e:
        print(f"Error processing image: {str(e)}")


if __name__ == "__main__":
    main()

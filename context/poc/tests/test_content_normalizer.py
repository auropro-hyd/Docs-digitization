"""
Test script for ContentJSONNormalizer.
Demonstrates how to use the normalizer to extract structured data from markdown content.

Usage:
    python -m tests.test_content_normalizer <markdown_file_path>
    
Example:
    python -m tests.test_content_normalizer "path/to/document.md"
"""
import re
import sys
import argparse
import json
import time
from pathlib import Path

from domain.models import BpcrPages
from infra.ContentJSONNormalizer import create_normalizer_with_azure


def read_markdown_file(file_path: str) -> str:
    """
    Read markdown file content.
    
    Args:
        file_path: Path to the markdown file
        
    Returns:
        The file content as a string
        
    Raises:
        FileNotFoundError: If file doesn't exist
        IOError: If file cannot be read
    """
    path = Path(file_path)
    
    if not path.exists():
        raise FileNotFoundError(f"Markdown file not found: {file_path}")
    
    if not path.is_file():
        raise ValueError(f"Path is not a file: {file_path}")
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        raise IOError(f"Failed to read file {file_path}: {str(e)}") from e

def split_markdown_pages(markdown_text):
    # This regex matches the page delimiter: {number} followed by dashes
    pattern = r"\{\d+\}-+\n"
    # Split and keep the delimiters as page headers (optional)
    pages = re.split(pattern, markdown_text)
    # Remove any leading/trailing whitespace from each page
    return [page.strip() for page in pages if page.strip()]

def normalize_markdown_to_bpcr(markdown_content: str) ->  BpcrPages:
    """
    Normalize markdown content to BpcrPage model using Azure OpenAI.
    
    Args:
        markdown_content: The markdown text to normalize
        
    Returns:
        BpcrPage instance with extracted data
        
    Raises:
        ValueError: If Azure OpenAI environment variables are not set
        Exception: If normalization fails
    """
    try:
        # Create normalizer with BpcrPage as output model
        normalizer = create_normalizer_with_azure(
            output_model=BpcrPages,
            temperature=0.0
        )
        
        print("Normalizer created successfully")
        print("Invoking Azure OpenAI LLM for data extraction...")
        
        #page_split = split_markdown_pages(markdown_content)[0]
        # Normalize the content
        
        start_time = time.time()
        result = normalizer.normalize(markdown_content)
        end_time = time.time()
        
        elapsed_time = end_time - start_time
        print(f"✓ Normalization completed successfully (took {elapsed_time:.2f} seconds)")
        return result
        
    except ValueError as e:
        print(f"✗ Configuration error: {str(e)}")
        raise
    except Exception as e:
        print(f"✗ Normalization failed: {str(e)}")
        raise


def output_result(bpcr_page: BpcrPages, output_file: str = None):
    """
    Output the normalized result.
    
    Args:
        bpcr_page: The extracted BpcrPage model
        output_file: Optional file path to save JSON output
    """
    # Convert to JSON
    json_output = bpcr_page.model_dump_json(indent=2)
    
    # Print to console
    print("\n" + "="*80)
    print("EXTRACTED BPCR DATA")
    print("="*80)
    print(json_output)
    print("="*80)
    
    # Save to file if specified
    if output_file:
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(json_output)
        
        print(f"\n✓ Output saved to: {output_path}")


def main():
    """Main entry point for the test script."""
    parser = argparse.ArgumentParser(
        description='Test ContentJSONNormalizer with markdown file'
    )
    parser.add_argument(
        'markdown_file',
        help='Path to the markdown file to normalize'
    )
    parser.add_argument(
        '-o', '--output',
        help='Output file path for JSON result (optional)'
    )
    parser.add_argument(
        '--model',
        default='gpt-4',
        help='Azure OpenAI model name (default: gpt-4)'
    )
    
    args = parser.parse_args()
    
    try:
        # Step 1: Read markdown file
        print(f"Reading markdown file: {args.markdown_file}")
        markdown_content = read_markdown_file(args.markdown_file)
        print(f"✓ File read successfully ({len(markdown_content)} characters)")
        
        # Step 2: Normalize to BPCR model
        print("\nNormalizing markdown to BpcrPage model...")
        bpcr_page = normalize_markdown_to_bpcr(markdown_content)
        
        # Step 3: Output result
        output_result(bpcr_page, args.output)
        
        print("\n✓ Test completed successfully!")
        return 0
        
    except FileNotFoundError as e:
        print(f"\n✗ File error: {e}")
        return 1
    except ValueError as e:
        print(f"\n✗ Validation error: {e}")
        print("\nPlease ensure the following environment variables are set:")
        print("  - AZURE_OPENAI_ENDPOINT")
        print("  - AZURE_OPENAI_API_KEY")
        print("  - AZURE_OPENAI_DEPLOYMENT_NAME")
        return 1
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

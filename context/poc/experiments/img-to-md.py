import os
import sys
import argparse
import json
from pathlib import Path
from dotenv import load_dotenv

# Import functions from docAI-image.py
from docAI_image import (
    encode_image_to_base64,
    get_image_mime_type,
    process_image_with_mistral
)

# Load environment variables
load_dotenv()


def save_to_markdown(result, output_folder, image_filename):
    """
    Save the Mistral AI result to a markdown file
    
    Args:
        result: The JSON response from Mistral AI
        output_folder: Directory to save the markdown file
        image_filename: Original image filename (used for naming the markdown file)
    
    Returns:
        Path to the created markdown file
    """
    # Create output folder if it doesn't exist
    os.makedirs(output_folder, exist_ok=True)
    
    # Generate markdown filename (same as image name but with .md extension)
    base_name = Path(image_filename).stem
    markdown_filename = f"{base_name}.md"
    output_path = os.path.join(output_folder, markdown_filename)
    
    # Extract markdown content from all pages
    markdown_content = []
    
    if 'pages' in result:
        for i, page in enumerate(result['pages'], 1):
            if 'markdown' in page:
                markdown_content.append(f"# Page {i}\n\n")
                markdown_content.append(page['markdown'])
                markdown_content.append("\n\n")
    
    # Combine all content
    full_content = "".join(markdown_content)
    
    # Save to file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(full_content)
    
    return output_path


def process_image_to_markdown(image_path, output_folder):
    """
    Main function to process an image and save as markdown
    
    Args:
        image_path: Path to the input image file
        output_folder: Directory to save the markdown output
    
    Returns:
        Path to the created markdown file or None if failed
    """
    print("\n" + "=" * 60)
    print("🚀 Image to Markdown Converter")
    print("=" * 60)
    
    # Validate input image
    if not os.path.exists(image_path):
        print(f"❌ Error: Image file not found at {image_path}")
        return None
    
    print(f"📁 Input: {image_path}")
    print(f"📂 Output folder: {output_folder}")
    
    # Process the image using Mistral AI
    print(f"\n🔄 Processing image with Mistral Document AI...")
    result = process_image_with_mistral(image_path)
    
    if not result:
        print("❌ Failed to process image")
        return None
    
    # Save the result as markdown
    print(f"\n💾 Saving to markdown...")
    try:
        markdown_path = save_to_markdown(result, output_folder, os.path.basename(image_path))
        print(f"✅ Success! Markdown saved to: {markdown_path}")
        
        # Show file info
        file_size = os.path.getsize(markdown_path)
        print(f"📊 File size: {file_size:,} bytes")
        
        # Show preview
        with open(markdown_path, 'r', encoding='utf-8') as f:
            preview = f.read(300)
        
        print(f"\n👀 Preview (first 300 characters):")
        print("-" * 40)
        print(preview)
        if file_size > 300:
            print("...")
        print("-" * 40)
        
        return markdown_path
        
    except Exception as e:
        print(f"❌ Error saving markdown: {e}")
        return None


def main():
    """Main entry point with argument parsing"""
    parser = argparse.ArgumentParser(
        description='Convert image to markdown using Mistral Document AI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python img-to-md.py image.jpg output/
  python img-to-md.py path/to/recipe.png markdown_output/
  python img-to-md.py document.jpg .
        """
    )
    
    parser.add_argument(
        'image_path',
        help='Path to the input image file'
    )
    
    parser.add_argument(
        'output_folder',
        nargs='?',
        default='markdown_output',
        help='Output folder for markdown file (default: markdown_output/)'
    )
    
    args = parser.parse_args()
    
    # Process the image
    result_path = process_image_to_markdown(args.image_path, args.output_folder)
    
    if result_path:
        print(f"\n🎉 Conversion complete!")
        sys.exit(0)
    else:
        print(f"\n❌ Conversion failed")
        sys.exit(1)


if __name__ == "__main__":
    main()

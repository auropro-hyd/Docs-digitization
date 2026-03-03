import os
import base64
import requests
import json
import time
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def encode_image_to_base64(image_path):
    """Read an image file and encode it to base64"""
    try:
        with open(image_path, 'rb') as image_file:
            image_content = image_file.read()
            base64_encoded = base64.b64encode(image_content).decode('utf-8')
            return base64_encoded
    except FileNotFoundError:
        print(f"❌ Error: Image file not found at {image_path}")
        return None
    except Exception as e:
        print(f"❌ Error reading image file: {e}")
        return None

def get_image_mime_type(image_path):
    """Determine the MIME type based on file extension"""
    extension = os.path.splitext(image_path)[1].lower()
    mime_types = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.bmp': 'image/bmp',
        '.webp': 'image/webp'
    }
    return mime_types.get(extension, 'image/jpeg')  # Default to jpeg

def process_image_with_mistral(image_path):
    """Process a local image file using Mistral Document AI"""
    
    print("🔄 DEMO: Image to Structured Data with Mistral Document AI")
    print("=" * 60)
    
    # Get API key from environment
    api_key = os.getenv('AZURE_API_KEY')
    if not api_key:
        print("❌ Error: AZURE_API_KEY not found in environment variables")
        return None
    
    # Show file info
    file_size = os.path.getsize(image_path) / 1024  # KB
    mime_type = get_image_mime_type(image_path)
    print(f"🖼️  Input Image: {image_path}")
    print(f"📊 File Size: {file_size:.1f} KB")
    print(f"🎨 Image Type: {mime_type}")
    
    # Encode image to base64
    print(f"\n🔄 Step 1: Encoding image to base64...")
    time.sleep(1)  # Demo pause
    base64_content = encode_image_to_base64(image_path)
    if not base64_content:
        return None
    
    print(f"✅ Encoded {len(base64_content):,} characters")
    
    # API endpoint and headers
    azure_endpoint = os.getenv('AZURE_ENDPOINT')
    url = azure_endpoint
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    # Request payload - KEY CHANGE: using image_url instead of document_url
    model_name = os.getenv('MODEL_NAME')
    payload = {
        "model": model_name,
        "document": {
            "type": "image_url",
            "image_url": f"data:{mime_type};base64,{base64_content}"
        },
        "include_image_base64": True
    }
    
    try:
        print(f"\n🚀 Step 2: Sending to Mistral Document AI...")
        print(f"🔗 Endpoint: {url}")
        print(f"📸 Processing as: {mime_type}")
        time.sleep(1)  # Demo pause
        
        start_time = time.time()
        response = requests.post(url, headers=headers, json=payload)
        end_time = time.time()
        
        response.raise_for_status()
        
        result = response.json()
        processing_time = end_time - start_time
        
        print(f"✅ Success! Processing completed in {processing_time:.2f} seconds")
        print(f"📄 Pages processed: {result['usage_info']['pages_processed']}")
        print(f"📊 Document size: {result['usage_info']['doc_size_bytes']:,} bytes")
        
        return result
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error making API request: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response status code: {e.response.status_code}")
            print(f"Response content: {e.response.text}")
        return None

def main():
    print("🎯 DEMO SCENARIO: Recipe Image to Shopping List")
    print("=" * 60)
    print("Problem: I have a recipe image and want to:")
    print("  • Extract the text accurately")
    print("  • Generate a shopping list")
    print("  • Get cooking parameters")
    print("\nSolution: AI-powered image processing!\n")
    
    # Example usage - replace with your actual image path
    image_path = "your_image.jpg"  # Put your image file in the same directory
    
    # Check if image file exists
    if not os.path.exists(image_path):
        print(f"📁 Please place your image file in the project directory.")
        print(f"📂 Current directory: {os.getcwd()}")
        print("\n🔍 Available image files:")
        
        # Look for common image extensions
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
        image_files = [f for f in os.listdir('.') 
                      if any(f.lower().endswith(ext) for ext in image_extensions)]
        
        if image_files:
            for file in image_files:
                print(f"  🖼️  {file}")
            image_path = image_files[0]
            print(f"\n🎯 Using: {image_path}")
        else:
            print("  ❌ No image files found")
            print("  📝 Supported formats: JPG, JPEG, PNG, GIF, BMP, WEBP")
            return
    
    # Process the image
    result = process_image_with_mistral(image_path)
    
    if result:
        # Save result
        output_file = "document_ai_result.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        
        print(f"\n💾 Raw extraction saved to: {output_file}")
        
        # Show preview
        print(f"\n👀 PREVIEW - First 200 characters:")
        print("-" * 40)
        first_page_content = result['pages'][0]['markdown'][:200]
        print(f"{first_page_content}...")
        
        print(f"\n🎉 DEMO COMPLETE - Ready for Phase 2!")
        print(f"▶️  Next: Run 'python parse-content-image.py' to see the magic!")

if __name__ == "__main__":
    main()
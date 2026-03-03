import os
import base64


def save_base64_images_to_files(
    images_dict: dict[str, str],
    output_folder: str
) -> list[str]:
    """
    Save base64 encoded images to files in the specified output folder.
    
    Args:
        images_dict: Dictionary with filename as key and base64 image data as value
        output_folder: Path to the output folder where images will be saved
    
    Returns:
        list[str]: List of full paths to the created image files
    """
    # Ensure the output folder exists
    os.makedirs(output_folder, exist_ok=True)
    
    created_files = []
    
    for filename, base64_data in images_dict.items():
        # Decode base64 data
        image_data = base64.b64decode(base64_data)
        
        # Construct the full file path
        file_path = os.path.join(output_folder, filename)
        
        # Write the binary image data to the file
        with open(file_path, 'wb') as f:
            f.write(image_data)
        
        created_files.append(file_path)
    
    return created_files


def create_file_with_content(
    content: str,
    output_folder: str,
    output_filename: str,
    extension: str
) -> str:
    """
    Create a file with the given content in the specified folder.
    
    Args:
        content: String content to write to the file
        output_folder: Path to the output folder
        output_filename: Name of the output file (without extension)
        extension: File extension (with or without leading dot)
    
    Returns:
        str: Full path to the created file
    """
    # Ensure the output folder exists
    os.makedirs(output_folder, exist_ok=True)
    
    # Remove leading dot from extension if present
    if extension.startswith('.'):
        extension = extension[1:]
    
    # Construct the full file path
    file_path = os.path.join(output_folder, f"{output_filename}.{extension}")
    
    
    
    # Write the content to the file
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    return file_path

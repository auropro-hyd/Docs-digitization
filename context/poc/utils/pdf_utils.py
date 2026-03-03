import os
from pathlib import Path
from typing import List
from PyPDF2 import PdfReader, PdfWriter
import fitz  # PyMuPDF


def split_pdf(input_pdf_path, output_directory, pages_per_chunk=5):
    """
    Split a PDF file into smaller chunks.
    
    Args:
        input_pdf_path (str): Path to the input PDF file
        output_directory (str): Directory where split PDFs will be saved
        pages_per_chunk (int): Number of pages per chunk (default: 5)
    """
    # Create output directory if it doesn't exist
    os.makedirs(output_directory, exist_ok=True)
    
    # Read the PDF
    pdf_reader = PdfReader(input_pdf_path)
    total_pages = len(pdf_reader.pages)
    
    # Get the base name of the input file (without extension)
    input_filename = Path(input_pdf_path).stem
    
    print(f"Total pages in PDF: {total_pages}")
    print(f"Splitting into chunks of {pages_per_chunk} pages...\n")
    
    # Split PDF into chunks
    for start_page in range(0, total_pages, pages_per_chunk):
        end_page = min(start_page + pages_per_chunk - 1, total_pages - 1)
        
        # Create a new PDF writer for this chunk
        pdf_writer = PdfWriter()
        
        # Add pages to the chunk
        for page_num in range(start_page, end_page + 1):
            pdf_writer.add_page(pdf_reader.pages[page_num])
        
        # Create output filename (1-indexed for user-friendly naming)
        output_filename = f"{input_filename}-{start_page + 1}-{end_page + 1}.pdf"
        output_path = os.path.join(output_directory, output_filename)
        
        # Write the chunk to a file
        with open(output_path, 'wb') as output_file:
            pdf_writer.write(output_file)
        
        print(f"Created: {output_filename} (Pages {start_page + 1} to {end_page + 1})")
    
    print(f"\nSplitting complete! Files saved to: {output_directory}")

def get_pdf_files(input_folder: Path) -> List[Path]:
        """
        Get all PDF files from the input folder.

        Returns:
            List[Path]: List of PDF file paths
        """
        pdf_files = list(input_folder.glob("*.pdf"))
        return pdf_files

def pdf_to_jpeg_array(pdf_path, output_directory, dpi=400):
    """
    Convert a PDF file to JPEG images and save them to a directory.
    Uses PyMuPDF (no external dependencies required).
    
    Args:
        pdf_path (str): Path to the input PDF file
        output_directory (str): Directory where JPEG images will be saved
        dpi (int): Resolution for the output images (default: 400 DPI)
    
    Returns:
        list: Array of file paths for the saved JPEG images
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    
    # Create output directory if it doesn't exist
    os.makedirs(output_directory, exist_ok=True)
    
    # Get the base name of the input file
    input_filename = Path(pdf_path).stem
    
    # Open the PDF
    pdf_document = fitz.open(pdf_path)
    page_count = len(pdf_document)
    
    print(f"Converting {page_count} pages to JPEG format...\n")
    
    # Calculate zoom factor for desired DPI (default PDF DPI is 72)
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)
    
    # Save each page as a JPEG file
    saved_files = []
    for page_num in range(page_count):
        # Get the page
        page = pdf_document[page_num]
        
        # Render page to an image
        pix = page.get_pixmap(matrix=mat)
        
        # Create output filename (1-indexed)
        output_filename = f"{input_filename}-page-{page_num + 1}.jpg"
        output_path = os.path.join(output_directory, output_filename)
        
        # Save the image as JPEG
        pix.save(output_path)
        saved_files.append(output_path)
        
        print(f"Saved: {output_filename}")
    
    # Close the PDF
    pdf_document.close()
    
    print(f"\nConversion complete! {len(saved_files)} images saved to: {output_directory}")
    
    return saved_files

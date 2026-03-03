import os
from utils import split_pdf, pdf_to_jpeg_array


if __name__ == "__main__":
    # ========== PLACEHOLDERS - UPDATE THESE VALUES ==========
    
    # Input PDF file path
    INPUT_PDF_PATH = "D:\\Workspace\\apitoria-use-case\\output\\2538104192-EHSII03-6-10.pdf"
    
    # Output directory for split PDFs
    OUTPUT_DIRECTORY = "D:\\Workspace\\apitoria-use-case\\output_img"
    
    # ========================================================
    
    # Validate input file exists
    if not os.path.exists(INPUT_PDF_PATH):
        print(f"Error: Input file not found: {INPUT_PDF_PATH}")
        exit(1)
    
    # ===== TEST: Split PDF functionality =====
    # Split the PDF
    #INPUT_PDF_PATH = "D:\\Workspace\\apitoria-use-case\\2538104192-EHSII03.pdf"
    
    # Output directory for split PDFs
    #OUTPUT_DIRECTORY = "D:\\Workspace\\apitoria-use-case\\output"
    # split_pdf(INPUT_PDF_PATH, OUTPUT_DIRECTORY, pages_per_chunk=5)
    
    # ===== TEST: PDF to JPEG conversion =====
    # Convert PDF pages to JPEG images
    jpeg_files = pdf_to_jpeg_array(INPUT_PDF_PATH, OUTPUT_DIRECTORY, dpi=200)
    print(f"\nGenerated {len(jpeg_files)} JPEG files")

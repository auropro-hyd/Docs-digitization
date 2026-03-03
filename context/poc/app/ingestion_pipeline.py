import asyncio
import logging
from pathlib import Path
from infra.ChandraExtraction import ChandraExtraction
from utils.file_utils import create_file_with_content, save_base64_images_to_files
from utils.pdf_utils import get_pdf_files, split_pdf, pdf_to_jpeg_array

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """
    Ingestion pipeline for processing PDF documents and generating markdown files.
    """

    def __init__(self, input_dpcr_doc_path: str, output_folder: str):
        """
        Initialize the ingestion pipeline.

        Args:
            input_dpcr_doc_path: Path to the folder containing PDF files
            output_folder: Path to the folder where markdown files will be saved
        """
        self.input_dpcr_doc_path = Path(input_dpcr_doc_path)
        self.output_folder = Path(output_folder)
        self.extractor = ChandraExtraction()
        self.staging_folder = self.output_folder.parent.joinpath("staging").joinpath("pdf_chunks")
        self.image_staging_folder = self.output_folder.parent.joinpath("staging").joinpath("images")

        # Validate input folder exists
        if not self.input_dpcr_doc_path.exists():
            raise FileNotFoundError(f"Input folder not found: {self.input_dpcr_doc_path}")

        # Create output folder if it doesn't exist
        self.output_folder.mkdir(parents=True, exist_ok=True)

    async def process_single_pdf(self, pdf_path: Path) -> None:
        """
        Process a single PDF file and generate markdown.

        Args:
            pdf_path: Path to the PDF file
        """
        try:
            logger.info(f"Processing: {pdf_path.name}")

            # Parse PDF to markdown using ChandraExtraction
            markdown_content,images = await self.extractor.parse(pdf_path)

            if markdown_content:
                # Generate output filename (same as PDF name but with .md extension)
                output_filename = pdf_path.stem

                # Save markdown file
                output_path = create_file_with_content(
                    content=markdown_content,
                    output_folder=str(self.output_folder),
                    output_filename=output_filename,
                    extension="md"
                )

                logger.info(f"Successfully created: {output_path}")
                if images:
                    save_base64_images_to_files(images, str(self.output_folder))
                    list_image_files = ', '.join(images.keys())
                    logger.info(f"Successfully saved images: {list_image_files}")
            else:
                logger.error(f"Failed to parse {pdf_path.name}: No content returned")

        except Exception as e:
            logger.error(f"Error processing {pdf_path.name}: {str(e)}")

    async def process_all_pdfs(self, test_mode: bool = False) -> None:
        """
        Process all PDF files in the input folder.
        
        Args:
            test_mode: If True, process only the first PDF file for testing
        """
        split_pdf(self.input_dpcr_doc_path, self.staging_folder,pages_per_chunk=5)
        pdf_files = get_pdf_files(self.staging_folder)
        pdf_to_jpeg_array(self.input_dpcr_doc_path, self.image_staging_folder)
        if not pdf_files:
            logger.warning(f"No PDF files found in {self.staging_folder}")
            return

        if test_mode:
            pdf_files = pdf_files[:1]  # Process only the first PDF for testing
            logger.info(f"TEST MODE: Processing only the first PDF file: {pdf_files[0].name}")
        else:
            logger.info(f"Starting ingestion pipeline for {len(pdf_files)} files")

        # Process PDFs
        tasks = [self.process_single_pdf(pdf_path) for pdf_path in pdf_files]
        await asyncio.gather(*tasks)

        logger.info("Ingestion pipeline completed")

    async def run(self, test_mode: bool = False) -> None:
        """
        Run the ingestion pipeline.
        
        Args:
            test_mode: If True, process only the first PDF file for testing
        """
        await self.process_all_pdfs(test_mode=test_mode)


async def main(input_dpcr_doc_path: str, output_folder: str, test_mode: bool = True) -> None:
    """
    Main entry point for the ingestion pipeline.

    Args:
        input_dpcr_doc_path: Path to the folder containing PDF files
        output_folder: Path to the folder where markdown files will be saved
        test_mode: If True, process only the first PDF file for testing (default: True)
    """
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    pipeline = IngestionPipeline(input_dpcr_doc_path, output_folder)
    await pipeline.run(test_mode=test_mode)


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv()
    if len(sys.argv) < 3:
        print("Usage: python ingestion_pipeline.py <input_dpcr_doc_path> <output_folder> [--all]")
        print("  --all: Process all PDFs (default: test mode with first PDF only)")
        sys.exit(1)

    input_dpcr_doc_path = sys.argv[1]
    output_folder = sys.argv[2]
    
    # Check if --all flag is provided
    test_mode = "--all" not in sys.argv

    asyncio.run(main(input_dpcr_doc_path, output_folder, test_mode=test_mode))

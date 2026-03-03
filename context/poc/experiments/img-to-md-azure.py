from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeResult
import os
import time
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def _in_span(word, spans):
    for span in spans:
        if word.span.offset >= span.offset and (word.span.offset + word.span.length) <= (span.offset + span.length):
            return True
    return False

def _format_polygon(polygon):
    if not polygon:
        return "N/A"
    return ", ".join([f"[{polygon[i]}, {polygon[i + 1]}]" for i in range(0, len(polygon), 2)])

endpoint = os.getenv("DOCUMENTINTELLIGENCE_ENDPOINT")
key = os.getenv("DOCUMENTINTELLIGENCE_API_KEY")

if not endpoint or not key:
    raise ValueError("DOCUMENTINTELLIGENCE_ENDPOINT and DOCUMENTINTELLIGENCE_API_KEY must be set in .env file")

path_of_sample_documents = "D:\\Workspace\\apitoria-use-case\\output_img\\2538104192-EHSII03-page-3.jpg"

print(f"Starting analysis of: {path_of_sample_documents}")
print("=" * 60)

document_intelligence_client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))

# Start timing
start_time = time.time()

with open(path_of_sample_documents, "rb") as f:
    poller = document_intelligence_client.begin_analyze_document("prebuilt-layout", body=f)
result: AnalyzeResult = poller.result()

# End timing
end_time = time.time()
total_time = end_time - start_time

# Check for handwritten content (commented out for brevity)
# if result.styles and any([style.is_handwritten for style in result.styles]):
#     print("Document contains handwritten content")
# else:
#     print("Document does not contain handwritten content")

# Process each page
for page in result.pages:
    page_start_time = time.time()
    
    print(f"\n----Page #{page.page_number}----")
    print(f"Dimensions: {page.width} x {page.height} ({page.unit})")
    
    # Word and line level analysis (commented out for brevity)
    # if page.lines:
    #     for line_idx, line in enumerate(page.lines):
    #         words = []
    #         if page.words:
    #             for word in page.words:
    #                 print(f"......Word '{word.content}' has a confidence of {word.confidence}")
    #                 if _in_span(word, line.spans):
    #                     words.append(word)
    #         print(
    #             f"...Line # {line_idx} has word count {len(words)} and text '{line.content}' "
    #             f"within bounding polygon '{_format_polygon(line.polygon)}'"
    #         )
    
    # Selection marks (commented out for brevity)
    # if page.selection_marks:
    #     for selection_mark in page.selection_marks:
    #         print(
    #             f"Selection mark is '{selection_mark.state}' within bounding polygon "
    #             f"'{_format_polygon(selection_mark.polygon)}' and has a confidence of {selection_mark.confidence}"
    #         )
    
    page_end_time = time.time()
    page_processing_time = page_end_time - page_start_time
    print(f"Page processing time: {page_processing_time:.2f} seconds")

# Final Results Summary
print("\n" + "=" * 60)
print("FINAL RESULTS")
print("=" * 60)

if result.paragraphs:
    print(f"\nDetected {len(result.paragraphs)} paragraphs:")
    # Sort all paragraphs by span's offset to read in the right order.
    result.paragraphs.sort(key=lambda p: (p.spans.sort(key=lambda s: s.offset), p.spans[0].offset))
    for idx, paragraph in enumerate(result.paragraphs, 1):
        print(f"\n[Paragraph {idx}] Role: {paragraph.role or 'N/A'}")
        print(f"Content: {paragraph.content}")
        # Detailed bounding regions (commented out for brevity)
        # if paragraph.bounding_regions:
        #     print(", ".join(
        #         f" Page #{region.page_number}: {_format_polygon(region.polygon)} bounding region"
        #         for region in paragraph.bounding_regions
        #     ))

if result.tables:
    print(f"\nDetected {len(result.tables)} tables:")
    for table_idx, table in enumerate(result.tables):
        print(f"\n[Table {table_idx + 1}] {table.row_count} rows x {table.column_count} columns")
        
        # Table cell details (commented out for brevity)
        # if table.bounding_regions:
        #     for region in table.bounding_regions:
        #         print(f"Table location on page {region.page_number}: {_format_polygon(region.polygon)}")
        # for cell in table.cells:
        #     print(f"Cell[{cell.row_index}][{cell.column_index}]: '{cell.content}'")

print("\n" + "=" * 60)
print(f"Total processing time: {total_time:.2f} seconds")
print(f"Average time per page: {total_time / len(result.pages):.2f} seconds")
print("=" * 60)
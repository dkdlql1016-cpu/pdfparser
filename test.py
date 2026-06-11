import opendataloader_pdf


opendataloader_pdf.convert(
    input_path=[".\\input\\ifrs_checklist.pdf"],
    output_dir="output/",
    format="markdown",
    table_method="default",
    reading_order="xycut",
    markdown_page_separator = "--- %page-number% ---",
)

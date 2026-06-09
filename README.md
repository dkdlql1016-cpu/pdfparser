# PDF Diff Viewer - One Run Final

## 실행

```powershell
cd pdf_diff_viewer_one_run
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# opendataloader_pdf도 같은 venv에 설치/사용 가능해야 함
python app.py
```

브라우저:

```text
http://127.0.0.1:8000
```

## 이제 패치 파일 실행 필요 없음

이 버전은 아래 기능이 전부 app.py에 통합되어 있음.

1. old/new PDF 업로드
2. opendataloader_pdf.convert로 markdown만 생성
3. diff_extract.py 실행 후 result.json 생성
4. result.json + PDF bbox alignment
5. 서버에서 word highlight를 line chunk로 병합
6. 선택 테두리는 inset 방식으로 표시
7. result.json, old.md, new.md, viewer_data.json 다운로드

## opendataloader 설정

```python
opendataloader_pdf.convert(
    input_path=[str(pdf_path)],
    output_dir=str(output_dir),
    format="markdown",
    table_method="default",
    markdown_page_separator="--- %page-number% ---",
    reading_order="xycut",
)
```

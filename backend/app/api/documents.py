'''
Document reading endpoint (plan 1 of 4).

POST /api/documents/read takes ONE file as the raw request body (not a multipart form, so no extra
upload library is needed), works out the real type from the first bytes (never from a file name) and
returns the text. It stores nothing, logs no file names or contents and calls no model.

It has its OWN key, INTERNAL_DOCS_KEY, sent in the X-Internal-Docs-Key header. No agent key opens it.
Fail closed: until the key is set on the server the endpoint answers 503.

Supported: PDF with a text layer, .docx, .xlsx and plain text. Old .doc/.xls files, password-protected
files and image-only PDFs get a clear refusal. The limits below are assumptions and easy to change.
'''

from __future__ import annotations

import io
import zipfile

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from app.api.security import require_internal_key

DOCS_KEY_ENV = 'INTERNAL_DOCS_KEY'
DOCS_KEY_HEADER = 'X-Internal-Docs-Key'

# Limits (illustrative assumptions, change here).
MAX_BYTES = 3 * 1024 * 1024
MAX_CHARS = 60_000
MAX_PDF_PAGES = 80
MAX_SHEETS = 10
MAX_ROWS = 2000
MAX_COLS = 50
MAX_ZIP_ENTRIES = 2000
MAX_ZIP_UNPACKED = 50 * 1024 * 1024

router = APIRouter()


class DocumentRefused(Exception):
    '''A clear, safe refusal that is shown to the caller as is.'''

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class _Budget:
    '''Collects text up to a character limit and remembers whether it had to cut.'''

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.parts: list[str] = []
        self.used = 0
        self.truncated = False

    def add(self, text: str) -> bool:
        '''Add text. Returns False once the budget is full and nothing more should be added.'''
        if self.truncated:
            return False
        remaining = self.limit - self.used
        if len(text) > remaining:
            self.parts.append(text[:remaining])
            self.used = self.limit
            self.truncated = True
            return False
        self.parts.append(text)
        self.used += len(text)
        return True

    def text(self) -> str:
        return ''.join(self.parts)


def _detect_zip_type(data: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise DocumentRefused(415, 'This file is not a readable Word or Excel file.')
    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_ZIP_ENTRIES:
            raise DocumentRefused(413, 'This file contains too many parts to read safely.')
        if sum(entry.file_size for entry in entries) > MAX_ZIP_UNPACKED:
            raise DocumentRefused(413, 'This file would unpack to more than the safe limit.')
        names = {entry.filename for entry in entries}
    if 'word/document.xml' in names:
        return 'docx'
    if 'xl/workbook.xml' in names:
        return 'xlsx'
    raise DocumentRefused(415, 'Only Word (.docx) and Excel (.xlsx) archives are supported.')


def _detect_type(data: bytes) -> str:
    if data[:5] == b'%PDF-':
        return 'pdf'
    if data[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
        raise DocumentRefused(
            415,
            'Old Word/Excel files (.doc, .xls) and password-protected files are not supported. '
            'Please save as .docx or .xlsx without a password.',
        )
    if data[:4] == b'PK\x03\x04':
        return _detect_zip_type(data)
    if b'\x00' in data[:8192]:
        raise DocumentRefused(415, 'This file type is not supported. Use PDF, .docx, .xlsx or plain text.')
    return 'text'


def _read_text(data: bytes, budget: _Budget, notes: list[str]) -> None:
    budget.add(data.decode('utf-8-sig', errors='replace'))


def _read_pdf(data: bytes, budget: _Budget, notes: list[str]) -> None:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        total_pages = len(pdf.pages)
        if total_pages > MAX_PDF_PAGES:
            notes.append(f'Only the first {MAX_PDF_PAGES} of {total_pages} pages were read.')
        for page in pdf.pages[:MAX_PDF_PAGES]:
            page_text = page.extract_text() or ''
            if page_text.strip():
                if not budget.add(page_text + '\n\n'):
                    break
    if not budget.text().strip():
        raise DocumentRefused(
            422,
            'This PDF has no text layer (it may be scanned images). Scanned PDFs are not supported.',
        )


def _read_docx(data: bytes, budget: _Budget, notes: list[str]) -> None:
    import docx

    document = docx.Document(io.BytesIO(data))
    notes.append('Word text is returned as paragraphs first, then tables.')
    for paragraph in document.paragraphs:
        if paragraph.text.strip():
            if not budget.add(paragraph.text + '\n'):
                return
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip().replace('\n', ' ') for cell in row.cells]
            if any(cells):
                if not budget.add(' | '.join(cells) + '\n'):
                    return


def _read_xlsx(data: bytes, budget: _Budget, notes: list[str]) -> None:
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        sheets = list(workbook.worksheets)
        if len(sheets) > MAX_SHEETS:
            notes.append(f'Only the first {MAX_SHEETS} of {len(sheets)} sheets were read.')
        for sheet in sheets[:MAX_SHEETS]:
            if not budget.add(f'## Sheet: {sheet.title}\n'):
                return
            for row in sheet.iter_rows(min_row=1, max_row=MAX_ROWS, max_col=MAX_COLS, values_only=True):
                cells = ['' if value is None else str(value).replace('\t', ' ').replace('\n', ' ') for value in row]
                if any(cell.strip() for cell in cells):
                    if not budget.add('\t'.join(cells).rstrip() + '\n'):
                        return
            budget.add('\n')
        notes.append(f'Each sheet is limited to {MAX_ROWS} rows and {MAX_COLS} columns.')
    finally:
        workbook.close()


def _extract(data: bytes) -> dict:
    if not data:
        raise DocumentRefused(400, 'The file is empty.')
    kind = _detect_type(data)
    budget = _Budget(MAX_CHARS)
    notes: list[str] = []
    if kind == 'pdf':
        _read_pdf(data, budget, notes)
    elif kind == 'docx':
        _read_docx(data, budget, notes)
    elif kind == 'xlsx':
        _read_xlsx(data, budget, notes)
    else:
        _read_text(data, budget, notes)
    text = budget.text()
    if not text.strip():
        raise DocumentRefused(422, 'No readable text was found in this file.')
    if budget.truncated:
        notes.append(f'The text was cut at {MAX_CHARS} characters.')
    return {
        'file_type': kind,
        'text': text,
        'characters': len(text),
        'truncated': budget.truncated,
        'notes': notes,
    }


@router.post('/api/documents/read')
async def read_document(
    request: Request,
    x_internal_docs_key: str | None = Header(default=None, alias=DOCS_KEY_HEADER),
):
    # The key is checked BEFORE the body is read, so a caller without it costs us nothing.
    require_internal_key(x_internal_docs_key, DOCS_KEY_ENV, DOCS_KEY_HEADER)

    declared = request.headers.get('content-length')
    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
        raise HTTPException(status_code=413, detail='The file is larger than the 3 MB limit.')

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BYTES:
            raise HTTPException(status_code=413, detail='The file is larger than the 3 MB limit.')
        chunks.append(chunk)
    data = b''.join(chunks)

    try:
        return await run_in_threadpool(_extract, data)
    except DocumentRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except ImportError:
        print('DOCUMENT READ: a reader library is not installed on this server', flush=True)
        raise HTTPException(status_code=503, detail='This file type cannot be read on this server yet.') from None
    except Exception as exc:
        # Only the error CLASS is logged, never the file name, contents or the library's message.
        print(f'DOCUMENT READ: failed with {type(exc).__name__}', flush=True)
        raise HTTPException(
            status_code=422,
            detail='The file could not be read. It may be damaged or password-protected.',
        ) from None

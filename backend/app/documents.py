"""Parse uploaded files into located sections, then split them into chunks.

Every chunk keeps a human-readable location (heading, page, sheet/row) so
answers can cite exactly where a claim comes from.
"""
import re
from dataclasses import dataclass
from pathlib import Path

SUPPORTED = {'.pdf', '.docx', '.xlsx', '.md', '.txt'}


@dataclass
class Section:
    location: str
    text: str


def parse(path: Path) -> list[Section]:
    suffix = path.suffix.lower()
    if suffix == '.pdf':
        return _parse_pdf(path)
    if suffix == '.docx':
        return _parse_docx(path)
    if suffix == '.xlsx':
        return _parse_xlsx(path)
    if suffix in ('.md', '.txt'):
        return _parse_markdown(path.read_text(encoding='utf-8'))
    raise ValueError(f'不支持的文件类型 {suffix}，支持：{", ".join(sorted(SUPPORTED))}')


def _parse_pdf(path):
    from pypdf import PdfReader
    return [Section(f'第{number}页', text)
            for number, page in enumerate(PdfReader(path).pages, 1)
            if (text := (page.extract_text() or '').strip())]


def _parse_docx(path):
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(path)
    sections, heading, lines = [], '正文', []

    def flush():
        if text := '\n'.join(lines).strip():
            sections.append(Section(heading, text))
        lines.clear()

    for element in document.element.body.iterchildren():
        tag = element.tag.rsplit('}', 1)[-1]
        if tag == 'p':
            paragraph = Paragraph(element, document)
            text = paragraph.text.strip()
            style = paragraph.style.name if paragraph.style is not None else ''
            if text and (style.startswith('Heading') or style.startswith('标题') or style == 'Title'):
                flush()
                heading = text
            elif text:
                lines.append(text)
        elif tag == 'tbl':
            for row in Table(element, document).rows:
                cells = [cell.text.strip() for cell in row.cells]
                # Merged cells repeat their text; drop adjacent duplicates.
                cells = [cell for index, cell in enumerate(cells) if index == 0 or cell != cells[index - 1]]
                lines.append(' | '.join(cells))
    flush()
    return sections


def _parse_xlsx(path, rows_per_section=5):
    from openpyxl import load_workbook
    workbook = load_workbook(path, read_only=True, data_only=True)
    sections = []
    for sheet in workbook.worksheets:
        rows = [[('' if value is None else str(value).strip()) for value in row]
                for row in sheet.iter_rows(values_only=True)]
        rows = [row for row in rows if any(row)]
        if not rows:
            continue
        header, body = rows[0], rows[1:]
        for start in range(0, len(body), rows_per_section):
            block = body[start:start + rows_per_section]
            # Row numbers are spreadsheet rows: header is row 1.
            lines = ['；'.join(f'{name}：{value}' for name, value in zip(header, row) if value)
                     for row in block]
            sections.append(Section(f'工作表「{sheet.title}」第{start + 2}-{start + len(block) + 1}行',
                                    '\n'.join(lines)))
    workbook.close()
    return sections


def _parse_markdown(text):
    sections, path, lines = [], [], []

    def flush():
        if body := '\n'.join(lines).strip():
            sections.append(Section(' > '.join(path) or '正文', body))
        lines.clear()

    for line in text.splitlines():
        if match := re.match(r'^(#{1,6})\s+(.*)', line):
            flush()
            level = len(match.group(1))
            path[:] = path[:level - 1] + [match.group(2).strip()]
        else:
            lines.append(line)
    flush()
    return sections


def chunk(sections: list[Section], max_chars=600, overlap=120) -> list[Section]:
    """Split long sections on line boundaries. The last line of a piece is repeated at the
    start of the next when it is short enough, so context carries across the cut."""
    chunks = []
    for section in sections:
        if len(section.text) <= max_chars:
            chunks.append(section)
            continue
        lines = []
        for line in section.text.split('\n'):
            # A single over-long line is cut into overlapping windows.
            step = max_chars - overlap
            lines.extend(line[i:i + max_chars] for i in range(0, max(len(line) - overlap, 1), step))
        current: list[str] = []
        for line in lines:
            if current and sum(map(len, current)) + len(current) + len(line) > max_chars:
                chunks.append(Section(section.location, '\n'.join(current)))
                current = [current[-1]] if len(current[-1]) <= overlap and len(current) > 1 else []
            current.append(line)
        if any(line.strip() for line in current):
            chunks.append(Section(section.location, '\n'.join(current)))
    return chunks

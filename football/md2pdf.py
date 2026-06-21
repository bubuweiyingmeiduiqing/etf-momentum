"""
Markdown to PDF converter for football analysis reports.
Handles: headings, tables, bold, italic, inline code, lists, horizontal rules, emoji.
"""
import re
import sys
import os
from pathlib import Path
from fpdf import FPDF

# ── Font setup ──────────────────────────────────────────────────
FONT_DIR = Path(__file__).parent / ".fonts"
FONT_DIR.mkdir(exist_ok=True)

# Chinese-capable TTF already present on Windows 10
CN_FONT = "C:/Windows/Fonts/msyh.ttc"       # 微软雅黑 (supports Chinese)
EMOJI_FONT = "C:/Windows/Fonts/seguiemj.ttf"  # Segoe UI Emoji

if not os.path.exists(CN_FONT):
    CN_FONT = "C:/Windows/Fonts/simsun.ttc"  # fallback: 宋体


class FootballPDF(FPDF):
    def __init__(self):
        super().__init__('P', 'mm', 'A4')
        self.add_font("CN", "", CN_FONT)
        self.add_font("CN", "B", CN_FONT)  # same file, bold simulated by fpdf2
        self.add_font("Emoji", "", EMOJI_FONT)
        self.set_auto_page_break(True, 15)
        self.cjk_line = ""

    # ── helpers ──
    def h1(self, text): self._heading(text, 16, 10, 6)
    def h2(self, text): self._heading(text, 14, 8, 5)
    def h3(self, text): self._heading(text, 12, 6, 4)

    def _heading(self, text, size, before, after):
        self.ln(before)
        self.set_font("CN", "B", size)
        self.set_text_color(0, 51, 102)
        self.multi_cell(0, size * 0.45, text, align='L')
        self.set_text_color(0, 0, 0)
        self.ln(after)

    def body(self, text):
        self.set_x(self.l_margin)
        if not text.strip():
            self.ln(3)
            return
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)
        self.set_font("CN", "", 9)
        avail_w = self.w - self.r_margin - self.l_margin
        self.multi_cell(avail_w, 5.5, text, align='L')

    def bullet(self, text):
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        self.set_font("CN", "", 9)
        indent = 5
        self.set_x(self.l_margin + indent)
        self.cell(4, 5.5, "•")
        self.ln()
        self.set_x(self.l_margin + indent + 5)
        avail_w = self.w - self.r_margin - self.l_margin - indent - 5
        self.multi_cell(avail_w, 5.5, text, align='L')
        self.set_x(self.l_margin)

    def numbered(self, num, text):
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        self.set_font("CN", "", 9)
        indent = 5
        self.set_x(self.l_margin + indent)
        self.cell(4, 5.5, f"{num}.")
        self.ln()
        self.set_x(self.l_margin + indent + 5)
        avail_w = self.w - self.r_margin - self.l_margin - indent - 5
        self.multi_cell(avail_w, 5.5, text, align='L')
        self.set_x(self.l_margin)

    def hr(self):
        self.ln(3)
        self.set_draw_color(200, 200, 200)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(3)

    def table(self, rows):
        """rows: list of lists; first row = header"""
        if not rows: return
        self.set_font("CN", "", 8)
        col_w = self._col_widths(rows)
        for i, row in enumerate(rows):
            self._table_row(row, col_w, header=(i == 0))
        self.ln(2)

    def _col_widths(self, rows):
        n = max(len(r) for r in rows)
        usable = self.w - self.l_margin - self.r_margin
        return [usable / n] * n

    def _table_row(self, row, widths, header=False):
        row_h = 6.5
        if header:
            self.set_fill_color(0, 51, 102)
            self.set_text_color(255, 255, 255)
            self.set_font("CN", "B", 8)
        else:
            self.set_fill_color(248, 248, 248) if self.page_no() % 2 == 0 else self.set_fill_color(255, 255, 255)
            self.set_text_color(0, 0, 0)
            self.set_font("CN", "", 8)

        # calculate max lines needed
        max_lines = 1
        cell_texts = []
        for j, w in enumerate(widths):
            txt = str(row[j]) if j < len(row) else ""
            txt = self.multi_cell(w - 2, row_h, txt, dry_run=True, output="LINES")
            cell_texts.append(txt)
            max_lines = max(max_lines, len(txt))

        actual_h = row_h * max_lines

        # check page break
        if self.get_y() + actual_h > self.h - self.b_margin:
            self.add_page()
            if header:
                self.set_fill_color(0, 51, 102)
                self.set_text_color(255, 255, 255)

        y_before = self.get_y()
        x_start = self.get_x()

        for j, w in enumerate(widths):
            x_pos = x_start + sum(widths[:j])
            self.set_xy(x_pos, y_before)
            # draw cell bg
            self.rect(x_pos, y_before, w, actual_h, 'FD' if header else 'D')
            if header:
                self.set_fill_color(0, 51, 102)
                self.set_text_color(255, 255, 255)
            else:
                self.set_text_color(0, 0, 0)

            # write text
            self.set_xy(x_pos + 1, y_before + 0.5)
            txt = str(row[j]) if j < len(row) else ""
            self.multi_cell(w - 2, row_h, txt, align='L')

        self.set_xy(x_start, y_before + actual_h)

    def quote(self, text):
        """Blockquote (for > lines)"""
        self.set_font("CN", "", 9)
        self.set_text_color(80, 80, 80)
        x = self.get_x()
        self.set_fill_color(230, 230, 230)
        self.rect(x, self.get_y(), 3, 5.5 * (text.count('\n') + 1), 'F')
        self.set_x(x + 5)
        self.multi_cell(0, 5.5, text, align='L')
        self.set_text_color(0, 0, 0)

    def code_block(self, text):
        """Inline code / small block"""
        self.set_font("CN", "", 8)
        self.set_fill_color(240, 240, 240)
        for line in text.split('\n'):
            self.cell(0, 4.5, f"  {line}", fill=True)
            self.ln()


# ── Markdown Parser ──────────────────────────────────────────────
def parse_md(text):
    """Simple line-by-line markdown → PDF calls"""
    pdf = FootballPDF()
    pdf.add_page()
    lines = text.split('\n')
    i = 0
    in_table = False
    table_rows = []
    in_code = False
    code_buf = []

    while i < len(lines):
        line = lines[i]

        # Code block
        if line.strip().startswith('```'):
            if in_code:
                pdf.code_block('\n'.join(code_buf))
                code_buf = []
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        # Horizontal rule
        if re.match(r'^[-*_]{3,}\s*$', line.strip()):
            if in_table:
                pdf.table(table_rows)
                table_rows = []
                in_table = False
            pdf.hr()
            i += 1
            continue

        # Empty line
        if not line.strip():
            if in_table:
                pdf.table(table_rows)
                table_rows = []
                in_table = False
            else:
                pdf.body("")
            i += 1
            continue

        # Heading
        h_match = re.match(r'^(#{1,3})\s+(.+)$', line)
        if h_match and not in_table:
            level, content = len(h_match.group(1)), h_match.group(2).strip()
            content = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', content)  # strip links
            if level == 1: pdf.h1(content)
            elif level == 2: pdf.h2(content)
            else: pdf.h3(content)
            i += 1
            continue

        # Table detection: | ... | ... |
        if '|' in line and line.strip().startswith('|'):
            cells = [c.strip() for c in line.strip().split('|')[1:-1]]
            # Skip separator row
            if all(re.match(r'^[-:]+$', c) for c in cells):
                i += 1
                continue
            table_rows.append(cells)
            in_table = True
            i += 1
            continue
        elif in_table:
            # End of table
            pdf.table(table_rows)
            table_rows = []
            in_table = False

        # Blockquote
        if line.strip().startswith('> '):
            content = line.strip()[2:]
            pdf.quote(content)
            i += 1
            continue

        # Bullet list
        bullet_match = re.match(r'^- (.+)$', line.strip())
        if bullet_match:
            # Clean markdown links for display
            content = bullet_match.group(1)
            content = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', content)
            pdf.bullet(content)
            i += 1
            continue

        # Numbered list
        num_match = re.match(r'^(\d+)\.\s+(.+)$', line.strip())
        if num_match:
            content = num_match.group(2)
            content = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', content)
            pdf.numbered(num_match.group(1), content)
            i += 1
            continue

        # Regular paragraph
        pdf.body(line.strip())
        i += 1

    # Flush remaining table
    if in_table and table_rows:
        pdf.table(table_rows)

    return pdf


import unicodedata

def strip_emoji(text):
    """Remove emoji and other non-renderable Unicode from text. Keep CJK, ASCII, Latin."""
    cleaned = []
    for ch in text:
        cp = ord(ch)
        # Keep: ASCII + basic Latin
        if cp < 0x024F:
            cleaned.append(ch)
        # CJK
        elif 0x2E80 <= cp <= 0x9FFF:
            cleaned.append(ch)
        elif 0xF900 <= cp <= 0xFAFF:
            cleaned.append(ch)
        elif 0xFE10 <= cp <= 0xFE6F:
            cleaned.append(ch)
        elif 0xFF00 <= cp <= 0xFFEF:
            cleaned.append(ch)
        # Common replacements for symbols used in our reports
        elif cp in (0x2713, 0x2714, 0x2705):
            cleaned.append('[OK]')
        elif cp in (0x2715, 0x2716, 0x274C, 0x274E):
            cleaned.append('[X]')
        elif cp == 0x26A0:
            cleaned.append('[!]')
        elif cp in (0x2B50, 0x2B55):
            cleaned.append('*')
        elif cp == 0x26BD:
            cleaned.append('')  # soccer ball → nothing
        elif cp == 0x26AB:
            cleaned.append('*')
        else:
            # Skip emoji, variation selectors, etc.
            cleaned.append('')
    return ''.join(cleaned)


def md_to_pdf(md_path, pdf_path=None):
    """Convert a markdown file to PDF."""
    if pdf_path is None:
        pdf_path = md_path.replace('.md', '.pdf')

    with open(md_path, 'r', encoding='utf-8') as f:
        md_text = f.read()

    # Strip emoji before rendering (fpdf2 can't render them with CN fonts)
    md_text = strip_emoji(md_text)

    pdf = parse_md(md_text)
    pdf.output(pdf_path)
    return pdf_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python md2pdf.py <markdown_file> [output_pdf]")
        sys.exit(1)

    md_file = sys.argv[1]
    out_file = sys.argv[2] if len(sys.argv) > 2 else None
    result = md_to_pdf(md_file, out_file)
    print(f"PDF saved: {result}")

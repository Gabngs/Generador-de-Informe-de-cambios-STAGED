#!/usr/bin/env python3
"""
git_diff_to_docx.py
────────────────────
Lee 'informe.txt' (en la misma carpeta) generado con:
    git --no-pager diff --staged > informe.txt

Genera un informe profesional .docx analizando la lógica
de los cambios, resumiendo archivos nuevos y detectando impacto.
"""

import sys
import re
import argparse
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Set

from docx import Document
from docx.shared import Pt, RGBColor, Inches, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

# ─────────────────────────────────────────────────────────────────────────────
# PALETA DE COLORES
# ─────────────────────────────────────────────────────────────────────────────
C_TITLE     = RGBColor(0x1E, 0x3A, 0x5F)
C_SUBTITLE  = RGBColor(0x55, 0x55, 0x55)
C_BODY      = RGBColor(0x22, 0x22, 0x22)
C_MUTED     = RGBColor(0x88, 0x88, 0x88)
C_ADD_TEXT  = RGBColor(0x16, 0x65, 0x34)
C_ADD_BG    = "F0FDF4"
C_DEL_TEXT  = RGBColor(0x99, 0x1B, 0x1B)
C_DEL_BG    = "FEF2F2"
C_MOD_TEXT  = RGBColor(0x1E, 0x3A, 0x5F)
C_MOD_BG    = "EFF6FF"
C_REF_TEXT  = RGBColor(0x92, 0x40, 0x0E)
C_REF_BG    = "FFFBEB"
C_HDR_BG    = "1E3A5F"
C_ACCENT    = "2563EB"
C_BORDER    = "CCCCCC"
C_ROW_ALT   = "F8FAFC"
C_WHITE     = "FFFFFF"

DEFAULT_INPUT  = "informe.txt"
DEFAULT_OUTPUT = "informe_cambios.docx"

ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def clean(text: str) -> str:
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = ANSI_RE.sub('', text)
    return text.replace('\x00', '')

# ─────────────────────────────────────────────────────────────────────────────
# MODELO: FileChange
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class FileChange:
    filepath: str
    added:    List[str] = field(default_factory=list)
    removed:  List[str] = field(default_factory=list)
    contexts: Set[str]  = field(default_factory=set) # Funciones o bloques modificados
    kind: str = "modified"
    is_binary: bool = False

    @property
    def filename(self) -> str:
        return Path(self.filepath).name

    @property
    def ext(self) -> str:
        name = self.filename.lower()
        for double in ('.component.ts', '.component.html', '.component.scss',
                       '.service.ts', '.spec.ts', '.module.ts', '.pipe.ts',
                       '.directive.ts', '.guard.ts', '.interceptor.ts'):
            if name.endswith(double):
                return double
        return Path(self.filepath).suffix.lower()

    @property
    def kind_label(self) -> str:
        if self.kind == 'added': return 'Adición'
        if self.kind == 'deleted': return 'Eliminación'
        if self.kind == 'renamed': return 'Renombrado'
        if self.is_binary: return 'Binario/Media'
        n_add, n_del = len(self.added), len(self.removed)
        if n_del == 0 and n_add > 0: return 'Adición'
        if n_add == 0 and n_del > 0: return 'Eliminación'
        if n_del > n_add * 2 and n_del > 10: return 'Refactor'
        if n_add == 0 and n_del == 0: return 'Configuración'
        return 'Modificación'

    @property
    def kind_colors(self) -> Tuple[RGBColor, str]:
        lbl = self.kind_label
        if lbl == 'Adición':     return C_ADD_TEXT, C_ADD_BG
        if lbl == 'Eliminación': return C_DEL_TEXT, C_DEL_BG
        if lbl == 'Refactor':    return C_REF_TEXT, C_REF_BG
        return C_MOD_TEXT, C_MOD_BG

    def extract_structure(self) -> Dict[str, List[str]]:
        """Analiza lógicamente el código para extraer imports, clases y funciones."""
        structure = {"imports": [], "entities": []}
        all_lines = self.added + self.removed
        
        import_pattern = re.compile(r'^\s*(import|from|require\(|include|using)\b')
        entity_pattern = re.compile(r'^\s*(export )?(class|def|function|interface|const \w+\s*=\s*\(|let \w+\s*=\s*\()')

        for line in all_lines:
            if import_pattern.search(line) and line not in structure["imports"]:
                structure["imports"].append(line.strip()[:80] + ('...' if len(line)>80 else ''))
            elif entity_pattern.search(line) and line not in structure["entities"]:
                clean_entity = line.strip().split('{')[0].strip()
                if clean_entity not in structure["entities"]:
                    structure["entities"].append(clean_entity)
        return structure

# ─────────────────────────────────────────────────────────────────────────────
# PARSER DE DIFF
# ─────────────────────────────────────────────────────────────────────────────
class DiffParser:
    _FILE   = re.compile(r'^diff --git a/(.+?) b/(.+)$')
    _NEW    = re.compile(r'^new file mode')
    _DEL    = re.compile(r'^deleted file mode')
    _REN    = re.compile(r'^rename to (.+)$')
    _BIN    = re.compile(r'^Binary files.*differ$')
    _HUNK   = re.compile(r'^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@\s*(.*)$')
    _PLUS3  = re.compile(r'^\+\+\+')
    _MIN3   = re.compile(r'^---')

    def parse(self, text: str) -> List[FileChange]:
        text = clean(text)
        files: List[FileChange] = []
        cur: Optional[FileChange] = None

        for line in text.splitlines():
            m_file = self._FILE.match(line)
            if m_file:
                cur = FileChange(filepath=m_file.group(2).strip())
                files.append(cur)
                continue

            if cur is None: continue

            if self._NEW.match(line):
                cur.kind = 'added'
            elif self._DEL.match(line):
                cur.kind = 'deleted'
            elif self._BIN.match(line):
                cur.is_binary = True
            elif self._REN.match(line):
                cur.kind = 'renamed'
                cur.filepath = self._REN.match(line).group(1).strip()
            elif m_hunk := self._HUNK.match(line):
                context_hint = m_hunk.group(1).strip()
                if context_hint and len(context_hint) > 2:
                    cur.contexts.add(context_hint[:60]) # Guardar nombre de función/bloque afectado
            elif self._PLUS3.match(line) or self._MIN3.match(line):
                continue
            elif line.startswith('+') and not line.startswith('+++'):
                s = line[1:].strip()
                if s: cur.added.append(s)
            elif line.startswith('-') and not line.startswith('---'):
                s = line[1:].strip()
                if s: cur.removed.append(s)

        return [f for f in files if f.filepath]

# ─────────────────────────────────────────────────────────────────────────────
# ANALIZADOR GENÉRICO 
# ─────────────────────────────────────────────────────────────────────────────
FILE_CATEGORIES: Dict[str, str] = {
    '.component.html': 'Template Angular', '.component.ts': 'Componente Angular',
    '.service.ts': 'Servicio Angular', '.spec.ts': 'Test Unitario',
    '.html': 'Template', '.scss': 'Estilos', '.css': 'Estilos',
    '.ts': 'TypeScript', '.js': 'JavaScript', '.py': 'Python',
    '.json': 'Configuración', '.md': 'Documentación', '.sql': 'Base de Datos',
}

IMPACT_SIGNALS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'\.subscribe\s*\('), 'Manejo de flujos asíncronos (RxJS)'),
    (re.compile(r'catchError|throwError|try\s*{|except\s+'), 'Gestión y control de excepciones'),
    (re.compile(r'apiUrl|API_URL|baseUrl|environ|\.env'), 'Cambio en variables de entorno o endpoints'),
    (re.compile(r'router\.navigate|HttpResponse|redirect'), 'Lógica de ruteo o navegación'),
    (re.compile(r'AuthService|token|JWT|password|hash|bcrypt', re.I), 'Capa de Seguridad y Autenticación'),
    (re.compile(r'console\.(log|warn|error)|print\('), 'Modificación de trazabilidad (Logs)'),
    (re.compile(r'SELECT|INSERT|UPDATE|DELETE|JOIN|Query', re.I), 'Interacción con Base de Datos / Consultas'),
    (re.compile(r'def |class |function '), 'Definición de nuevas estructuras de negocio'),
]

def analyze_impact(fc: FileChange) -> str:
    if fc.is_binary: return "Actualización de archivo binario (ej. Imagen, PDF)."
    
    all_lines = "\n".join(fc.added + fc.removed)
    found = set()

    for pattern, description in IMPACT_SIGNALS:
        if pattern.search(all_lines):
            found.add(description)

    if found:
        return " | ".join(found)

    category = FILE_CATEGORIES.get(fc.ext, 'Archivo')
    n_add, n_del = len(fc.added), len(fc.removed)

    if n_add == 0 and n_del == 0: return f"Ajuste de propiedades/permisos en {category}"
    if fc.kind == 'added': return f"Implementación base de {category}"
    if fc.kind == 'deleted': return f"Depuración/Eliminación de {category}"
    return f"Ajuste de lógica en {category}"

def analyze_recommendations(changes: List[FileChange]) -> List[str]:
    recs = []
    all_added   = "\n".join(l for f in changes for l in f.added).lower()
    all_removed = "\n".join(l for f in changes for l in f.removed).lower()
    
    if 'console.' in all_removed or 'print(' in all_removed:
        recs.append("Se limpiaron logs de depuración. Validar que no falten métricas críticas.")
    if 'apiurl' in all_added or 'baseurl' in all_added or '.env' in all_added:
        recs.append("Cambios en URLs o variables de entorno. Verificar despliegue en Staging/Producción.")
    if any(f.is_binary for f in changes):
        recs.append("Se modificaron archivos binarios. Verificar integridad de los assets visuales.")
    if any(f.kind == 'deleted' for f in changes):
        recs.append("Se eliminaron archivos. Ejecutar un build completo para asegurar que no hay dependencias rotas ('import' huérfanos).")
    
    has_ui = any(f.ext in ('.html', '.scss', '.css', '.vue', '.jsx', '.tsx') for f in changes)
    if has_ui:
        recs.append("Cambios detectados en la interfaz de usuario. Se sugiere QA visual en resoluciones móviles y de escritorio.")

    if not recs:
        recs.append("Revisar cobertura de pruebas unitarias para las nuevas estructuras de negocio añadidas.")
    return list(set(recs))

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS DOCX (Mantenidos igual para respetar tu diseño)
# ─────────────────────────────────────────────────────────────────────────────
def _set_bg(cell, hex_color: str):
    tcPr = cell._tc.get_or_add_tcPr()
    shd  = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)

def _set_borders(cell, color: str = "CCCCCC"):
    tcPr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for side in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"), "single"); el.set(qn("w:sz"), "4"); el.set(qn("w:space"), "0"); el.set(qn("w:color"), color)
        borders.append(el)
    tcPr.append(borders)

def _set_width(cell, cm: float):
    tcPr = cell._tc.get_or_add_tcPr()
    tcW  = OxmlElement("w:tcW")
    tcW.set(qn("w:w"), str(int(cm * 567))); tcW.set(qn("w:type"), "dxa")
    tcPr.append(tcW)

def _run(para, text: str, bold=False, italic=False, color: RGBColor = None, size: float = 10, font: str = "Arial"):
    r = para.add_run(text)
    r.bold, r.italic, r.font.size, r.font.name = bold, italic, Pt(size), font
    if color: r.font.color.rgb = color
    return r

def _header_row(table, cols: List[Tuple[str, float]]):
    row = table.rows[0]
    for i, (text, w) in enumerate(cols):
        cell = row.cells[i]
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        _set_bg(cell, C_HDR_BG); _set_borders(cell, C_ACCENT); _set_width(cell, w)
        p = cell.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _run(p, text, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))

def _data_row(table, cells: List[Tuple[str, float, RGBColor, bool, str]]):
    row = table.add_row()
    for i, (text, w, tc, bold, bg) in enumerate(cells):
        cell = row.cells[i]
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        _set_bg(cell, bg); _set_borders(cell); _set_width(cell, w)
        p = cell.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        for j, line in enumerate(text.split("\n")):
            if j > 0: p = cell.add_paragraph()
            _run(p, line, bold=bold, color=tc, size=9)

def _bullet(doc, text: str, symbol: str, color: RGBColor, indent: float = 1.0):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent, p.paragraph_format.first_line_indent, p.paragraph_format.space_after = Cm(indent), Cm(-0.5), Pt(3)
    _run(p, f"{symbol}  ", bold=True, color=color, size=10)
    _run(p, text, color=color, size=10)

def _section_title(doc, text: str):
    p = doc.add_paragraph()
    p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(12), Pt(5)
    _run(p, text, bold=True, color=C_TITLE, size=12)
    pPr, pBdr, bottom = p._p.get_or_add_pPr(), OxmlElement("w:pBdr"), OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single"); bottom.set(qn("w:sz"), "4"); bottom.set(qn("w:space"), "1"); bottom.set(qn("w:color"), C_ACCENT)
    pBdr.append(bottom); pPr.append(pBdr)

def _divider(doc):
    p = doc.add_paragraph()
    p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(6), Pt(6)
    pPr, pBdr, bot = p._p.get_or_add_pPr(), OxmlElement("w:pBdr"), OxmlElement("w:bottom")
    bot.set(qn("w:val"), "single"); bot.set(qn("w:sz"), "4"); bot.set(qn("w:space"), "1"); bot.set(qn("w:color"), C_BORDER)
    pBdr.append(bot); pPr.append(pBdr)

def _h1(doc, text: str):
    p = doc.add_paragraph()
    p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(14), Pt(7)
    _run(p, text, bold=True, color=C_TITLE, size=14)

# ─────────────────────────────────────────────────────────────────────────────
# GENERADOR DOCX
# ─────────────────────────────────────────────────────────────────────────────
class ReportGenerator:
    def __init__(self, changes: List[FileChange], branch_from: str, branch_to: str, output: str):
        self.changes, self.branch_from, self.branch_to, self.output = changes, branch_from, branch_to, output
        self.doc = Document()
        self._page_setup()

    def _page_setup(self):
        sec = self.doc.sections[0]
        sec.page_width, sec.page_height = Inches(8.5), Inches(11)
        sec.left_margin, sec.right_margin, sec.top_margin, sec.bottom_margin = Cm(2.5), Cm(2.5), Cm(2.0), Cm(2.0)
        style = self.doc.styles["Normal"]
        style.font.name, style.font.size = "Arial", Pt(10)

    def _title(self):
        fecha = datetime.now().strftime("%d de %B de %Y")
        p = self.doc.add_paragraph(); p.alignment, p.paragraph_format.space_after = WD_ALIGN_PARAGRAPH.CENTER, Pt(3)
        _run(p, "INFORME DE CAMBIOS DE CÓDIGO", bold=True, color=C_TITLE, size=20)
        p2 = self.doc.add_paragraph(); p2.alignment, p2.paragraph_format.space_after = WD_ALIGN_PARAGRAPH.CENTER, Pt(2)
        _run(p2, f"Rama: {self.branch_from}  →  {self.branch_to}", color=C_SUBTITLE, size=11)
        p3 = self.doc.add_paragraph(); p3.alignment, p3.paragraph_format.space_after = WD_ALIGN_PARAGRAPH.CENTER, Pt(12)
        _run(p3, f"Fecha: {fecha}", color=C_MUTED, size=10)
        p4 = self.doc.add_paragraph(); p4.paragraph_format.space_after = Pt(12)
        pPr, pBdr, bot = p4._p.get_or_add_pPr(), OxmlElement("w:pBdr"), OxmlElement("w:bottom")
        bot.set(qn("w:val"), "single"); bot.set(qn("w:sz"), "12"); bot.set(qn("w:space"), "1"); bot.set(qn("w:color"), C_ACCENT)
        pBdr.append(bot); pPr.append(pBdr)

    def _summary(self):
        _h1(self.doc, "1. Tabla Resumen de Cambios")
        total_add = sum(len(f.added) for f in self.changes)
        total_del = sum(len(f.removed) for f in self.changes)
        p = self.doc.add_paragraph(); p.paragraph_format.space_after = Pt(6)
        _run(p, f"Archivos afectados: {len(self.changes)}   Líneas añadidas: +{total_add}   Líneas eliminadas: −{total_del}", color=C_MUTED, size=9, italic=True)

        COLS = [("Archivo", 4.8), ("Categoría", 3.0), ("Tipo de Cambio", 2.6), ("Impacto detectado", 4.6)]
        tbl = self.doc.add_table(rows=1, cols=len(COLS)); tbl.style = "Table Grid"
        _header_row(tbl, COLS)

        for fc in self.changes:
            tc, bg = fc.kind_colors
            category = FILE_CATEGORIES.get(fc.ext, Path(fc.filepath).suffix.upper() or "Archivo")
            _data_row(tbl, [
                (fc.filename, 4.8, C_MOD_TEXT, True, "EFF6FF"),
                (category, 3.0, C_BODY, False, C_ROW_ALT),
                (fc.kind_label, 2.6, tc, True, bg),
                (analyze_impact(fc), 4.6, C_BODY, False, C_WHITE),
            ])
        self.doc.add_paragraph()

    def _detail(self):
        _h1(self.doc, "2. Detalle de Cambios por Archivo")

        for i, fc in enumerate(self.changes):
            _section_title(self.doc, fc.filename)
            p = self.doc.add_paragraph(); p.paragraph_format.space_after = Pt(4)
            _run(p, fc.filepath, color=C_MUTED, size=8, italic=True)

            if fc.contexts:
                p_ctx = self.doc.add_paragraph()
                _run(p_ctx, "Bloques / Funciones afectadas: ", bold=True, color=C_SUBTITLE, size=9)
                _run(p_ctx, ", ".join(fc.contexts), color=C_BODY, size=9, italic=True)

            p2 = self.doc.add_paragraph(); p2.paragraph_format.space_after = Pt(6)
            _run(p2, f"+{len(fc.added)} líneas añadidas   −{len(fc.removed)} líneas eliminadas", color=C_MUTED, size=8)

            # LÓGICA DE RESUMEN INTELIGENTE
            if fc.is_binary:
                _bullet(self.doc, "El archivo es binario. No se muestra contenido de texto.", "ℹ", C_MUTED)
            elif (fc.kind == 'added' and len(fc.added) > 30) or len(fc.added) + len(fc.removed) > 100:
                _run(self.doc.add_paragraph(), "Resumen Estructural (Archivo extenso):", bold=True, color=C_TITLE, size=9)
                struct = fc.extract_structure()
                
                if struct["imports"]:
                    _bullet(self.doc, "Dependencias / Imports detectados:", "📦", C_SUBTITLE)
                    for imp in struct["imports"]: _bullet(self.doc, imp, "·", C_BODY, indent=1.5)
                
                if struct["entities"]:
                    _bullet(self.doc, "Estructuras Lógicas (Clases/Funciones):", "⚙", C_SUBTITLE)
                    for ent in struct["entities"]: _bullet(self.doc, ent, "·", C_BODY, indent=1.5)
                    
                if not struct["imports"] and not struct["entities"]:
                    _bullet(self.doc, "Se añadieron datos planos o contenido estructurado (ej. JSON/HTML amplio).", "📄", C_BODY)
            else:
                if fc.added:
                    _run(self.doc.add_paragraph(), "Líneas añadidas:", bold=True, color=C_ADD_TEXT, size=9)
                    for line in fc.added: _bullet(self.doc, line, "✔", C_ADD_TEXT)

                if fc.removed:
                    p3 = self.doc.add_paragraph(); p3.paragraph_format.space_before = Pt(4)
                    _run(p3, "Líneas eliminadas:", bold=True, color=C_DEL_TEXT, size=9)
                    for line in fc.removed: _bullet(self.doc, line, "✖", C_DEL_TEXT)

            if not fc.added and not fc.removed and not fc.is_binary:
                p4 = self.doc.add_paragraph()
                _run(p4, "Archivo sin modificaciones en el código fuente. Posible cambio de propiedades (ej. chmod), creación de archivo vacío o renombramiento.", color=C_MUTED, size=9, italic=True)

            if i < len(self.changes) - 1:
                _divider(self.doc)

        self.doc.add_paragraph()

    def _recommendations(self):
        _h1(self.doc, "3. Recomendaciones antes del Merge")
        recs = analyze_recommendations(self.changes)
        p = self.doc.add_paragraph(); p.paragraph_format.space_after = Pt(7)
        _run(p, "Recomendaciones generadas lógicamente a partir del código modificado:", color=C_BODY, size=10)
        for rec in recs: _bullet(self.doc, rec, "➤", C_MOD_TEXT)
        self.doc.add_paragraph()

    def _footer(self):
        _divider(self.doc)
        p = self.doc.add_paragraph(); p.alignment, p.paragraph_format.space_before = WD_ALIGN_PARAGRAPH.CENTER, Pt(6)
        total_add = sum(len(f.added) for f in self.changes)
        total_del = sum(len(f.removed) for f in self.changes)
        _run(p, f"Archivos: {len(self.changes)}  ·  +{total_add} líneas  ·  −{total_del} líneas  ·  Informe Generado automaticamente con Python - Ryu Gabo -", color=C_MUTED, size=8, italic=True)

    def generate(self) -> str:
        self._title(); self._summary(); self._detail(); self._recommendations(); self._footer()
        self.doc.save(self.output)
        return self.output

# ─────────────────────────────────────────────────────────────────────────────
# CLI Y EJECUCIÓN
# ─────────────────────────────────────────────────────────────────────────────
def find_input(arg: Optional[str]) -> Path:
    if arg:
        p = Path(arg)
        if not p.exists():
            print(f"[ERROR] No se encontró: {p}", file=sys.stderr)
            sys.exit(1)
        return p

    for candidate in (Path.cwd() / DEFAULT_INPUT, Path(__file__).parent / DEFAULT_INPUT):
        if candidate.exists(): return candidate

    print(f"[ERROR] No se encontró '{DEFAULT_INPUT}'.\nGenera el archivo con:\n  git --no-pager diff --staged > {DEFAULT_INPUT}", file=sys.stderr)
    sys.exit(1)

def main():
    ap = argparse.ArgumentParser(description="Convierte 'informe.txt' en un informe .docx profesional analizando la lógica.")
    ap.add_argument("--input", "-i", default=None, help=f"Archivo diff (default: {DEFAULT_INPUT})")
    ap.add_argument("--output", "-o", default=None, help=f"Archivo salida (default: {DEFAULT_OUTPUT})")
    ap.add_argument("--branch-from", "-bf", default="feature/cambios", help="Rama origen")
    ap.add_argument("--branch-to", "-bt", default="develop", help="Rama destino")
    args = ap.parse_args()

    input_path = find_input(args.input)
    print(f"[INFO] Leyendo: {input_path}")

    text = clean(input_path.read_text(encoding="utf-8", errors="replace"))
    if not text.strip():
        print("[ERROR] El archivo está vacío.", file=sys.stderr)
        sys.exit(1)

    changes = DiffParser().parse(text)
    if not changes:
        print("[AVISO] No se encontraron cambios legibles.", file=sys.stderr)
        sys.exit(1)

    output = args.output or str(input_path.parent / DEFAULT_OUTPUT)
    result = ReportGenerator(changes=changes, branch_from=args.branch_from, branch_to=args.branch_to, output=output).generate()
    print(f"\n[OK] Informe generado exitosamente: {result}")

if __name__ == "__main__":
    main()
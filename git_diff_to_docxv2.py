#!/usr/bin/env python3
"""
git_diff_to_docx.py
────────────────────
Lee 'informe.txt' (en la misma carpeta) generado con:
    git --no-pager diff --staged -U9999 > informe.txt

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

# =============================================================================
# PALETA DE COLORES
# =============================================================================
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

# Colores para niveles de impacto
C_IMPACT_NULA     = RGBColor(0x6B, 0x72, 0x80)
C_IMPACT_LEVE     = RGBColor(0x05, 0x96, 0x69)
C_IMPACT_BAJA     = RGBColor(0x0D, 0x94, 0x88)
C_IMPACT_MEDIA    = RGBColor(0xD9, 0x77, 0x06)
C_IMPACT_ALTA     = RGBColor(0xDC, 0x25, 0x26)
C_IMPACT_CRITICA  = RGBColor(0x7F, 0x1D, 0x1D)

BG_IMPACT_NULA    = "F3F4F6"
BG_IMPACT_LEVE    = "ECFDF5"
BG_IMPACT_BAJA    = "F0FDFA"
BG_IMPACT_MEDIA   = "FFFBEB"
BG_IMPACT_ALTA    = "FFF1F2"
BG_IMPACT_CRITICA = "FEF2F2"

DEFAULT_INPUT  = "informe.txt"
DEFAULT_OUTPUT = "informe_cambios.docx"

ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

MESES_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
    5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"
}

def fecha_espanol() -> str:
    hoy = datetime.now()
    return f"{hoy.day} de {MESES_ES[hoy.month]} de {hoy.year}"

def clean(text: str) -> str:
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = ANSI_RE.sub('', text)
    return text.replace('\x00', '')

# =============================================================================
# NIVELES DE IMPACTO
# =============================================================================
def impact_colors(level: str) -> Tuple[RGBColor, str]:
    return {
        "Nula":    (C_IMPACT_NULA,    BG_IMPACT_NULA),
        "Leve":    (C_IMPACT_LEVE,    BG_IMPACT_LEVE),
        "Baja":    (C_IMPACT_BAJA,    BG_IMPACT_BAJA),
        "Media":   (C_IMPACT_MEDIA,   BG_IMPACT_MEDIA),
        "Alta":    (C_IMPACT_ALTA,    BG_IMPACT_ALTA),
        "Critica": (C_IMPACT_CRITICA, BG_IMPACT_CRITICA),
    }.get(level, (C_BODY, C_WHITE))

# =============================================================================
# MODELO: FileChange
# =============================================================================
@dataclass
class FileChange:
    filepath: str
    added:    List[str] = field(default_factory=list)
    removed:  List[str] = field(default_factory=list)
    full_content: List[str] = field(default_factory=list)
    contexts: Set[str]  = field(default_factory=set)
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
                       '.directive.ts', '.guard.ts', '.interceptor.ts',
                       '.reducer.ts', '.action.ts', '.effect.ts', '.selector.ts',
                       '.resolver.ts', '.model.ts', '.interface.ts', '.enum.ts',
                       '.helper.ts', '.util.ts', '.config.ts', '.constant.ts'):
            if name.endswith(double):
                return double
        return Path(self.filepath).suffix.lower()

    @property
    def is_lockfile(self) -> bool:
        """Detecta archivos de lock/dependencias que no aportan valor en detalle linea a linea."""
        return self.filename.lower() in (
            'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
            'composer.lock', 'gemfile.lock', 'poetry.lock', 'cargo.lock',
            'packages.lock.json', 'shrinkwrap.json'
        )

    @property
    def is_environment_file(self) -> bool:
        """Detecta archivos de entorno/configuracion de ambiente."""
        name = self.filename.lower()
        return (name.startswith('environment') and name.endswith('.ts')) or \
               name in ('.env', '.env.local', '.env.production', '.env.staging', '.env.qa')

    @property
    def needs_structural_summary(self) -> bool:
        """
        FIX BUG 2: Umbral inteligente por tipo de archivo.
        Determina si el archivo debe mostrarse como resumen estructural
        en vez de linea por linea.
        """
        total = len(self.added) + len(self.removed)

        # Lock files: siempre resumen (nunca linea a linea)
        if self.is_lockfile:
            return True

        # Archivos grandes (mas de 60 lineas totales)
        if total > 60:
            return True

        # Archivos nuevos con mas de 15 lineas: resumen estructural
        if self.kind == 'added' and len(self.added) > 15:
            return True

        # Archivos con interfaces/tipos exportados (aunque sean pequenos)
        all_text = "\n".join(self.added)
        if re.search(r'export\s+(interface|type|enum|class)\b', all_text) and len(self.added) > 10:
            return True

        return False

    @property
    def kind_label(self) -> str:
        if self.kind == 'added':   return 'Adicion'
        if self.kind == 'deleted': return 'Eliminacion'
        if self.kind == 'renamed': return 'Renombrado'
        if self.is_binary:         return 'Binario/Media'
        n_add, n_del = len(self.added), len(self.removed)
        if n_del == 0 and n_add > 0: return 'Adicion'
        if n_add == 0 and n_del > 0: return 'Eliminacion'
        if n_del > n_add * 2 and n_del > 10: return 'Refactor'
        if n_add == 0 and n_del == 0: return 'Configuracion'
        return 'Modificacion'

    @property
    def kind_colors(self) -> Tuple[RGBColor, str]:
        lbl = self.kind_label
        if lbl == 'Adicion':     return C_ADD_TEXT, C_ADD_BG
        if lbl == 'Eliminacion': return C_DEL_TEXT, C_DEL_BG
        if lbl == 'Refactor':    return C_REF_TEXT, C_REF_BG
        return C_MOD_TEXT, C_MOD_BG

    def extract_structure(self) -> Dict[str, List[str]]:
        """Analiza logicamente el codigo para extraer imports, clases y funciones."""
        structure: Dict[str, List[str]] = {
            "imports": [], "entities": [], "decorators": [], "routes": []
        }
        all_lines = self.added + self.removed

        import_pattern    = re.compile(r'^\s*(import|from|require\(|include|using)\b')
        entity_pattern    = re.compile(
            r'^\s*(export )?(class|def|function|interface|const \w+\s*=\s*\(|'
            r'let \w+\s*=\s*\(|async function|type\s+\w+\s*=|enum\s+\w+)'
        )
        decorator_pattern = re.compile(r'^\s*@\w+')
        route_pattern     = re.compile(
            r"(path\s*:\s*['\"]|route\s*\(|router\.(get|post|put|delete|patch)\s*\()", re.I
        )

        for line in all_lines:
            if import_pattern.search(line) and line not in structure["imports"]:
                structure["imports"].append(
                    line.strip()[:90] + ('...' if len(line) > 90 else '')
                )
            elif decorator_pattern.match(line) and line.strip() not in structure["decorators"]:
                structure["decorators"].append(line.strip()[:60])
            elif route_pattern.search(line) and line.strip() not in structure["routes"]:
                structure["routes"].append(line.strip()[:80])
            elif entity_pattern.search(line):
                clean_entity = line.strip().split('{')[0].strip()
                if clean_entity not in structure["entities"]:
                    structure["entities"].append(clean_entity)
        return structure

    def _extract_deleted_identifiers(self, line: str) -> List[str]:
        line = line.strip()
        m_ts_braces = re.search(r'import\s+\{([^}]+)\}', line)
        if m_ts_braces:
            return [x.strip() for x in m_ts_braces.group(1).split(',')]
        m_ts_simple = re.search(r'import\s+([a-zA-Z0-9_]+)\s+from', line)
        if m_ts_simple:
            return [m_ts_simple.group(1)]
        m_py_from = re.search(r'from\s+\S+\s+import\s+(.+)', line)
        if m_py_from:
            return [x.strip() for x in m_py_from.group(1).split(',')]
        m_var = re.search(
            r'(?:(?:private|public|protected|readonly|const|let|var|static)\s+)+([a-zA-Z0-9_]+)',
            line
        )
        if m_var:
            return [m_var.group(1)]
        m_simple_var = re.match(r'^([a-zA-Z0-9_]+)\s*[=:]', line)
        if m_simple_var:
            return [m_simple_var.group(1)]
        return []

    def verify_dead_code(self, deleted_line: str) -> bool:
        if not self.full_content:
            return False
        identifiers = [i for i in self._extract_deleted_identifiers(deleted_line) if i]
        if not identifiers:
            return False
        for ident in identifiers:
            pattern = re.compile(rf'\b{re.escape(ident)}\b')
            if not any(pattern.search(line) for line in self.full_content):
                return True
        return False

    def detect_linter_fix(self, removed_line: str) -> str:
        """
        Detecta si la eliminacion de una linea fue por correccion de linter/formatter.
        FIX BUG 3: Corregido falso positivo en regla eqeqeq cuando r_line == a_line
                   y la linea no contiene operadores de comparacion.
        """
        if not self.added:
            return ""
        r_line = removed_line.strip()
        if not r_line:
            return ""

        for a_line in self.added:
            a_line = a_line.strip()
            if not a_line:
                continue

            # (regex negativo lookahead/lookbehind para evitar === y !==)
            has_equality_op = bool(re.search(r'(?<![=!<>])={2}(?!=)|(?<!!)!={1}(?!=)', r_line))
            if has_equality_op:
                r_eq = r_line.replace('!==', '\x00').replace('===', '\x01')
                r_eq = r_eq.replace('!=', '!==').replace('==', '===')
                r_eq = r_eq.replace('\x00', '!==').replace('\x01', '===')
                # Solo reportar si realmente cambio algo
                if r_eq == a_line and r_eq != r_line:
                    return "ESLint: Igualdad estricta (=== / !==)"

            # 2. ESLint prefer-const / no-var
            if 'let' in r_line and re.sub(r'\blet\b', 'const', r_line) == a_line:
                return "ESLint: Usar const en lugar de let"
            if 'var' in r_line and re.sub(r'\bvar\b', 'let', r_line) == a_line:
                return "ESLint: Usar let en lugar de var"
            if 'var' in r_line and re.sub(r'\bvar\b', 'const', r_line) == a_line:
                return "ESLint: Usar const en lugar de var"

            # 3. Prettier quotes
            if "'" in r_line and r_line.replace("'", '"') == a_line:
                return "Prettier: Cambio a comillas dobles"
            if '"' in r_line and r_line.replace('"', "'") == a_line:
                return "Prettier: Cambio a comillas simples"

            # 4. Prettier semi
            if r_line + ';' == a_line:
                return "Prettier: Agregar punto y coma"
            if r_line == a_line + ';':
                return "Prettier: Eliminar punto y coma"

            # 5. Prettier: espaciado / indentacion (solo si las lineas son distintas)
            r_ns = re.sub(r'\s+', '', r_line)
            a_ns = re.sub(r'\s+', '', a_line)
            if r_ns == a_ns and r_line != a_line and len(r_ns) > 2:
                return "Prettier: Arreglo de espaciado/indentacion"

            # 6. ESLint trailing comma
            if (r_line.rstrip(',') == a_line.rstrip(',') and r_line != a_line
                    and r_line.endswith(',') != a_line.endswith(',') and len(r_line) > 2):
                return "ESLint: Trailing comma"

            # 7. TSLint: tipo any -> unknown
            if 'any' in r_line and re.sub(r'\bany\b', 'unknown', r_line) == a_line:
                return "TSLint: Reemplazar 'any' por 'unknown'"

            # 8. ESLint no-console
            if re.match(r'^console\.(log|warn|error|info|debug)\s*\(', r_line):
                return "ESLint: Eliminar console.log/warn/error"

            # 9. Prettier: template literals
            if '`' in r_line and r_line.replace('`', '"') == a_line:
                return "Prettier: Template literal a comillas dobles"
            if '"' in r_line and r_line.replace('"', '`') == a_line:
                return "Prettier: Comillas a template literal"

            # 10. ESLint prefer-arrow-callback
            m_func  = re.match(r'function\s*\(([^)]*)\)\s*\{(.+)\}', r_line)
            m_arrow = re.match(r'\(([^)]*)\)\s*=>\s*\{?(.+)\}?', a_line)
            if m_func and m_arrow and m_func.group(1).strip() == m_arrow.group(1).strip():
                return "ESLint: Convertir a arrow function"

            # 11. TSLint: eliminar modificador 'public' implicito
            if 'public' in r_line and re.sub(r'\bpublic\b\s*', '', r_line).strip() == a_line.strip():
                return "TSLint: Eliminar modificador 'public' implicito"

            # 12. ESLint: object shorthand { x: x } -> { x }
            m_long  = re.search(r'\{\s*(\w+)\s*:\s*\1\s*\}', r_line)
            m_short = re.search(r'\{\s*(\w+)\s*\}', a_line)
            if m_long and m_short and m_long.group(1) == m_short.group(1):
                return "ESLint: Object shorthand"

            # 13. Prettier: parentesis innecesarios en arrow de un parametro
            if '=>' in r_line and re.sub(r'\((\w+)\)\s*=>', r'\1 =>', r_line) == a_line:
                return "Prettier: Eliminar parentesis en arrow function de un parametro"

        return ""

    def classify_removed_line(self, line: str) -> str:
        """Clasifica la razon de eliminacion con mayor precision."""
        linter = self.detect_linter_fix(line)
        if linter:
            return f"[Linter] {linter}"
        if self.verify_dead_code(line):
            return "[Limpieza] Codigo sin uso detectado"

        stripped = line.strip()
        if stripped.startswith('//') or stripped.startswith('#') \
                or stripped.startswith('*') or stripped.startswith('/*'):
            return "[Doc] Comentario o documentacion eliminada"
        if re.match(r'(console\.(log|warn|error|debug|info)|print\(|logger\.|Log\.)', stripped):
            return "[Debug] Traza o log de depuracion eliminada"
        if re.search(r'\b(TODO|FIXME|HACK|XXX|TEMP)\b', stripped, re.I):
            return "[Deuda tecnica] Comentario TODO/FIXME eliminado"
        if re.search(r'\b(isDevMode|environment\.|process\.env|DEBUG|FEATURE_FLAG)\b', stripped, re.I):
            return "[Config] Flag de entorno o feature flag"
        if stripped.startswith('//') and re.search(r'[({;=]', stripped):
            return "[Refactor] Codigo comentado eliminado"
        if re.search(r'\b(mock|stub|fake|dummy|hardcoded|temp|test_data)\b', stripped, re.I):
            return "[Test] Dato de prueba o mock eliminado"
        return ""


# =============================================================================
# PARSER DE DIFF
# =============================================================================
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
            if cur is None:
                continue
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
                hint = m_hunk.group(1).strip()
                if hint and len(hint) > 2:
                    cur.contexts.add(hint[:60])
            elif self._PLUS3.match(line) or self._MIN3.match(line):
                continue
            elif line.startswith('+') and not line.startswith('+++'):
                s = line[1:].strip()
                if s:
                    cur.added.append(s)
                    cur.full_content.append(s)
            elif line.startswith('-') and not line.startswith('---'):
                s = line[1:].strip()
                if s:
                    cur.removed.append(s)
            elif line.startswith(' '):
                s = line[1:].strip()
                if s:
                    cur.full_content.append(s)

        return [f for f in files if f.filepath]


# =============================================================================
# CATEGORIAS DE ARCHIVOS
# =============================================================================
FILE_CATEGORIES: Dict[str, str] = {
    '.component.html':  'Template Angular',
    '.component.ts':    'Componente Angular',
    '.component.scss':  'Estilos Componente',
    '.service.ts':      'Servicio Angular',
    '.spec.ts':         'Test Unitario',
    '.module.ts':       'Modulo Angular',
    '.pipe.ts':         'Pipe Angular',
    '.directive.ts':    'Directiva Angular',
    '.guard.ts':        'Guard de Ruta',
    '.interceptor.ts':  'Interceptor HTTP',
    '.reducer.ts':      'Reducer NgRx',
    '.action.ts':       'Accion NgRx',
    '.effect.ts':       'Efecto NgRx',
    '.selector.ts':     'Selector NgRx',
    '.resolver.ts':     'Resolver Angular',
    '.model.ts':        'Modelo de Datos',
    '.interface.ts':    'Interfaz TypeScript',
    '.enum.ts':         'Enumeracion',
    '.helper.ts':       'Helper/Utilidad',
    '.util.ts':         'Utilidad',
    '.config.ts':       'Configuracion',
    '.constant.ts':     'Constantes',
    '.html':            'Template HTML',
    '.scss':            'Estilos SCSS',
    '.css':             'Estilos CSS',
    '.ts':              'TypeScript',
    '.js':              'JavaScript',
    '.py':              'Python',
    '.json':            'Configuracion JSON',
    '.md':              'Documentacion',
    '.sql':             'Base de Datos',
    '.yml':             'Config YAML',
    '.yaml':            'Config YAML',
    '.env':             'Variables de Entorno',
    '.sh':              'Script Shell',
    '.xml':             'XML / Config',
    '.graphql':         'Esquema GraphQL',
    '.prisma':          'Schema Prisma ORM',
}

def get_category(fc: FileChange) -> str:
    if fc.is_lockfile:
        return 'Dependencias (Lock)'
    if fc.is_environment_file:
        return 'Archivo de Entorno'
    return FILE_CATEGORIES.get(fc.ext, Path(fc.filepath).suffix.upper() or 'Archivo')


# =============================================================================
# SENALES DE IMPACTO TECNICO
# =============================================================================
IMPACT_SIGNALS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'\.subscribe\s*\('),
     'Flujo asincrono RxJS'),
    (re.compile(r'catchError|throwError|try\s*{|except\s+|\.catch\s*\('),
     'Gestion de errores y excepciones'),
    (re.compile(r'\bAuthService\b|\bAuthGuard\b|inject\s*\(.*Auth|new\s+Auth\w+'),
     'Seguridad: Servicio de autenticacion'),
    (re.compile(r'\bJWT\b|jsonwebtoken|\.sign\s*\(|\.verify\s*\(|bearerToken|refreshToken'),
     'Seguridad: Manejo de JWT/tokens'),
    (re.compile(r'(?<!\w)password(?!\w)|\bhash\b|\bbcrypt\b|\bsalt\b|\bcipher\b', re.I),
     'Seguridad: Credenciales o hashes'),
    (re.compile(r'apiLoginUrl|apiBackend|apiUrl\b|API_URL|baseUrl|apiSIAW|apiSIP', re.I),
     'Variable de entorno o endpoint de API'),
    (re.compile(r'router\.navigate|HttpResponse|location\.href|\[routerLink\]'),
     'Logica de ruteo o navegacion'),
    (re.compile(r'HttpClient|http\.(get|post|put|delete|patch)\s*[(<]', re.I),
     'Llamada HTTP a API externa'),
    (re.compile(r'console\.(log|warn|error)|print\(|logger\.'),
     'Trazabilidad (logs de depuracion)'),
    (re.compile(r'SELECT\b|INSERT\b|UPDATE\b|DELETE\b|JOIN\b|\.save\s*\(|\.find\s*\(', re.I),
     'Consulta u operacion de base de datos'),
    (re.compile(r'\bexport\s+(class|interface|type|enum)\b'),
     'Contrato exportado publicamente'),
    (re.compile(r'@Input\s*\(\)|@Output\s*\(\)|EventEmitter|@Prop\s*\(\)'),
     'Contrato de componente (inputs/outputs)'),
    (re.compile(r'@NgModule\s*\(|providers\s*:\s*\[|declarations\s*:\s*\['),
     'Configuracion de modulo Angular'),
    (re.compile(r'environment\.(prod|production|cloud|staging|qa)', re.I),
     'Configuracion de ambiente especifico'),
    (re.compile(r'dispatch\s*\(|store\.select|createAction|createReducer|createEffect'),
     'Estado global NgRx'),
    (re.compile(r'migration|schema|alembic|flyway|liquibase|ALTER TABLE|DROP TABLE', re.I),
     'Migracion de base de datos'),
    (re.compile(r'cron|@Scheduled|scheduler|setInterval|setTimeout'),
     'Tarea programada/scheduler'),
    (re.compile(r'WebSocket|socket\.io|ws://|wss://'),
     'Comunicacion en tiempo real (WebSocket)'),
    (re.compile(r'\bcache\b|redis|memcache|localStorage|sessionStorage', re.I),
     'Capa de cache o almacenamiento'),
    (re.compile(r'\bi18n\b|translate\.|locale|l10n', re.I),
     'Internacionalizacion (i18n)'),
    (re.compile(r'canActivate|canLoad|canMatch|menuGuard|authGuard'),
     'Guards de ruta (control de acceso)'),
]
# =============================================================================
# MOTOR DE ANALISIS SEMANTICO CONTEXTUAL
# =============================================================================

class SemanticInsightEngine:
    """
    Motor heurístico que intenta deducir la intención del cambio.
    No depende de ejemplos específicos.
    Detecta patrones arquitectónicos, de layout, estado y diseño.
    """

    def analyze(self, fc: FileChange) -> List[str]:
        insights = []

        added = "\n".join(fc.added)
        removed = "\n".join(fc.removed)
        full = added + "\n" + removed

        # ---------------------------------------------------------
        # 1. Migración de lógica TS hacia CSS
        # ---------------------------------------------------------
        if fc.ext in ('.component.ts', '.ts'):
            if re.search(r'\b(height|width)\s*:', removed) and \
               re.search(r'calc\(.*vh', added):
                insights.append(
                    "Se migra control de dimensiones desde lógica TypeScript "
                    "hacia cálculo dinámico en CSS, favoreciendo separación de responsabilidades."
                )

        # ---------------------------------------------------------
        # 2. Simplificación de binding Angular
        # ---------------------------------------------------------
        if fc.ext in ('.html', '.component.html'):
            if re.search(r'\[.*\]=', removed) and not re.search(r'\[.*\]=', added):
                insights.append(
                    "Se reduce uso de binding dinámico, simplificando el template "
                    "y disminuyendo dependencia del estado del componente."
                )

        # ---------------------------------------------------------
        # 3. Eliminación de propiedad posiblemente obsoleta
        # ---------------------------------------------------------
        if fc.ext in ('.ts', '.component.ts'):
            if re.search(r'\b(public|private|protected)?\s*\w+\s*:', removed):
                insights.append(
                    "Se elimina propiedad del componente, posible reducción "
                    "de estado interno o eliminación de lógica innecesaria."
                )

        # ---------------------------------------------------------
        # 4. Consolidación de estilos
        # ---------------------------------------------------------
        if fc.ext in ('.scss', '.css', '.component.scss'):
            if removed.count('}') > added.count('}'):
                insights.append(
                    "Se reduce cantidad de bloques CSS, indicando consolidación "
                    "o simplificación de reglas de estilo."
                )

        # ---------------------------------------------------------
        # 5. Eliminación de duplicidad estructural
        # ---------------------------------------------------------
        if len(set(fc.removed)) < len(fc.removed):
            insights.append(
                "Se eliminan líneas repetidas, posible corrección de duplicidad estructural."
            )

        # ---------------------------------------------------------
        # 6. Refactor hacia patrón más declarativo
        # ---------------------------------------------------------
        if re.search(r'if\s*\(', removed) and not re.search(r'if\s*\(', added):
            insights.append(
                "Se reduce lógica condicional explícita, posible migración "
                "hacia patrón más declarativo o basado en configuración."
            )

        # ---------------------------------------------------------
        # 7. Mejora responsive
        # ---------------------------------------------------------
        if re.search(r'vh|vw|%', added) and not re.search(r'vh|vw|%', removed):
            insights.append(
                "Se introducen unidades relativas (vh/vw/%), indicando mejora en comportamiento responsive."
            )

        # ---------------------------------------------------------
        # 8. Reducción de acoplamiento
        # ---------------------------------------------------------
        if re.search(r'import ', removed) and not re.search(r'import ', added):
            insights.append(
                "Se eliminan dependencias importadas, posible reducción de acoplamiento."
            )

        # ---------------------------------------------------------
        # 9. Optimización de performance
        # ---------------------------------------------------------
        if re.search(r'\.subscribe\(', removed) and re.search(r'async|await|pipe', added):
            insights.append(
                "Se modifica patrón asincrónico, posible mejora en manejo de suscripciones o performance."
            )

        # ---------------------------------------------------------
        # 10. Eliminación de estado redundante
        # ---------------------------------------------------------
        if re.search(r'\bthis\.', removed) and not re.search(r'\bthis\.', added):
            insights.append(
                "Se reduce uso de propiedades del componente, indicando simplificación del estado."
            )

        return insights
def analyze_technical_impact(fc: FileChange) -> str:
    """
    FIX BUG 5: Archivos de entorno analizan full_content para detectar
    patrones criticos que estan en lineas de contexto (sin + ni -).
    """
    if fc.is_binary:
        return "Actualizacion de archivo binario (imagen, fuente, recurso)"
    if fc.is_lockfile:
        n = len(fc.added)
        d = len(fc.removed)
        return f"Actualizacion de dependencias: +{n} entradas nuevas, -{d} eliminadas"

    if fc.is_environment_file:
        search_text = "\n".join(fc.added + fc.removed + fc.full_content[:40])
    else:
        search_text = "\n".join(fc.added + fc.removed)

    found = []
    for pattern, description in IMPACT_SIGNALS:
        if pattern.search(search_text):
            found.append(description)

    if found:
        return " | ".join(found[:4])

    category = get_category(fc)
    if len(fc.added) == 0 and len(fc.removed) == 0:
        return f"Ajuste de propiedades o permisos en {category}"
    if fc.kind == 'added':
        return f"Implementacion inicial de {category}"
    if fc.kind == 'deleted':
        return f"Eliminacion completa de {category}"
    return f"Modificacion de logica interna en {category}"


def calculate_deploy_impact(fc: FileChange) -> str:
    """
    FIX BUG 4: Regex de AuthService mas preciso (sin falsos positivos en rutas).
    FIX BUG 5: Archivos de entorno analizan full_content para score correcto.
    """
    if fc.is_binary:
        return "Leve"
    if fc.is_lockfile:
        return "Leve"

    if fc.is_environment_file:
        all_lines   = "\n".join(fc.added + fc.removed + fc.full_content[:60])
    else:
        all_lines   = "\n".join(fc.added + fc.removed)

    n_add = len(fc.added)
    n_del = len(fc.removed)
    score = 0

    # --- CRITICO ---
    if re.search(r'\bAuthService\b|\bAuthGuard\b|jsonwebtoken|\.verify\s*\(.*token', all_lines):
        score += 60
    if re.search(r'(?<!\w)password(?!\w)|\bbcrypt\b|\bsalt\b', all_lines, re.I):
        score += 50
    if re.search(r'migration|alembic|flyway|DROP TABLE|ALTER TABLE', all_lines, re.I):
        score += 70

    # --- ALTO: entornos con URLs reales ---
    if fc.is_environment_file and re.search(r'apiLoginUrl|apiBackend|apiUrl\b', all_lines, re.I):
        score += 45

    # --- ALTO: contratos publicos ---
    if re.search(r'\bexport\s+(interface|type|class|enum)\b', all_lines):
        score += 40
    if re.search(r'@Input\s*\(\)|@Output\s*\(\)|EventEmitter', all_lines):
        score += 35
    if re.search(r'@NgModule\s*\(|providers\s*:\s*\[|declarations\s*:\s*\[', all_lines):
        score += 35
    if re.search(r'dispatch\s*\(|createAction|createReducer|createEffect', all_lines):
        score += 30

    # --- MEDIO ---
    if re.search(r'HttpClient|http\.(get|post|put|delete|patch)', all_lines, re.I):
        score += 30
    if re.search(r'catchError|throwError|try\s*{|except\s+', all_lines):
        score += 20
    if re.search(r'SELECT\b|INSERT\b|UPDATE\b|DELETE\b|\.save\s*\(', all_lines, re.I):
        score += 25
    if re.search(r'router\.navigate|canActivate|canLoad|authGuard|menuGuard', all_lines):
        score += 20
    if re.search(r'cron|scheduler|setTimeout|setInterval', all_lines):
        score += 20
    if re.search(r'WebSocket|socket\.io', all_lines, re.I):
        score += 25

    # --- BAJO: UI y estilos ---
    if fc.ext in ('.scss', '.css', '.component.scss'):
        score += 5
    if fc.ext in ('.html', '.component.html'):
        score += 10

    # --- LEVE: tests, docs ---
    if fc.ext == '.spec.ts':
        score = min(score + 3, 15)
    if fc.ext == '.md':
        return "Nula"

    # Bonus por magnitud
    total_lines = n_add + n_del
    if total_lines > 200:   score += 20
    elif total_lines > 100: score += 12
    elif total_lines > 50:  score += 6
    elif total_lines > 20:  score += 3

    # Archivo nuevo: cap de impacto
    if fc.kind == 'added' and score < 40:
        score = min(score, 30)
    # Archivo eliminado: riesgo de dependencias rotas
    if fc.kind == 'deleted':
        score += 20

    # Solo linter fixes
    if fc.removed and not fc.added:
        if all(fc.detect_linter_fix(l) for l in fc.removed if l.strip()):
            return "Nula"

    if score == 0:        return "Nula"
    elif score <= 10:     return "Leve"
    elif score <= 28:     return "Baja"
    elif score <= 50:     return "Media"
    elif score <= 75:     return "Alta"
    else:                 return "Critica"


# =============================================================================
# RESUMEN DE LOCK FILE
# =============================================================================
def summarize_lockfile(fc: FileChange) -> Dict[str, List[str]]:
    """Extrae nombres de paquetes nuevos/eliminados de un lock file."""
    added_pkgs:   List[str] = []
    removed_pkgs: List[str] = []
    pkg_pattern = re.compile(r'"(@?[\w/@.-]+)":\s*\{|^(@?[\w/@.-]+)@[\d.^~]')

    for line in fc.added:
        m = pkg_pattern.search(line.strip())
        if m:
            name = (m.group(1) or m.group(2) or '').strip('"')
            if name and name not in added_pkgs:
                added_pkgs.append(name)

    for line in fc.removed:
        m = pkg_pattern.search(line.strip())
        if m:
            name = (m.group(1) or m.group(2) or '').strip('"')
            if name and name not in removed_pkgs:
                removed_pkgs.append(name)

    return {"added": added_pkgs[:20], "removed": removed_pkgs[:20]}


# =============================================================================
# RECOMENDACIONES
# =============================================================================
def analyze_recommendations(changes: List[FileChange]) -> List[str]:
    recs = []
    all_removed = "\n".join(l for f in changes for l in f.removed).lower()

    if 'console.' in all_removed or 'print(' in all_removed:
        recs.append(
            "Se limpiaron logs de depuracion. Verificar que no falten "
            "trazas criticas en produccion."
        )

    env_changes = [f for f in changes if f.is_environment_file]
    if env_changes:
        ambientes = [f.filename for f in env_changes]
        recs.append(
            f"Se modificaron {len(env_changes)} archivo(s) de entorno "
            f"({', '.join(ambientes)}). Confirmar que los endpoints sean "
            f"correctos en cada ambiente antes del despliegue."
        )

    if any(f.is_lockfile for f in changes):
        recs.append(
            "Se actualizo el archivo de dependencias. Ejecutar 'npm install' "
            "en el servidor de CI/CD para sincronizar node_modules."
        )

    if any(f.kind == 'deleted' for f in changes):
        recs.append(
            "Se eliminaron archivos. Ejecutar build completo y verificar "
            "que no queden imports huerfanos."
        )

    if any(f.ext in ('.html', '.scss', '.css', '.component.html', '.component.scss')
           for f in changes):
        recs.append(
            "Cambios en interfaz de usuario. Realizar QA visual en "
            "distintas resoluciones (movil y escritorio)."
        )

    all_code = "\n".join(l for f in changes for l in f.added + f.removed)
    if re.search(r'\bexport\s+(interface|type)\b', all_code):
        recs.append(
            "Se modificaron interfaces o tipos exportados. Verificar que "
            "todos los consumidores del tipo esten actualizados y que el "
            "build compile sin errores de tipo."
        )

    if any(f.ext == '.spec.ts' for f in changes):
        recs.append(
            "Se modificaron tests unitarios. Ejecutar suite completa de "
            "pruebas antes del merge."
        )

    if re.search(r'migration|ALTER TABLE|DROP', all_code, re.I):
        recs.append(
            "ALERTA: Detectadas posibles migraciones de base de datos. "
            "Revisar con DBA antes del despliegue."
        )

    if re.search(r'@NgModule|providers\s*:|declarations\s*:', all_code):
        recs.append(
            "Se modifico un modulo Angular. Verificar que las declaraciones "
            "y providers sean correctas y que no haya duplicados."
        )

    if any(calculate_deploy_impact(f) in ("Alta", "Critica") for f in changes):
        recs.append(
            "Existen cambios de impacto ALTO o CRITICO. Se recomienda "
            "revision por pares (code review) antes del merge a develop."
        )

    if not recs:
        recs.append(
            "No se detectaron riesgos criticos. Revisar cobertura de pruebas "
            "unitarias para las nuevas estructuras anadidas."
        )

    return list(dict.fromkeys(recs))


# =============================================================================
# HELPERS DOCX
# =============================================================================
def _set_bg(cell, hex_color: str):
    tcPr = cell._tc.get_or_add_tcPr()
    shd  = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)

def _set_borders(cell, color: str = "CCCCCC"):
    tcPr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for side in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color)
        borders.append(el)
    tcPr.append(borders)

def _set_width(cell, cm: float):
    tcPr = cell._tc.get_or_add_tcPr()
    tcW  = OxmlElement("w:tcW")
    tcW.set(qn("w:w"), str(int(cm * 567)))
    tcW.set(qn("w:type"), "dxa")
    tcPr.append(tcW)

def _run(para, text: str, bold=False, italic=False,
         color: RGBColor = None, size: float = 10, font: str = "Arial"):
    r = para.add_run(text)
    r.bold        = bold
    r.italic      = italic
    r.font.size   = Pt(size)
    r.font.name   = font
    if color:
        r.font.color.rgb = color
    return r

def _header_row(table, cols: List[Tuple[str, float]]):
    row = table.rows[0]
    for i, (text, w) in enumerate(cols):
        cell = row.cells[i]
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        _set_bg(cell, C_HDR_BG)
        _set_borders(cell, C_ACCENT)
        _set_width(cell, w)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _run(p, text, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF), size=11)

def _data_row(table, cells: List[Tuple[str, float, RGBColor, bool, str]]):
    row = table.add_row()
    for i, (text, w, tc, bold, bg) in enumerate(cells):
        cell = row.cells[i]
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        _set_bg(cell, bg)
        _set_borders(cell)
        _set_width(cell, w)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        for j, line in enumerate(text.split("\n")):
            if j > 0:
                p = cell.add_paragraph()
            _run(p, line, bold=bold, color=tc, size=10)

def _bullet(doc, text: str, symbol: str, color: RGBColor, indent: float = 1.0):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent       = Cm(indent)
    p.paragraph_format.first_line_indent = Cm(-0.5)
    p.paragraph_format.space_after       = Pt(2)
    _run(p, f"{symbol}  ", bold=True, color=color, size=9.5)
    _run(p, text, color=color, size=9.5)

def _section_title(doc, text: str):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after  = Pt(5)
    _run(p, text, bold=True, color=C_TITLE, size=12)
    pPr    = p._p.get_or_add_pPr()
    pBdr   = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"),   "single")
    bottom.set(qn("w:sz"),    "4")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), C_ACCENT)
    pBdr.append(bottom)
    pPr.append(pBdr)

def _divider(doc):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after  = Pt(6)
    pPr  = p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bot  = OxmlElement("w:bottom")
    bot.set(qn("w:val"),   "single")
    bot.set(qn("w:sz"),    "4")
    bot.set(qn("w:space"), "1")
    bot.set(qn("w:color"), C_BORDER)
    pBdr.append(bot)
    pPr.append(pBdr)

def _h1(doc, text: str):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(14)
    p.paragraph_format.space_after  = Pt(7)
    _run(p, text, bold=True, color=C_TITLE, size=14)


# =============================================================================
# GENERADOR DOCX
# =============================================================================
class ReportGenerator:
    def __init__(self, changes: List[FileChange], branch_from: str, branch_to: str, output: str):
        self.changes     = changes
        self.branch_from = branch_from
        self.branch_to   = branch_to
        self.output      = output
        self.doc         = Document()
        self._upgrade_compatibility()
        self._page_setup()

    def _upgrade_compatibility(self):
        settings = self.doc.settings.element
        compat   = OxmlElement('w:compat')
        cs       = OxmlElement('w:compatSetting')
        cs.set(qn('w:name'), 'compatibilityMode')
        cs.set(qn('w:uri'),  'http://schemas.microsoft.com/office/word')
        cs.set(qn('w:val'),  '15')
        compat.append(cs)
        settings.append(compat)

    def _page_setup(self):
        sec = self.doc.sections[0]
        sec.page_width    = Inches(8.5)
        sec.page_height   = Inches(11)
        sec.left_margin   = Cm(2.0)
        sec.right_margin  = Cm(2.0)
        sec.top_margin    = Cm(2.0)
        sec.bottom_margin = Cm(2.0)
        style = self.doc.styles["Normal"]
        style.font.name = "Arial"
        style.font.size = Pt(10)

    def _title(self):
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(3)
        _run(p, "INFORME DE CAMBIOS DE CODIGO", bold=True, color=C_TITLE, size=20)

        p2 = self.doc.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p2.paragraph_format.space_after = Pt(2)
        _run(p2, f"Rama: {self.branch_from}  hacia  {self.branch_to}", color=C_SUBTITLE, size=11)

        p3 = self.doc.add_paragraph()
        p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p3.paragraph_format.space_after = Pt(12)
        _run(p3, f"Fecha: {fecha_espanol()}", color=C_MUTED, size=10)

        p4 = self.doc.add_paragraph()
        p4.paragraph_format.space_after = Pt(12)
        pPr  = p4._p.get_or_add_pPr()
        pBdr = OxmlElement("w:pBdr")
        bot  = OxmlElement("w:bottom")
        bot.set(qn("w:val"),   "single")
        bot.set(qn("w:sz"),    "12")
        bot.set(qn("w:space"), "1")
        bot.set(qn("w:color"), C_ACCENT)
        pBdr.append(bot)
        pPr.append(pBdr)

    def _summary(self):
        _h1(self.doc, "1. Tabla Resumen de Cambios")
        total_add = sum(len(f.added) for f in self.changes)
        total_del = sum(len(f.removed) for f in self.changes)

        p = self.doc.add_paragraph()
        p.paragraph_format.space_after = Pt(6)
        _run(
            p,
            f"Archivos afectados: {len(self.changes)}   "
            f"Lineas anadidas: +{total_add}   "
            f"Lineas eliminadas: -{total_del}",
            color=C_MUTED, size=9, italic=True
        )

        COLS = [
            ("Archivo",           3.8),
            ("Categoria",         2.6),
            ("Tipo de Cambio",    2.2),
            ("Impacto Tecnico",   4.0),
            ("Impacto en Deploy", 1.6),
        ]
        tbl = self.doc.add_table(rows=1, cols=len(COLS))
        tbl.style = "Table Grid"
        tbl.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _header_row(tbl, COLS)

        for fc in self.changes:
            tc_kind, bg_kind = fc.kind_colors
            category         = get_category(fc)
            deploy_level     = calculate_deploy_impact(fc)
            tc_dep, bg_dep   = impact_colors(deploy_level)
            tech_impact      = analyze_technical_impact(fc)

            _data_row(tbl, [
                (fc.filename,   3.8, C_MOD_TEXT, True,  "EFF6FF"),
                (category,      2.6, C_BODY,     False, C_ROW_ALT),
                (fc.kind_label, 2.2, tc_kind,    True,  bg_kind),
                (tech_impact,   4.0, C_BODY,     False, C_WHITE),
                (deploy_level,  1.6, tc_dep,     True,  bg_dep),
            ])

        self.doc.add_paragraph()

    def _render_lockfile_detail(self, fc: FileChange):
        """Renderizado compacto para archivos de dependencias (lock files)."""
        summary = summarize_lockfile(fc)
        p_info  = self.doc.add_paragraph()
        _run(
            p_info,
            f"Actualizacion de dependencias del proyecto. "
            f"Cambios: +{len(fc.added)} / -{len(fc.removed)} entradas. "
            f"Archivo gestionado automaticamente por el package manager.",
            color=C_MUTED, size=9, italic=True
        )
        if summary["added"]:
            p_a = self.doc.add_paragraph()
            _run(p_a, "Paquetes con entradas nuevas:", bold=True, color=C_ADD_TEXT, size=9)
            for pkg in summary["added"]:
                _bullet(self.doc, pkg, "+", C_ADD_TEXT)
        if summary["removed"]:
            p_r = self.doc.add_paragraph()
            _run(p_r, "Paquetes con entradas eliminadas:", bold=True, color=C_DEL_TEXT, size=9)
            for pkg in summary["removed"]:
                _bullet(self.doc, pkg, "-", C_DEL_TEXT)
        if not summary["added"] and not summary["removed"]:
            _bullet(self.doc, "Actualizacion de metadatos internos (integridad, resoluciones).", "-", C_MUTED)

    def _render_structural_summary(self, fc: FileChange):
        """Renderizado de resumen estructural para archivos extensos o con interfaces."""
        p_lbl = self.doc.add_paragraph()
        _run(p_lbl, "Resumen Estructural del archivo:", bold=True, color=C_TITLE, size=9)
        struct = fc.extract_structure()

        if struct["decorators"]:
            p_d = self.doc.add_paragraph()
            _run(p_d, "Decoradores detectados:", bold=True, color=C_SUBTITLE, size=9)
            for d in struct["decorators"]:
                _bullet(self.doc, d, "-", C_REF_TEXT, indent=1.5)

        if struct["imports"]:
            p_i = self.doc.add_paragraph()
            _run(p_i, "Dependencias / Imports:", bold=True, color=C_SUBTITLE, size=9)
            for imp in struct["imports"]:
                _bullet(self.doc, imp, "-", C_BODY, indent=1.5)

        if struct["entities"]:
            p_e = self.doc.add_paragraph()
            _run(p_e, "Estructuras Logicas (Clases / Funciones / Interfaces / Tipos):",
                 bold=True, color=C_SUBTITLE, size=9)
            for ent in struct["entities"]:
                _bullet(self.doc, ent, "-", C_BODY, indent=1.5)

        if struct["routes"]:
            p_r = self.doc.add_paragraph()
            _run(p_r, "Rutas registradas:", bold=True, color=C_SUBTITLE, size=9)
            for rt in struct["routes"]:
                _bullet(self.doc, rt, "-", C_MOD_TEXT, indent=1.5)

        if not any(struct.values()):
            _bullet(
                self.doc,
                "Contenido estructurado sin entidades detectables (datos, configuracion, markup).",
                "-", C_BODY
            )

    def _render_line_detail(self, fc: FileChange):
        """Renderizado linea a linea para cambios acotados con clasificacion de razon."""
        if fc.added:
            p_add = self.doc.add_paragraph()
            _run(p_add, "Lineas anadidas:", bold=True, color=C_ADD_TEXT, size=9)
            for line in fc.added:
                _bullet(self.doc, line, "+", C_ADD_TEXT)

        if fc.removed:
            p_rem = self.doc.add_paragraph()
            p_rem.paragraph_format.space_before = Pt(4)
            _run(p_rem, "Lineas eliminadas:", bold=True, color=C_DEL_TEXT, size=9)

            TAG_COLORS = {
                "Linter":        C_ADD_TEXT,
                "Limpieza":      C_MUTED,
                "Debug":         C_MUTED,
                "Doc":           C_MUTED,
                "Deuda tecnica": C_REF_TEXT,
                "Config":        C_MOD_TEXT,
                "Refactor":      C_REF_TEXT,
                "Test":          C_SUBTITLE,
            }

            for line in fc.removed:
                reason = fc.classify_removed_line(line)
                if reason:
                    tag_match = re.match(r'\[([^\]]+)\]\s*(.*)', reason)
                    if tag_match:
                        tag_key  = tag_match.group(1)
                        tag_desc = tag_match.group(2)
                        tc_tag   = TAG_COLORS.get(tag_key, C_SUBTITLE)
                        p_line   = self.doc.add_paragraph()
                        p_line.paragraph_format.left_indent       = Cm(1.0)
                        p_line.paragraph_format.first_line_indent = Cm(-0.5)
                        p_line.paragraph_format.space_after       = Pt(2)
                        _run(p_line, "-  ", bold=True, color=C_DEL_TEXT, size=9)
                        _run(p_line, line, color=C_DEL_TEXT, size=9)
                        _run(p_line, f"  [{tag_key}: {tag_desc}]",
                             bold=True, color=tc_tag, size=8, italic=True)
                    else:
                        _bullet(self.doc, f"{line}  ({reason})", "-", C_DEL_TEXT)
                else:
                    _bullet(self.doc, line, "-", C_DEL_TEXT)

    def _detail(self):
        semantic_engine = SemanticInsightEngine()
        _h1(self.doc, "2. Detalle de Cambios por Archivo")

        for i, fc in enumerate(self.changes):
            _section_title(self.doc, fc.filename)

            p_path = self.doc.add_paragraph()
            p_path.paragraph_format.space_after = Pt(3)
            _run(p_path, fc.filepath, color=C_MUTED, size=8, italic=True)

            if fc.contexts:
                p_ctx = self.doc.add_paragraph()
                p_ctx.paragraph_format.space_after = Pt(3)
                _run(p_ctx, "Bloques / Funciones afectadas: ", bold=True, color=C_SUBTITLE, size=9)
                _run(p_ctx, ", ".join(fc.contexts), color=C_BODY, size=9, italic=True)

            deploy_level = calculate_deploy_impact(fc)
             # -----------------------------------------
            # ANALISIS SEMANTICO CONTEXTUAL
            # -----------------------------------------
            semantic_insights = semantic_engine.analyze(fc)
            if semantic_insights:
                p_sem = self.doc.add_paragraph()
                _run(p_sem, "Analisis Semantico Detectado:",
                     bold=True, color=C_TITLE, size=9)

                for insight in semantic_insights:
                    _bullet(self.doc, insight, ">", C_MOD_TEXT)
            tc_dep, _    = impact_colors(deploy_level)
            p_stats = self.doc.add_paragraph()
            p_stats.paragraph_format.space_after = Pt(4)
            _run(p_stats, f"+{len(fc.added)} anadidas   -{len(fc.removed)} eliminadas   ",
                 color=C_MUTED, size=8)
            _run(p_stats, f"Impacto en deploy: {deploy_level}", bold=True, color=tc_dep, size=8)

            # Elegir estrategia de renderizado
            if fc.is_binary:
                _bullet(self.doc, "Archivo binario. No se puede mostrar contenido de texto.",
                        "-", C_MUTED)
            elif fc.is_lockfile:
                self._render_lockfile_detail(fc)
            elif fc.needs_structural_summary:
                self._render_structural_summary(fc)
            else:
                self._render_line_detail(fc)

            if not fc.added and not fc.removed and not fc.is_binary:
                p_empty = self.doc.add_paragraph()
                _run(
                    p_empty,
                    "Sin modificaciones en el codigo fuente. Posible cambio de "
                    "permisos, archivo vacio o renombramiento.",
                    color=C_MUTED, size=9, italic=True
                )

            if i < len(self.changes) - 1:
                _divider(self.doc)

        self.doc.add_paragraph()

    def _impact_legend(self):
        _h1(self.doc, "3. Leyenda de Niveles de Impacto en Deploy")

        legend = [
            ("Nula",
             "Sin riesgo. Documentacion, solo linter/formatter o configuracion menor."),
            ("Leve",
             "Archivo de dependencias, nuevo archivo aislado o cambio estetico."),
            ("Baja",
             "Nuevo componente/servicio sin contratos externos. Logica interna acotada."),
            ("Media",
             "Cambio en logica de negocio, llamadas HTTP, rutas o estado compartido."),
            ("Alta",
             "Interfaces/tipos exportados, modulos Angular, guards o archivos de entorno."),
            ("Critica",
             "Autenticacion, JWT, passwords, migraciones de BD o contratos de API externa."),
        ]

        tbl = self.doc.add_table(rows=1, cols=2)
        tbl.style = "Table Grid"
        tbl.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _header_row(tbl, [("Nivel", 2.5), ("Descripcion del criterio", 11.7)])

        for level, desc in legend:
            tc_l, bg_l = impact_colors(level)
            _data_row(tbl, [
                (level, 2.5,  tc_l,   True,  bg_l),
                (desc,  11.7, C_BODY, False, C_WHITE),
            ])

        self.doc.add_paragraph()

    def _recommendations(self):
        _h1(self.doc, "4. Recomendaciones antes del Merge")
        recs = analyze_recommendations(self.changes)
        p = self.doc.add_paragraph()
        p.paragraph_format.space_after = Pt(7)
        _run(
            p,
            "Recomendaciones generadas a partir del analisis del codigo modificado:",
            color=C_BODY, size=10
        )
        for rec in recs:
            _bullet(self.doc, rec, "->", C_MOD_TEXT)
        self.doc.add_paragraph()

    def _footer(self):
        _divider(self.doc)
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(6)
        total_add = sum(len(f.added) for f in self.changes)
        total_del = sum(len(f.removed) for f in self.changes)
        _run(
            p,
            f"Archivos: {len(self.changes)}  |  "
            f"+{total_add} lineas  |  "
            f"-{total_del} lineas  |  "
            f"Archivo generado automaticamente - Ryu Gabo -",
            color=C_MUTED, size=9.5, italic=True
        )

    def generate(self) -> str:
        self._title()
        self._summary()
        self._detail()
        self._impact_legend()
        self._recommendations()
        self._footer()
        self.doc.save(self.output)
        return self.output


# =============================================================================
# CLI Y EJECUCION
# =============================================================================
def find_input(arg: Optional[str]) -> Path:
    if arg:
        p = Path(arg)
        if not p.exists():
            print(f"[ERROR] No se encontro: {p}", file=sys.stderr)
            sys.exit(1)
        return p
    for candidate in (Path.cwd() / DEFAULT_INPUT, Path(__file__).parent / DEFAULT_INPUT):
        if candidate.exists():
            return candidate
    print(
        f"[ERROR] No se encontro '{DEFAULT_INPUT}'.\n"
        f"Genera el archivo con:\n"
        f"  git --no-pager diff --staged -U9999 > {DEFAULT_INPUT}",
        file=sys.stderr
    )
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(
        description="Convierte 'informe.txt' (git diff) en un informe .docx profesional."
    )
    ap.add_argument("--input",       "-i",  default=None,       help=f"Archivo diff (default: {DEFAULT_INPUT})")
    ap.add_argument("--output",      "-o",  default=None,       help=f"Archivo salida (default: {DEFAULT_OUTPUT})")
    ap.add_argument("--branch-from", "-bf", default="gabotest", help="Rama origen  (default: gabotest)")
    ap.add_argument("--branch-to",   "-bt", default="develop",  help="Rama destino (default: develop)")
    args = ap.parse_args()

    input_path = find_input(args.input)
    print(f"[INFO] Leyendo: {input_path}")

    text = clean(input_path.read_text(encoding="utf-8", errors="replace"))
    if not text.strip():
        print("[ERROR] El archivo esta vacio.", file=sys.stderr)
        sys.exit(1)

    changes = DiffParser().parse(text)
    if not changes:
        print("[AVISO] No se encontraron cambios legibles.", file=sys.stderr)
        sys.exit(1)

    output = args.output or str(input_path.parent / DEFAULT_OUTPUT)
    result = ReportGenerator(
        changes=changes,
        branch_from=args.branch_from,
        branch_to=args.branch_to,
        output=output
    ).generate()

    print(f"\n[OK] Informe generado: {result}")


if __name__ == "__main__":
    main()
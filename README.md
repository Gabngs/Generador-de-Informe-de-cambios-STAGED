# Git Diff to DOCX Reporter 📄

Este script de Python permite transformar el resultado de un `git diff` en un informe profesional en formato Word (.docx). Es ideal para adjuntar evidencias de cambios de código en entregas de proyectos o revisiones de lógica.

## Características
- Analiza lógicamente adiciones, eliminaciones y refactorizaciones.
- Detecta automáticamente el impacto (APIs, Base de Datos, Seguridad, etc.).
- Genera tablas resumen y detalles por archivo con formato visual limpio.
- Usa el comando git --no-pager diff --staged > informe.txt en tu consola con cambios STAGED 

## Requisitos
- Python 3.8+
- [python-docx](https://pypi.org/project/python-docx/)

## Instalación

1. Clona este repositorio.
2. Crea un entorno virtual e instala las dependencias:
   ```bash
   python -m venv venv
   source venv/bin/activate  # En Windows: venv\Scripts\activate
   pip install -r requirements.txt
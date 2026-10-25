"""
Entry point Vercel (serverless).

A Vercel moderna detecta a instância ASGI FastAPI diretamente via 'app'.
"""

import sys
from pathlib import Path

# Garante que a raiz do projeto esteja no sys.path na Vercel
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import app

# Exporta app diretamente (suporte nativo ASGI da Vercel)
# Mantém handler para compatibilidade se algum adaptador buscar por handler
handler = app


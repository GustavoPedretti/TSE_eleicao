"""
Entry point Vercel (serverless).

Todas as rotas do FastAPI passam por aqui via Mangum.
"""

from mangum import Mangum

from app.main import app

# lifespan="auto" tenta rodar startup; na Vercel o Cron não depende do loop local
handler = Mangum(app, lifespan="auto")

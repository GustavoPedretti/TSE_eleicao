# SPEC — Dashboard Eleições Brasil (TSE) · estudo de infra + API

Documento para **reproduzir** o projeto em outra IA / outro ambiente e para **comparar** implementações.

- **Objetivo do projeto:** estudar arquitetura de requisição, cache, banco e APIs usando dados reais do TSE (eleições 2026).
- **Não é** produto oficial da Justiça Eleitoral.
- **Stack alvo:** Python (FastAPI) + SQLite local (e Postgres/Supabase em produção) + front estático (Leaflet) + deploy pensado para **Vercel** (Cron 1 min) + **Supabase**.

---

## 1. Intenção original do autor (requisitos de produto / aprendizado)

### 1.1 Aprendizado
- Entender **sistema de requisição** (cliente → nosso backend → fonte externa).
- Entender **cache**: F5 de N usuários não pode virar N hits no TSE / no banco “quente”.
- Entender modelos de API / BFF (Backend for Frontend).
- Evoluir de dashboard tosco → dashboard com mapa.
- Subir depois no GitHub (o autor faz o Git sozinho).

### 1.2 Fonte de dados (TSE)
- API/CDN **pública** de divulgação: `https://resultados.tse.jus.br`
- Ambiente: `oficial`
- Ciclo: `ele2026`
- **Não é REST** tipo `/candidatos?uf=SP`. São **arquivos JSON** com URL previsível (CDN).
- Rate limit oficial ~**100 req/IP/s**; 404 em massa / abuso → bloqueio; município com **5 dígitos**.

### 1.3 Códigos de eleição 2026 (confirmados)
| Momento | Pleito | Federal (Presidente) | Estaduais |
|---------|--------|----------------------|-----------|
| 1º turno (04/10/2026) | 3220 | **6257** | 6259 |
| 2º turno (25/10/2026) | 3221 | **6258** | 6260 |

### 1.4 Regras de arquitetura (não negociáveis na visão do autor)
1. **Só o servidor fala com o TSE** — um “ciclo” / poller. O browser **nunca** chama `resultados.tse.jus.br` para votos.
2. O que o usuário chama é **nossa API**, que lê **nosso banco** (e opcionalmente cache em memória).
3. “Um hit” no TSE, na prática, **não** é um único HTTP (o TSE fragmenta em milhares de JSONs). Significa: **um ciclo do poller** que busca **tudo que precisamos** e grava no banco. Clientes só espalham (spread) o que já está conosco.
4. Front pode ser leve (não carregar o Brasil municipal de uma vez no cliente); o **servidor** é quem materializa o dataset.
5. Deploy: **Vercel** com Cron **1 minuto** (Hobby não dá 20s de cron). Banco alvo: **Supabase (Postgres)**. Local: SQLite ok.

### 1.5 Dashboard / UX desejada
- Mapa do Brasil **integrado ao mapa-múndi** (basemap com países/oceano).
- Estados coloridos; zoom em estado → **cidades (municípios)**; destaque leve do estado selecionado.
- Clique fora do estado → volta à visão Brasil.
- Cores: **sem votos = cinza**; com votos = escala **13 vermelho ↔ 22 azul** (disputa).
- Duas abas:
  - **2º turno** sozinho.
  - **Comparativo 1º × 2º** (mesma família de UI + tabela de comparação).
- Front carrega dados do banco **uma vez** (`/api/dashboard`) para as abas; não martelar TSE nem “refetch agressivo” sem necessidade.

---

## 2. O que foi implementado (estado atual)

### 2.1 Estrutura de pastas (reproduzir)

```
TSE_eleicao/
├── SPEC.md                 # este documento
├── requirements.txt
├── vercel.json
├── .env.example
├── .gitignore
├── api/
│   └── index.py            # entry Mangum (Vercel)
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── main.py             # FastAPI + rotas
│   ├── tse_client.py       # montagem de URL + parse JSON TSE
│   ├── poller.py           # ÚNICO falante do TSE
│   ├── municipios.py       # sync municipal (poller) + leitura DB
│   ├── db.py               # SQLite e/ou Postgres (DATABASE_URL)
│   ├── cache.py            # cache RAM (processo local)
│   └── enrich.py           # normaliza snapshots antigos na leitura
├── static/
│   ├── index.html          # dashboard Leaflet
│   └── geo/
│       └── br-ufs.geojson  # malha UFs (local)
├── supabase/
│   └── schema.sql
├── notebooks/
│   └── 01_eda_dados_tse.ipynb
└── data/                   # gitignored (sqlite, raw)
```

### 2.2 Dependências (`requirements.txt`)
- `fastapi`, `uvicorn[standard]`, `httpx`
- `mangum` (adapter Vercel)
- `psycopg[binary]` (Postgres/Supabase quando `DATABASE_URL` setada)
- Jupyter/pandas opcional (EDA)

### 2.3 URLs do TSE (contratos reais)

**Config de eleições**
```
GET https://resultados.tse.jus.br/oficial/comum/config/ele-c.json
```

**Config de municípios (lista cd TSE + cdi IBGE)**
```
GET https://resultados.tse.jus.br/oficial/ele2026/{eleicao}/config/mun-e{eleicao_6dig}-cm.json
# ex.: .../6258/config/mun-e006258-cm.json
```

**Resultado unificado Presidente — BR ou UF**
```
GET https://resultados.tse.jus.br/oficial/ele2026/{eleicao}/dados/{abr}/{abr}-c0001-e{eleicao_6dig}-u.json
# ex. 2º BR: .../6258/dados/br/br-c0001-e006258-u.json
# ex. SP:    .../6258/dados/sp/sp-c0001-e006258-u.json
```

**Resultado unificado Presidente — município**
```
GET https://resultados.tse.jus.br/oficial/ele2026/{eleicao}/dados/{uf}/{uf}{cd5}-c0001-e{eleicao_6dig}-u.json
# ex. SP capital: .../6258/dados/sp/sp71072-c0001-e006258-u.json
# cd = código TSE 5 dígitos; cdi = IBGE 7 dígitos (no config mun)
```

**Campos úteis no JSON**
- Root: `ele`, `t` (turno), `idg` (versão do arquivo), `dg`/`hg`, `s` (seções), `e` (eleitorado), `v` (votos), `carg`
- Candidatos: `carg[].agr[].par[].cand[]` → `n`, `nm`, `vap`, `pvap`
- Seções totalizadas %: `s.pst`

### 2.4 Poller (ciclo servidor)

Arquivo: `app/poller.py`

**A cada ciclo** (local: loop `POLL_INTERVAL_SEGUNDOS`, default **60**; Vercel: `GET/POST /api/poll` via Cron):

1. Sincroniza **BR + todas UFs + ZZ** para **1º turno (6257)** e **2º turno (6258)**.
2. Sincroniza **todos os municípios** do 2º turno.
3. Se `SYNC_MUN_1T=1`, sincroniza municípios do 1º turno também.

**Persistência**
- Tabela `snapshots`: um registro por download (eleicao + abrangencia + resumo_json + idg + mudou…).
- Tabela `polls`: log de tentativas.
- Abrangências:
  - `br`, `sp`, `rj`, … (2 letras / `zz`)
  - município: `sp71072` (sigla + cd 5 dígitos)

**Concorrência** (ajustável; cuidado com HTTP 429 do CDN):
- UFs: ~4
- Municípios: ~10

**Regra:** rotas do browser **não** chamam o TSE para votos.

### 2.5 Banco

Arquivo: `app/db.py` + `supabase/schema.sql`

- Sem `DATABASE_URL` → **SQLite** em `data/eleicoes.db`
- Com `DATABASE_URL` → **Postgres** (Supabase)
- Na Vercel, SQLite é efêmero → **Supabase é obrigatório** em produção

Schema (essência):
```sql
snapshots(
  id, eleicao, abrangencia, turno, idg,
  gerado_em_tse, secoes_pct, baixado_em, mudou, resumo_json
)
polls(id, eleicao, abrangencia, ok, mudou, idg, erro, em)
```

`resumo_json` inclui (após parse/`enrich`):
- candidatos, lider, `votos_13`, `votos_22`, `tem_votos`, `score_disputa` (−1 = 100% no 13, +1 = 100% no 22, null = sem votos)

### 2.6 Cache em memória

Arquivo: `app/cache.py`

- Espelha sobretudo o **2º turno** (BR + UFs) no processo local.
- Em Vercel serverless o cache **morre** entre invocações → verdade está no **Postgres**.
- Hits de F5 devem bater na API → banco/cache nosso, não no TSE.

### 2.7 API (FastAPI)

Arquivo: `app/main.py`

| Método | Rota | Comportamento |
|--------|------|----------------|
| GET/POST | `/api/poll` | Dispara ciclo TSE (proteger com `POLL_SECRET` / `CRON_SECRET`) |
| GET | `/api/dashboard` | **Uma leitura** do banco: bloco 1º + bloco 2º (BR + UFs) |
| GET | `/api/presidente?turno=1\|2` | BR do turno |
| GET | `/api/mapa/ufs?turno=1\|2` | UFs do turno |
| GET | `/api/mapa/{uf}/municipios?turno=1\|2` | **Só lê banco** |
| GET | `/api/status` | Saúde / contadores |
| GET | `/` | `static/index.html` |

### 2.8 Front (mapa)

Arquivo: `static/index.html`

- Leaflet + basemap **Esri World Dark Gray** (sem API key; **não** usar Carto free — exige key e mostra watermark).
- GeoJSON UFs: `/static/geo/br-ufs.geojson`
- GeoJSON municípios: CDN `geodata-br` (`geojs-{codigo_ibge_uf}-mun.json`), join por `cdi` (IBGE)
- Escala de cor: cinza sem voto; interpolação vermelho(13)↔azul(22) via `score_disputa`
- Abas: `2º turno` | `Comparativo 1º × 2º`
- UX mapa:
  - Clique UF → zoom, borda destaque, carrega cidades, **UF deixa de capturar clique** (`pointer-events: none`)
  - Clique cidade → painel lateral
  - Clique **fora** do estado selecionado → volta ao Brasil
  - Botão `← Brasil`

### 2.9 Vercel

`vercel.json`: build `api/index.py` (@vercel/python), rotas `/(.*)` → Mangum, Cron `* * * * *` → `/api/poll`.

Env sugeridas: `DATABASE_URL`, `CRON_SECRET`/`POLL_SECRET`, `ELEICAO_1T`, `ELEICAO_2T`, `RUN_LOCAL_POLLER=0`, `SYNC_MUN_1T`.

### 2.10 Como rodar local

```bash
cd TSE_eleicao
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
# abrir http://127.0.0.1:8000/
# forçar sync: curl -X POST http://127.0.0.1:8000/api/poll
```

---

## 3. Fluxo de dados (diagrama mental)

```
┌─────────────┐     Cron 1/min / loop local
│   Poller    │ ──────────────────────────► TSE CDN (muitos JSONs)
└──────┬──────┘
       │ grava
       ▼
┌─────────────┐     opcional espelho RAM (2º turno)
│ SQLite /    │ ◄────────────────────────── cache.py
│ Supabase    │
└──────┬──────┘
       │ lê (1× dashboard + sob zoom mun)
       ▼
┌─────────────┐
│  FastAPI    │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Browser    │  Leaflet (mapa mundi + UFs + municípios)
└─────────────┘
```

**Proibido:** browser → TSE (votos).  
**Permitido:** browser → CDN de **geometria** (GeoJSON IBGE/geodata-br) — isso não é resultado eleitoral.

---

## 4. Ponto em aberto — leitura do banco, cache e infra

A implementação atual **está correta** no princípio (poller único + banco + API de leitura). O autor quer **pensar melhorias** sem ainda fechar desenho. Outra IA deve **reproduzir o comportamento**, mas pode **propor** (e idealmente implementar de forma opcional/clara) melhorias nesses eixos:

### 4.1 O que já funciona
- Separação write-path (poller) vs read-path (API).
- Snapshots com `idg` para saber se o arquivo do TSE mudou.
- Dual backend SQLite/Postgres.
- Dashboard com uma leitura agregada.

### 4.2 Limitações conhecidas (candidatas a melhoria)
1. **Volume municipal**  
   ~5,5k arquivos/turno por ciclo. Em Cron Vercel (timeout curto Hobby) pode falhar. 429 do CDN aparece se concorrência alta.

2. **Cache RAM**  
   Útil só com processo longo (local). Na Vercel é quase inútil entre requests. Talvez: Redis/Upstash, ou confiar só em Postgres + HTTP cache curto.

3. **Modelo de dados**  
   Hoje: append de `resumo_json` blob. Alternativas: tabelas normalizadas (`resultados_uf`, `resultados_mun`), upsert por `(eleicao, abrangencia)` guardando só “último” + tabela `historico` quando `idg` muda.

4. **Leitura**  
   `/api/dashboard` monta payload na hora. Melhorias: materializar view/`dashboard_snapshot`, ETag, compressão, paginar comparativo.

5. **Estratégia de sync**  
   - 2º turno a cada 1 min (BR+UF sempre; mun rotativo por UF se timeout).  
   - 1º turno (já fechado): sync raro / uma vez.  
   Isso reduz carga sem violar “só o servidor fala com o TSE”.

6. **Fila / workers**  
   Autor cogitou fila + Nginx depois. Para Vercel, alternativas: Cron + job longo (Pro maxDuration), ou worker fora (Fly/Railway) só para o poller, API na Vercel só leitura.

7. **Observabilidade**  
   Métricas: duração do ciclo, 429, % mun sincronizados, lag `idg`.

### 4.3 Perguntas abertas (para discussão / comparação entre IAs)
- Upsert “último estado” vs append-only: o que priorizar para estudo vs produção?
- Cache L1 (RAM) + L2 (Redis) + L3 (Postgres) vale a pena neste tamanho?
- Sync municipal full a cada minuto vs round-robin de UFs?
- Separar serviço **Ingestão** do serviço **API** desde já?
- Como versionar schema do `resumo_json` sem quebrar o front?

**Instrução para a outra IA:**  
Reproduza primeiro o comportamento desta SPEC. Se divergir na arquitetura de cache/banco, documente o trade-off. Não faça o browser falar com o TSE para votos.

---

## 5. Critérios de aceite (reprodução “igual”)

1. `uvicorn` sobe; `/` mostra mapa mundi **sem** watermark “API KEY REQUIRED”.
2. `/api/poll` (ou poller local) grava BR/UFs 1º e 2º no banco; municípios ao menos do 2º.
3. Browser **não** gera tráfego de votos para `resultados.tse.jus.br` (só geometria CDN se necessário).
4. Mapa: cinza sem voto; escala 13 vermelho ↔ 22 azul com votos.
5. Clique UF → cidades; clique cidade → painel; clique fora do estado → Brasil.
6. Abas 2º turno e Comparativo; dados de `/api/dashboard` em uma leitura.
7. Pronto para `DATABASE_URL` Supabase + Cron Vercel 1 min.

---

## 6. Fora de escopo (por enquanto)
- Bairro / seção no mapa.
- Autenticação de usuários finais.
- Alterar/republicar dados do TSE.
- App mobile nativo.

---

## 7. Prompt sugerido para a outra IA

> Implemente do zero o projeto descrito em SPEC.md (Dashboard Eleições TSE 2026).  
> Respeite: poller único falando com o TSE; API só lê banco; front Leaflet com mapa mundi (basemap sem API key paga), UFs + municípios, escala 13×22, abas 2º e comparativo 1º×2º.  
> Stack: Python FastAPI, SQLite local + Postgres opcional, vercel.json + Mangum.  
> Ao final, liste desvios em relação à SPEC e propostas de melhoria só na camada cache/banco/ingestão, sem mudar a regra “cliente não fala com o TSE”.

---

*Gerado a partir da sessão de construção do repo `TSE_eleicao` (estudo). Data de referência do pleito 2º turno: 25/10/2026.*

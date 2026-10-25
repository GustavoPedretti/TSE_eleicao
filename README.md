# 🗳️ Dashboard de Eleições Presidenciais (TSE)

Dashboard interativo e arquitetura de ingestão de dados em tempo real da apuração eleitoral brasileira (dados oficiais do TSE).

Projeto concebido como estudo de engenharia de software para **alta concorrência**, **cache em camadas**, **BFF (Backend for Frontend)** e **persistência desacoplada (SQLite local e Supabase/PostgreSQL em nuvem)**, com deploy na **Vercel**.

---

## 🏛️ Arquitetura

```text
       [ TSE CDN Pública ]
                │
                ▼ (Poller Periódico / Vercel Cron / GitHub Action)
   [ FastAPI Backend / BFF ] ──► Grava em [ Supabase PostgreSQL ]
                ▲
                │ (Leitura otimizada com cache)
        [ Browser / Front ] (Leaflet interativo: Brasil, Estados e Municípios)
```

- **Consumo Protegido**: O navegador do usuário **nunca** faz requisições diretas à CDN do TSE. Apenas o servidor consome os dados oficiais via ciclo de polling, respeitando limites e rate limiting.
- **Eficiência**: O frontend consome os dados consolidados do nosso banco (`/api/dashboard`), evitando requisições excessivas.
- **Multi-ambiente**: Roda 100% local com SQLite ou na nuvem com Supabase (PostgreSQL) e Vercel Serverless.

---

## 🚀 Passo a Passo: Subindo no GitHub, Supabase e Vercel

### 1️⃣ Criar e Configurar o Supabase

1. Crie uma conta ou acesse [supabase.com](https://supabase.com).
2. Crie um novo projeto (ex: `tse-eleicao`). Guarde a senha definida para o banco.
3. No painel do projeto, abra o **SQL Editor** e execute o script contido em:
   [`supabase/schema.sql`](supabase/schema.sql)
4. Obtenha a Connection String do banco:
   - Vá em **Project Settings** (ícone de engrenagem) ➔ **Database**.
   - Na seção **Connection string**, escolha **URI**.
   - Copie a URI no formato:
     `postgresql://postgres.[SEU-PROJETO]:[SUA-SENHA]@aws-0-[REGIAO].pooler.supabase.com:6543/postgres`
   - *(Substitua `[SUA-SENHA]` pela sua senha real)*.

*(Opcional: Migrar dados já coletados no SQLite local)*:
Se você já tiver dados coletados localmente em `data/eleicoes.db`, rode:
```bash
python scripts/migrate_sqlite_to_supabase.py
```

---

### 2️⃣ Inicializar e Subir no GitHub

Na pasta raiz do projeto (`TSE_eleicao`), execute os comandos:

```bash
# 1. Inicializar o repositório git
git init

# 2. Adicionar os arquivos e commitar
git add .
git commit -m "feat: release inicial do dashboard tse com suporte a vercel e supabase"

# 3. Definir branch principal como main
git branch -M main

# 4. Conectar com o seu repositório no GitHub (crie um repo vazio no github.com antes)
git remote add origin https://github.com/SEU_USUARIO/SEU_REPOSITORIO.git

# 5. Fazer push
git push -u origin main
```

---

### 3️⃣ Deploy na Vercel

1. Acesse [vercel.com](https://vercel.com) e conecte sua conta do GitHub.
2. Clique em **Add New...** ➔ **Project** e importe o repositório `SEU_REPOSITORIO`.
3. Na seção **Environment Variables**, adicione as seguintes variáveis:

| Variável | Valor Exemplo | Descrição |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres.xxx:senha@...:6543/postgres` | URI do Postgres do Supabase |
| `CRON_SECRET` | `uma-chave-secreta-aleatoria-bem-longa` | Token de autenticação para acionar `/api/poll` |
| `POLL_SECRET` | `mesmo-valor-do-CRON_SECRET` | Compatibilidade com rotas de poll |
| `ELEICAO_ATIVA` | `6258` | Código da eleição ativa (ex: 2º turno 2026) |
| `SYNC_MUN_1T` | `0` | `0` para poupar tempo de execução em serverless |

4. Clique em **Deploy**.

---

### 4️⃣ Atualização Contínua dos Dados (Polling)

Como o plano gratuito (Hobby) da Vercel limita jobs cron agendados a uma vez por dia, o projeto inclui duas opções para polling frequente (a cada 5 ou 10 minutos):

#### Opção A — GitHub Actions (Automático e Gratuito)
O workflow já está pronto em [`.github/workflows/poller.yml`](.github/workflows/poller.yml).
Para ativá-lo:
1. No seu repositório do GitHub, vá em **Settings** ➔ **Secrets and variables** ➔ **Actions**.
2. Adicione os secrets:
   - `VERCEL_APP_URL`: Exemplo: `https://seu-projeto.vercel.app`
   - `CRON_SECRET`: O mesmo token cadastrado na Vercel.

#### Opção B — cron-job.org
Cadastre gratuitamente em [cron-job.org](https://cron-job.org):
- **URL**: `https://seu-projeto.vercel.app/api/poll`
- **Method**: `POST`
- **Header**: `Authorization: Bearer SEU_CRON_SECRET`
- **Frequência**: A cada 1 a 5 minutos.

---

## 💻 Desenvolvimento Local

```bash
# 1. Ative o ambiente virtual
.\.venv\Scripts\activate   # Windows

# 2. Instale dependências
pip install -r requirements.txt

# 3. Copie o arquivo de variáveis
cp .env.example .env

# 4. Inicie o servidor
uvicorn app.main:app --reload --port 8000
```
Acesse `http://localhost:8000` no navegador.

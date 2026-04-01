# QualIA Separado

## Estrutura

- `qualia_api.py`: backend/API FastAPI
- `frontend/`: site separado

## Desenvolvimento local

Rode a API:

```powershell
cd C:\Users\gabri\OneDrive\Documentos\UTFPR\Projetos
python -m uvicorn qualia_api:app --host 0.0.0.0 --port 8010
```

Abra:

- `http://localhost:8010`

## Publicação separada

### Backend

Publique o backend no Render/Railway com:

```bash
uvicorn qualia_api:app --host 0.0.0.0 --port $PORT
```

Se o frontend ficar em outro domínio, configure:

- `QUALIA_CORS_ORIGINS=https://seu-frontend.com`

Ou vários:

```text
QUALIA_CORS_ORIGINS=https://seu-frontend.com,https://www.seu-frontend.com
```

### Frontend

Publique a pasta `frontend/` em Vercel, Netlify ou outro host estático.

Antes de publicar, ajuste `frontend/config.js`:

```javascript
window.QUALIA_API_BASE = "https://sua-api.onrender.com";
```

Se quiser manter um exemplo separado, use `frontend/config.example.js` como base.

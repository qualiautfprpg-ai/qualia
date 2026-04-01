# QualIA

Sistema local para:

- cadastrar usuarios
- registrar testes fisicos
- rodar predicao com o modelo treinado
- mostrar o resultado em um painel web
- preparar a entrada futura por MQTT

## Como iniciar

Na pasta `C:\Users\gabri\OneDrive\Documentos\UTFPR\Projetos`:

```powershell
python qualia_api.py
```

Depois abra:

```text
http://localhost:8000
```

## Login inicial

- Admin: `admin@qualia.com`
- Senha: `1234`

Quando o admin cadastra um usuario, a senha inicial dele vira os 4 ultimos digitos do CPF.

## Fluxo atual

1. O admin cria o usuario.
2. O admin salva a avaliacao fisica.
3. A API roda o modelo de IA.
4. O resultado fica salvo no banco local `qualia_app.db`.
5. O usuario entra e visualiza score, classificacoes e recomendacoes.

## MQTT

Existe um endpoint pronto para ingestao:

```text
POST /mqtt/ingest
```

Corpo esperado:

```json
{
  "mqtt_key": "qualia-local-key",
  "email": "alguem@exemplo.com",
  "tipo_avaliacao": "mqtt",
  "peso": 82.4,
  "bf": 19.5,
  "agua": 54.0,
  "massa_muscular": 41.0,
  "vo2": 39.2,
  "pressao_sist": 120,
  "pressao_diast": 80,
  "cooper": 2.2,
  "flexibilidade": 26,
  "abd": 38,
  "flexao": 24,
  "fc_rep": 63,
  "fc_pos": 122,
  "fc_rec_5": 78,
  "fonte": "mqtt"
}
```

Voce pode trocar a chave padrao com a variavel:

```powershell
$env:QUALIA_MQTT_KEY="sua-chave"
python qualia_api.py
```

## Google Login

O layout ja suporta o botao, mas o login real com Google ainda depende de configurar a credencial:

```powershell
$env:QUALIA_GOOGLE_CLIENT_ID="seu-client-id"
python qualia_api.py
```

## Observacao importante sobre o modelo

A base atual tem apenas 111 registros. Para esse tamanho, deep learning tende a piorar o resultado por overfitting.
O melhor proximo passo e:

- revisar a qualidade da base
- aumentar o numero de amostras
- manter modelos tabulares fortes
- so depois testar rede neural

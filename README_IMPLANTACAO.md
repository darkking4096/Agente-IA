# Fernanda IA — Pacote de Implantação (Passo a passo leigo)

## O que é cada arquivo
- `app/fernanda_backend.py`: o **cérebro**. Recebe mensagens no `/webhook`, conversa com a IA usando o **prompt**, e envia a resposta.
- `app/prompt_fernanda.md`: o **manual da Fernanda** (identidade, tom de voz, como atender).
- `app/clinica_config.json`: dados da clínica (nome, endereço, horários).
- `app/knowledge_base.csv`: **planilha de serviços** com palavras-chave.
- `infra/nginx.conf`: o **porteiro** que manda `/api/...` e `/webhook` para o backend.
- `web/index.html`: página simples de entrada.
- `docker-compose.yml`: a **caixa** que liga tudo com um comando.
- `.env.example`: modelo de senhas/chaves (copie para `.env` e preencha).

## Como ligar (no servidor ou PC da clínica)
1. Instale Docker + Docker Compose.
2. Copie a pasta toda para a máquina.
3. Faça uma cópia do `.env.example` para `.env` e preencha as chaves.
4. Rode:
   ```bash
   docker-compose up -d
   ```
5. Teste em `http://SEU_ENDERECO/status` (deve aparecer `ok`).

## Apontar o WhatsApp (Evolution API)
- No painel da Evolution, coloque seu webhook como:  
  `http://SEU_ENDERECO/webhook`
- Coloque o token no header `X-Webhook-Token` (igual ao `WEBHOOK_TOKEN` do `.env`).

## Desligar rápido (botão de pânico)
- Remova o webhook no painel da Evolution (o bot para de receber mensagens).
- Ou rode `docker-compose down` (desliga tudo).

## Dicas de segurança
- Nunca compartilhe `.env`.
- Troque chaves se tiver dúvidas sobre vazamento.
- Mantenha o RUN_MODE=dev até terminar testes; mude para prod para enviar mensagens.

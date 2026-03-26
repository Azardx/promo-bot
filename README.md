# PromoBot 🤖🛒

O **PromoBot** é um bot de Telegram profissional, assíncrono e altamente escalável, projetado para coletar, filtrar e enviar automaticamente as melhores promoções de grandes marketplaces e agregadores brasileiros.

Desenvolvido com foco em robustez e performance, o bot utiliza técnicas modernas de scraping, sistema de cache em memória, banco de dados assíncrono e integração nativa com a API do Telegram via `aiogram 3`.

## 🌟 Funcionalidades Principais

- **Coleta Multi-Fonte:** Scrapers integrados para Shopee, AliExpress, Amazon, KaBuM!, Pelando e Promobit.
- **Motor Assíncrono:** Coleta concorrente de todas as fontes utilizando `httpx` com suporte a HTTP/2.
- **Filtros Inteligentes:** Remoção automática de produtos de baixa qualidade, spam e ofertas fora da faixa de preço desejada.
- **Deduplicação em 3 Camadas:** Verificação no batch atual, cache em memória (LRU com TTL) e banco de dados SQLite para garantir que nenhuma oferta seja enviada duas vezes.
- **Sistema de Scoring:** Priorização de ofertas baseada em desconto, categoria, loja e palavras-chave.
- **Formatação Profissional:** Mensagens em HTML com emojis, cálculo automático de desconto, economia e detecção de cupons.
- **Anti-Bot Bypass:** Rotação de User-Agents, controle de rate limit, backoff exponencial e suporte a proxies.
- **Painel Administrativo:** Comandos exclusivos para o administrador (`/stats`, `/health`, `/force`).

## 🏗️ Arquitetura do Sistema

O projeto segue uma arquitetura modular e limpa:

- `main.py`: Ponto de entrada, inicializa componentes e o scheduler assíncrono.
- `config.py`: Gerenciamento centralizado de variáveis de ambiente.
- `database/`: Modelos de dados (`models.py`) e operações assíncronas com SQLite (`db.py`).
- `scrapers/`: Scrapers específicos por loja, herdando de uma classe base robusta.
- `services/`: Lógica de negócio (Filtros, Deduplicação, Scoring, Motor Principal e Telegram).
- `utils/`: Utilitários compartilhados (Cache, HTTP Client, Logger, Proxy Manager).

## 🚀 Como Instalar e Executar

### Pré-requisitos

- Python 3.9 ou superior
- Um token de bot do Telegram (obtido via [@BotFather](https://t.me/BotFather))
- Um canal ou grupo no Telegram onde o bot seja administrador

### Instalação

1. Clone o repositório:
   ```bash
   git clone https://github.com/Azardx/promo-bot.git
   cd promo-bot
   ```

2. Crie um ambiente virtual e ative-o:
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/macOS
   # ou
   venv\Scripts\activate  # Windows
   ```

3. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```

4. Configure as variáveis de ambiente:
   ```bash
   cp .env.example .env
   ```
   Edite o arquivo `.env` e preencha com seus dados (principalmente `BOT_TOKEN`, `ADMIN_ID` e `CHANNEL_ID`).

### Execução

Para iniciar o bot, basta rodar o arquivo principal:

```bash
python promo_bot/main.py
```

O bot começará a monitorar as lojas imediatamente e enviará as ofertas para o canal configurado.

## ⚙️ Configurações Avançadas

Todas as configurações são feitas via arquivo `.env`. Algumas das principais:

- `SCRAPE_INTERVAL`: Tempo em segundos entre cada ciclo de busca (padrão: 120).
- `MIN_PRICE` / `MAX_PRICE`: Faixa de preço aceitável para as ofertas.
- `BLOCKED_KEYWORDS`: Palavras que farão a oferta ser ignorada (ex: capinha, película).
- `PRIORITY_KEYWORDS`: Palavras que aumentam a pontuação da oferta, fazendo com que seja enviada primeiro.
- `USE_PROXIES`: Se `True`, o bot utilizará a lista de proxies definida em `PROXY_LIST_FILE` para evitar bloqueios de IP.

## 📊 Comandos do Bot

- `/start` - Mensagem de boas-vindas.
- `/help` - Lista de comandos disponíveis.
- `/stats` - Exibe estatísticas detalhadas de coleta, cache e envio (Apenas Admin).
- `/health` - Exibe o status de saúde do sistema (Apenas Admin).
- `/force` - Força a execução imediata de um ciclo de coleta (Apenas Admin).

## 🛡️ Tratamento de Erros e Logs

O sistema possui um logger profissional configurado para exibir mensagens coloridas no console e salvar logs detalhados na pasta `logs/`.
- `promo_bot.log`: Log completo da aplicação com rotação automática.
- `errors.log`: Arquivo exclusivo para registro de erros e exceções.

## 🤝 Contribuição

Contribuições são bem-vindas! Sinta-se à vontade para abrir issues ou enviar pull requests com melhorias, novos scrapers ou correções de bugs.

## 📝 Licença

Este projeto está licenciado sob a licença MIT.

# PromoBot v2.2 — Changelog Completo

## Resumo das Alterações

Todas as 6 issues reportadas foram corrigidas e implementadas. O bot agora conta com **8 scrapers**, **filtro de conteúdo digital**, **sistema de cupons funcional** e **16+ comandos admin**.

---

## Bug #1: Bot Travava/Parava Após Enviar Promoções

**Causa raiz:** Os scrapers (especialmente Amazon e Shopee) usavam `curl_cffi` com retries e backoff exponencial que acumulavam sleeps de 6+ minutos, travando o event loop inteiro.

**Correções aplicadas:**

| Arquivo | Mudança |
|---------|---------|
| `main.py` | Timeout global de 180s por ciclo via `asyncio.wait_for()` |
| `engine.py` | Timeout individual de 45s por scraper |
| `http_client.py` | Retries reduzidos de 3 para 2, backoff máximo de 5s |
| `main.py` | Polling com `handle_as_tasks=True` para não bloquear |

---

## Bug #2: Amazon Enviando Filmes/Séries + Sem Fotos

**Causa raiz:** O scraper Amazon não tinha nenhum filtro de categoria — coletava qualquer coisa da página de deals, incluindo filmes, séries, livros e conteúdo digital do Prime Video.

**Correções aplicadas:**

| Arquivo | Mudança |
|---------|---------|
| `amazon.py` | Lista de 40+ keywords de conteúdo digital (temporada, série, filme, kindle, etc.) |
| `amazon.py` | Padrões de URL para Prime Video, Kindle, Music, Audible |
| `amazon.py` | Detecção de títulos curtos sem preço (padrão de filme/série) |
| `amazon.py` | Extração de imagem melhorada com 5 seletores de fallback |
| `amazon.py` | Extração de cupons de cards de oferta |

---

## Bug #3: Shopee Inativa (0 Ofertas)

**Causa raiz:** O scraper dependia de `window.__INITIAL_STATE__` que a Shopee não usa mais. O HTML retornado era uma shell vazia renderizada por JavaScript.

**Correções aplicadas:**

| Arquivo | Mudança |
|---------|---------|
| `shopee.py` | Reescrito usando API v4 da Shopee (endpoints internos) |
| `shopee.py` | Flash sales via `/api/v4/flash_sale/get_all_itemids` + `get_items` |
| `shopee.py` | Busca por keywords populares via `/api/v4/search/search_items` |
| `shopee.py` | Headers anti-bot com `af-ac-enc-dat` e cookies de sessão |
| `shopee.py` | Extração de vouchers/cupons da resposta da API |

---

## Bug #4: Cupons Não Sendo Enviados

**Causa raiz:** Embora o formatter tivesse suporte a cupons, nem todos os scrapers extraíam cupons, e o campo `coupon_code` ficava vazio.

**Correções aplicadas:**

| Arquivo | Mudança |
|---------|---------|
| `amazon.py` | Extração de cupons de badges e textos de desconto |
| `shopee.py` | Extração de voucher_info da API |
| `formatter.py` | Detecção automática de cupons no título (regex melhorado) |
| `formatter.py` | Template dedicado "CUPOM DE DESCONTO" com instrução "toque para copiar" |

---

## Feature #5: Novos Sites — Terabyte Shop e Mercado Livre

### Terabyte Shop (`terabyte.py`)
- Scraping da página de promoções e busca por keywords
- Extração de preços, preço original, desconto, imagem
- Detecção de cupons e frete grátis
- Suporte a links de afiliado

### Mercado Livre (`mercadolivre.py`)
- Scraping da página de ofertas do dia
- Busca por keywords populares (notebook, ssd, etc.)
- Extração de preços, desconto, imagem, frete grátis
- Filtro de vendedores com boa reputação

### Arquivos atualizados para suportar novas lojas:

| Arquivo | Mudança |
|---------|---------|
| `models.py` | Adicionados `TERABYTE` e `MERCADOLIVRE` ao enum Store |
| `__init__.py` | Registro dos novos scrapers |
| `config.py` | Adicionados à lista padrão de scrapers habilitados |
| `formatter.py` | Emojis e nomes de exibição para as novas lojas |

---

## Feature #6: Comandos Admin Expandidos

### Comandos Gerais
| Comando | Descrição |
|---------|-----------|
| `/start` | Mensagem de boas-vindas com lista de lojas |
| `/help` | Lista completa de comandos (diferente para admin e usuário) |

### Controle de Execução
| Comando | Descrição |
|---------|-----------|
| `/force` | Forçar ciclo de coleta imediato |
| `/pause` | Pausar coleta automática |
| `/resume` | Retomar coleta automática |
| `/interval [seg]` | Ver ou alterar intervalo de coleta (30-3600s) |

### Gerenciamento de Scrapers
| Comando | Descrição |
|---------|-----------|
| `/scrapers` | Status detalhado de cada scraper (24h) |
| `/enable [nome]` | Ativar um scraper em runtime |
| `/disable [nome]` | Desativar um scraper em runtime |
| `/test [nome]` | Testar um scraper específico (mostra 5 primeiros produtos) |
| `/available` | Listar todos os scrapers disponíveis |

### Informações e Monitoramento
| Comando | Descrição |
|---------|-----------|
| `/stats` | Estatísticas completas do sistema |
| `/health` | Status de saúde (cache, HTTP, proxies, Telegram) |
| `/uptime` | Tempo de atividade e contadores |
| `/config` | Configuração atual do bot |
| `/recent` | Últimas 10 promoções enviadas |

### Manutenção
| Comando | Descrição |
|---------|-----------|
| `/clearcache` | Limpar cache de deduplicação |
| `/resetdb` | Limpar todas as promoções do banco |
| `/blacklist [palavra]` | Adicionar palavra ao filtro |
| `/unblacklist [palavra]` | Remover palavra do filtro |
| `/showblacklist` | Mostrar blacklist atual |

### Comunicação
| Comando | Descrição |
|---------|-----------|
| `/broadcast [msg]` | Enviar mensagem personalizada ao canal |

---

## Arquivos Modificados

| Arquivo | Tipo |
|---------|------|
| `promo_bot/main.py` | Modificado (admin commands, timeout, v2.2) |
| `promo_bot/services/engine.py` | Modificado (per-scraper timeout) |
| `promo_bot/utils/http_client.py` | Modificado (reduced retries/backoff) |
| `promo_bot/scrapers/amazon.py` | Modificado (media filter, images, coupons) |
| `promo_bot/scrapers/shopee.py` | Reescrito (API v4) |
| `promo_bot/scrapers/terabyte.py` | **Novo** |
| `promo_bot/scrapers/mercadolivre.py` | **Novo** |
| `promo_bot/scrapers/__init__.py` | Modificado (novos scrapers) |
| `promo_bot/database/models.py` | Modificado (novos stores) |
| `promo_bot/config.py` | Modificado (novos scrapers habilitados) |
| `promo_bot/services/formatter.py` | Modificado (novos stores) |

---

## Como Atualizar

```bash
cd promo-bot
git pull origin main
pip install -r requirements.txt  # caso haja novas dependências
python -m promo_bot.main
```

## Nota Importante

Se você já tem um `.env` configurado com `ENABLED_SCRAPERS`, adicione os novos scrapers:

```env
ENABLED_SCRAPERS=shopee,aliexpress,amazon,pelando,promobit,kabum,terabyte,mercadolivre
```

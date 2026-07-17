"""Self-check para a regra IPOG (solicitação vs confirmação) e extração de data/valor.
Roda sem credenciais reais — GOOGLE_SERVICE_ACCOUNT_JSON fica ausente de propósito,
então create_ipog_payment_event() para antes de chamar a API do Calendar.
"""
import os

os.environ.setdefault('TELEGRAM_TOKEN', 'x')
os.environ.setdefault('CHAT_ID', '1')
os.environ.setdefault('GMAIL_USER', 'a@example.com')
os.environ.setdefault('GMAIL_APP_PASSWORD', 'x')

import bot

CONFIRMACAO_BODY = """Olá!

CONFIRMAÇÃO DE RECEBIMENTO NF

  * Confirmamos o recebimento da(s) nota(s) anexada (s) a esse e-mail.
  * A nota fiscal será direcionada ao Departamento Fiscal para análise, estando em conformidade com o solicitado, o pagamento será programado para o dia 26/02/2026.

...cadeia citada...
VALOR DA NOTA FISCAL: R$ 4215,00
"""

# 1) match_rule: email original (sem RES:) cai na regra de encaminhar p/ Vanessa
rule = bot.match_rule('nf@ipog.edu.br', 'SOLICITAÇÃO NF SEMANA 02 A 08/02/2026')
assert rule['name'] == 'IPOG — Solicitação de NF', rule

# 2) match_rule: resposta (RES:) cai na regra de calendário, não na de encaminhar
rule = bot.match_rule('nf@ipog.edu.br', 'RES: SOLICITAÇÃO NF SEMANA 02 A 08/02/2026')
assert rule['name'] == 'IPOG — Confirmação de Recebimento NF', rule
assert rule['action'] == 'calendar_event'

# 3) extração de data e valor da confirmação
link, valor, err = bot.create_ipog_payment_event(CONFIRMACAO_BODY)
assert link is None  # sem credencial no teste
assert valor == '4215,00', valor
assert 'GOOGLE_SERVICE_ACCOUNT_JSON' in err, err

# 4) corpo sem data de pagamento -> erro específico, sem quebrar
link, valor, err = bot.create_ipog_payment_event('sem nada relevante aqui')
assert link is None and err == 'Data de pagamento não encontrada no email'

# 5) consulta "próximo recebimento IPOG" também falha graciosamente sem credencial
events, err = bot.find_next_ipog_payments()
assert events == [] and 'GOOGLE_SERVICE_ACCOUNT_JSON' in err, (events, err)

# 6) a ferramenta está registrada pro Claude poder escolhê-la
assert any(t['name'] == 'proximo_recebimento_ipog' for t in bot.CLAUDE_TOOLS)

print('OK — regras IPOG e extração de data/valor validadas')

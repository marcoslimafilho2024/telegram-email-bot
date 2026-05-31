import os
import imaplib
import email
import json
from email.header import decode_header
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, JobQueue

TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
CHAT_ID = int(os.environ['CHAT_ID'])

# Contas monitoradas
ACCOUNTS = [
    {
        'user': os.environ['GMAIL_USER'],
        'password': os.environ['GMAIL_APP_PASSWORD'],
        'label': '💼 Trabalho',
    },
    {
        'user': os.environ.get('GMAIL_USER_2', ''),
        'password': os.environ.get('GMAIL_APP_PASSWORD_2', ''),
        'label': '👤 Pessoal',
    },
]
ACCOUNTS = [a for a in ACCOUNTS if a['user'] and a['password']]

# Compat: manter GMAIL_USER/GMAIL_APP_PASSWORD apontando para a primeira conta
GMAIL_USER = ACCOUNTS[0]['user']
GMAIL_APP_PASSWORD = ACCOUNTS[0]['password']

# ─── ALERTAS IMEDIATOS ────────────────────────────────────────────────────────
# Palavras que disparam 🚨 ALERTA na hora, independente do horário

ALERT_KEYWORDS = [
    # Pessoas chave
    'fellipe guerra', 'vanessa', 'ipog',
    # Obrigações fiscais
    'sped', 'sped fiscal', 'sped contábil', 'sped contabil', 'efd',
    'ecf', 'ecd', 'reinf', 'esocial', 'e-social', 'dctf', 'gia', 'gim',
    'nfe', 'nf-e', 'nota fiscal', 'xml', 'danfe',
    # Clientes (nomes distintos da planilha)
    'flex wind', 'guerra advogados', 'guerra treinamentos',
    'growup', 'italtecnology', 'iqnus', 'kgary', 'gmad',
    'milk heroes', 'mlvl', 'mourao estrutura', 'novo frio',
    'fit & fresh', 'fg coworking', 'fg consultoria',
    'hbr logistica', 'heverline', 'gutemberg',
    'farmacia filadelfia', 'farmacia eri', 'm3 farmacia',
    'lead imobiliaria', 'loteamento portal',
    'jessica furtado', 'luana lima', 'ivone cordeiro',
    'franklin cunha', 'marluce pinheiro',
    'c4 comercio', 'c4 consultoria', 'az servicos',
    'instituto ect', 'instituto de educacao contabil',
    'deusamim', 'vg braga', 'braga reis',
    '4 estylos', 'erico brasil',
    # Ações urgentes
    'intimação', 'intimacao', 'auto de infração', 'auto de infracao',
    'embargo', 'autuação', 'autuacao', 'notificação fiscal', 'notificacao fiscal',
    'multa de ofício', 'multa de oficio', 'parcelamento', 'execução fiscal',
    'penhora', 'bloqueio judicial', 'certidão negativa', 'certidao negativa',
    'regularize', 'pgfn', 'dívida ativa', 'divida ativa',
]

# ─── FILTROS ──────────────────────────────────────────────────────────────────

SPAM_SENDERS = [
    'netshoes', 'nordresearch', 'avast', 'cvc.com', 'skyscanner',
    'grancursosonline', 'serasa', 'smiles.com', 'facebookmail',
    'insiderstore', 'wispr.ai', 'academia-mail', 'hotmilhas',
    'retornar.com', 'accor', 'reminders@', 'news.all@mail',
    'grancursos', 'empiricus', 'infomoney',
]
SPAM_SUBJECTS = [
    '% off', 'cupom', 'oferta imperd', 'passagem', 'megapromo',
    'economize', 'desconto extra', 'prorrogamos', 'feirao', 'feirão',
    'voucher', 'milhas', 'resort', 'black friday',
    'ultima chance', 'última chance', 'imperdivel', 'imperdível',
]
URGENT_SENDERS = [
    'atendimento@compliance-ce.com.br', 'pgfn.gov.br', 'qive.com.br',
    'sigmavaf.com.br', 'accounts.google.com', 'adveronix.com',
    'regularize', 'receita.fazenda', 'flex-wind.com', 'tailscale',
    'sefaz', 'prefeitura', 'tce.', 'tribunal',
]
URGENT_SUBJECTS = [
    'gestta', 'vencer', 'certificado', 'regularize', 'seguranca',
    'segurança', 'prefeitura', 'pgfn', 'multa', 'vencimento',
    'prazo', 'pendencia', 'pendência', 'provisao', 'provisão',
    'reversao', 'reversão', 'trial ends', 'notificacao', 'notificação',
    'intimacao', 'intimação', 'auto de infração',
]
SALES_SENDERS = ['eduzz.com', 'hotmart.com']
PROFESSIONAL_SENDERS = [
    'conjur.com.br', 'legisweb.com.br', 'reformatributaria.com.br', 'fbc.org.br',
]
PERSONAL_SENDERS = ['colmaster.com.br', 'isabelbtz@gmail']
FINANCIAL_SENDERS = ['xpi.com.br', 'xpi.com', 'nubank', 'itau', 'bradesco', 'bb.com.br']

ICONS = {
    'URGENTE': '🔴',
    'VENDAS': '💰',
    'PROFISSIONAL': '👨‍💼',
    'PESSOAL': '👦',
    'FINANCEIRO': '💳',
    'OUTROS': '📌',
}

# Controle de alertas já enviados (evita repetição)
_alerted_ids = set()


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def decode_str(s):
    if not s:
        return ''
    parts = decode_header(s)
    result = ''
    for part, enc in parts:
        if isinstance(part, bytes):
            result += part.decode(enc or 'utf-8', errors='replace')
        else:
            result += str(part)
    return result.strip()


def is_spam(sender, subject):
    s, sub = sender.lower(), subject.lower()
    return any(x in s for x in SPAM_SENDERS) or any(x in sub for x in SPAM_SUBJECTS)


def classify(sender, subject):
    s, sub = sender.lower(), subject.lower()
    if any(x in s for x in URGENT_SENDERS) or any(x in sub for x in URGENT_SUBJECTS):
        return 'URGENTE'
    if any(x in s for x in SALES_SENDERS):
        return 'VENDAS'
    if any(x in s for x in PROFESSIONAL_SENDERS):
        return 'PROFISSIONAL'
    if any(x in s for x in PERSONAL_SENDERS):
        return 'PESSOAL'
    if any(x in s for x in FINANCIAL_SENDERS):
        return 'FINANCEIRO'
    return 'OUTROS'


def match_alert(sender, subject):
    """Retorna o keyword que deu match, ou None."""
    combined = (sender + ' ' + subject).lower()
    for kw in ALERT_KEYWORDS:
        if kw in combined:
            return kw
    return None


def get_imap(user=None, password=None, readonly=True):
    user = user or GMAIL_USER
    password = password or GMAIL_APP_PASSWORD
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(user, password)
    mail.select('inbox', readonly=readonly)
    return mail


def search_unseen(mail, hours=None):
    if hours:
        since = (datetime.now() - timedelta(hours=hours)).strftime('%d-%b-%Y')
        _, data = mail.search(None, f'(UNSEEN SINCE {since})')
    else:
        _, data = mail.search(None, 'UNSEEN')
    return data[0].split()


def fetch_headers(mail, ids, limit=200):
    results = []
    for eid in ids[-limit:]:
        _, msg_data = mail.fetch(eid, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])')
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        sender = decode_str(msg.get('From', ''))
        subject = decode_str(msg.get('Subject', '(sem assunto)'))
        results.append((eid, sender, subject))
    return results


def fetch_emails_account(acc, hours=None, only_cat=None, limit=100):
    mail = get_imap(acc['user'], acc['password'], readonly=True)
    ids = search_unseen(mail, hours)
    total_unseen = len(ids)
    buckets = {k: [] for k in ICONS}
    spam_count = 0
    for eid, sender, subject in fetch_headers(mail, ids, limit):
        if is_spam(sender, subject):
            spam_count += 1
            continue
        cat = classify(sender, subject)
        if only_cat and cat != only_cat:
            continue
        name = sender.split('<')[0].strip().strip('"') or sender
        name = name[:35] + '...' if len(name) > 35 else name
        sub = subject[:55] + '...' if len(subject) > 55 else subject
        buckets[cat].append(f'• {sub}\n  ↳ {name}')
    mail.logout()
    return buckets, total_unseen, spam_count


def fetch_emails(hours=None, only_cat=None, limit=100):
    try:
        merged = {k: [] for k in ICONS}
        total_all, spam_all = 0, 0
        for acc in ACCOUNTS:
            buckets, total, spam = fetch_emails_account(acc, hours, only_cat, limit)
            total_all += total
            spam_all += spam
            for cat in ICONS:
                # Prefixar com label da conta se mais de uma conta
                if len(ACCOUNTS) > 1:
                    merged[cat].extend([f'[{acc["label"]}] {item}' for item in buckets[cat]])
                else:
                    merged[cat].extend(buckets[cat])
        return merged, total_all, spam_all, None
    except Exception as e:
        return None, 0, 0, str(e)


def count_by_category(hours=None, limit=500):
    try:
        total_all = 0
        counts_all = {k: 0 for k in ICONS}
        spam_all = 0
        for acc in ACCOUNTS:
            mail = get_imap(acc['user'], acc['password'], readonly=True)
            ids = search_unseen(mail, hours)
            total_all += len(ids)
            for eid, sender, subject in fetch_headers(mail, ids, limit):
                if is_spam(sender, subject):
                    spam_all += 1
                    continue
                counts_all[classify(sender, subject)] += 1
            mail.logout()
        return total_all, counts_all, spam_all, None
    except Exception as e:
        return 0, {}, 0, str(e)


def mark_as_read(hours=None):
    try:
        total = 0
        for acc in ACCOUNTS:
            mail = get_imap(acc['user'], acc['password'], readonly=False)
            ids = search_unseen(mail, hours)
            if ids:
                for i in range(0, len(ids), 100):
                    batch = ids[i:i+100]
                    mail.store(b','.join(batch), '+FLAGS', '\\Seen')
            total += len(ids)
            mail.logout()
        return total
    except Exception as e:
        return -1


def build_message(buckets, title='📬 Emails não lidos'):
    lines = [title, '']
    total = 0
    for cat, items in buckets.items():
        if items:
            lines.append(f'{ICONS[cat]} {cat}')
            lines.extend(items)
            lines.append('')
            total += len(items)
    if total == 0:
        return '✅ Nenhum email importante no momento.'
    return '\n'.join(lines).strip()


# ─── MONITORAMENTO AUTOMÁTICO (a cada 10 min) ─────────────────────────────────

async def check_alerts(context):
    """Roda em background — dispara alerta se email contiver palavra-chave crítica."""
    global _alerted_ids
    for acc in ACCOUNTS:
        try:
            mail = get_imap(acc['user'], acc['password'], readonly=True)
            since = (datetime.now() - timedelta(minutes=35)).strftime('%d-%b-%Y')
            _, data = mail.search(None, f'(UNSEEN SINCE {since})')
            ids = data[0].split()
            alerts = []
            for eid, sender, subject in fetch_headers(mail, ids, limit=50):
                uid = f"{acc['user']}:{eid}"
                if uid in _alerted_ids:
                    continue
                kw = match_alert(sender, subject)
                if kw:
                    name = sender.split('<')[0].strip().strip('"') or sender
                    alerts.append((uid, name, subject, kw))
            mail.logout()
            for uid, name, subject, kw in alerts:
                _alerted_ids.add(uid)
                hora = datetime.now().strftime('%H:%M')
                msg = (
                    f'🚨 ALERTA — {hora}\n'
                    f'{acc["label"]}\n\n'
                    f'Palavra-chave: {kw.upper()}\n\n'
                    f'📧 {subject}\n'
                    f'↳ {name}'
                )
                await context.bot.send_message(chat_id=CHAT_ID, text=msg)
        except Exception as e:
            print(f'Erro check_alerts {acc["user"]}: {e}')


# ─── HANDLERS ────────────────────────────────────────────────────────────────

def only_authorized(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != CHAT_ID:
            return
        await func(update, context)
    return wrapper


@only_authorized
async def cmd_todos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Contando TODOS os emails não lidos...')
    total, counts, spam, err = count_by_category(hours=None, limit=500)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    lines = [f'📊 RESUMO GERAL — {total} emails não lidos\n']
    for cat, n in counts.items():
        if n > 0:
            lines.append(f'{ICONS[cat]} {cat}: {n}')
    lines.append(f'🗑️ Propaganda: {spam}')
    nao_contados = total - sum(counts.values()) - spam
    if nao_contados > 0:
        lines.append(f'⚠️ Além do limite analisado: ~{nao_contados}')
    lines.append('\nUse "urgente", "vendas" ou "hoje" para detalhes.')
    await update.message.reply_text('\n'.join(lines))


@only_authorized
async def cmd_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Emails das últimas 24h...')
    buckets, total, spam, err = fetch_emails(hours=24, limit=100)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    await update.message.reply_text(build_message(buckets, f'📬 Hoje — {total} total, {spam} propagandas'))


@only_authorized
async def cmd_hoje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_emails(update, context)


@only_authorized
async def cmd_semana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Emails da semana (7 dias)...')
    buckets, total, spam, err = fetch_emails(hours=168, limit=200)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    await update.message.reply_text(build_message(buckets, f'📬 Semana — {total} total, {spam} propagandas'))


@only_authorized
async def cmd_urgente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Buscando urgentes...')
    buckets, total, spam, err = fetch_emails(hours=None, only_cat='URGENTE', limit=200)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    items = buckets.get('URGENTE', [])
    if not items:
        await update.message.reply_text('✅ Nenhum urgente.')
        return
    msg = f'🔴 URGENTE ({len(items)})\n\n' + '\n'.join(items[:30])
    await update.message.reply_text(msg)
    if len(items) > 30:
        await update.message.reply_text(f'... e mais {len(items)-30} emails urgentes.')


@only_authorized
async def cmd_vendas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Buscando vendas...')
    buckets, total, spam, err = fetch_emails(hours=None, only_cat='VENDAS', limit=200)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    items = buckets.get('VENDAS', [])
    msg = (f'💰 VENDAS ({len(items)})\n\n' + '\n'.join(items[:30])) if items else '📭 Nenhuma venda.'
    await update.message.reply_text(msg)


@only_authorized
async def cmd_profissional(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buckets, total, spam, err = fetch_emails(hours=None, only_cat='PROFISSIONAL', limit=100)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    items = buckets.get('PROFISSIONAL', [])
    msg = (f'👨‍💼 PROFISSIONAL ({len(items)})\n\n' + '\n'.join(items[:30])) if items else '📭 Nenhum.'
    await update.message.reply_text(msg)


@only_authorized
async def cmd_financeiro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buckets, total, spam, err = fetch_emails(hours=None, only_cat='FINANCEIRO', limit=100)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    items = buckets.get('FINANCEIRO', [])
    msg = (f'💳 FINANCEIRO ({len(items)})\n\n' + '\n'.join(items[:30])) if items else '📭 Nenhum email financeiro.'
    await update.message.reply_text(msg)


@only_authorized
async def cmd_pessoal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buckets, total, spam, err = fetch_emails(hours=None, only_cat='PESSOAL', limit=100)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    items = buckets.get('PESSOAL', [])
    msg = (f'👦 PESSOAL ({len(items)})\n\n' + '\n'.join(items[:30])) if items else '📭 Nenhum email pessoal.'
    await update.message.reply_text(msg)


@only_authorized
async def cmd_marcar_lido(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('⏳ Marcando TODOS como lidos...')
    total = mark_as_read(hours=None)
    if total == -1:
        await update.message.reply_text('❌ Erro ao marcar emails.')
    elif total == 0:
        await update.message.reply_text('📭 Nenhum email não lido.')
    else:
        await update.message.reply_text(f'✅ {total} email(s) marcados como lidos!')


@only_authorized
async def cmd_pessoal_gmail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lê somente a conta pessoal cnp.marcoslima@gmail.com"""
    pessoal = next((a for a in ACCOUNTS if 'cnp' in a['user']), None)
    if not pessoal:
        await update.message.reply_text('❌ Conta pessoal não configurada.')
        return
    await update.message.reply_text(f'🔍 Lendo {pessoal["user"]}...')
    try:
        mail = get_imap(pessoal['user'], pessoal['password'], readonly=True)
        ids = search_unseen(mail, hours=None)
        total = len(ids)
        buckets = {k: [] for k in ICONS}
        spam_count = 0
        for eid, sender, subject in fetch_headers(mail, ids, limit=100):
            if is_spam(sender, subject):
                spam_count += 1
                continue
            cat = classify(sender, subject)
            name = sender.split('<')[0].strip().strip('"') or sender
            name = name[:35] + '...' if len(name) > 35 else name
            sub = subject[:55] + '...' if len(subject) > 55 else subject
            buckets[cat].append(f'• {sub}\n  ↳ {name}')
        mail.logout()
        msg = build_message(buckets, f'👤 Pessoal ({pessoal["user"]}) — {total} não lidos, {spam_count} propagandas')
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f'❌ Erro: {e}')


@only_authorized
async def cmd_varredura(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Varrendo caixa de entrada...')
    try:
        total = 0
        encontrados = []
        for acc in ACCOUNTS:
            mail = get_imap(acc['user'], acc['password'], readonly=True)
            ids = search_unseen(mail, hours=None)
            total += len(ids)
            for eid, sender, subject in fetch_headers(mail, ids, limit=500):
                kw = match_alert(sender, subject)
                if kw:
                    name = sender.split('<')[0].strip().strip('"') or sender
                    name = name[:35] + '...' if len(name) > 35 else name
                    sub = subject[:55] + '...' if len(subject) > 55 else subject
                    encontrados.append((kw, sub, name, acc['label']))
            mail.logout()

        if not encontrados:
            await update.message.reply_text(
                f'✅ Varredura concluída — {total} emails analisados\n'
                f'Nenhum email com palavras-chave críticas encontrado.'
            )
            return

        lines = [f'🚨 VARREDURA — {len(encontrados)} alertas em {total} emails\n']
        for kw, sub, name, label in encontrados[:30]:
            lines.append(f'⚡ [{kw.upper()}] {label}\n• {sub}\n  ↳ {name}\n')
        msg = '\n'.join(lines)
        await update.message.reply_text(msg)
        if len(encontrados) > 30:
            await update.message.reply_text(f'... e mais {len(encontrados)-30} emails com alertas.')
    except Exception as e:
        await update.message.reply_text(f'❌ Erro na varredura: {e}')


@only_authorized
async def cmd_alertas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kws = '\n'.join(f'• {k}' for k in sorted(ALERT_KEYWORDS))
    await update.message.reply_text(f'🚨 Palavras-chave monitoradas ({len(ALERT_KEYWORDS)}):\n\n{kws}')


@only_authorized
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '🤖 Fale naturalmente ou use comandos:\n\n'
        '📊 "ler os 1000 emails" — resumo geral\n'
        '☀️ "o que chegou hoje?" — últimas 24h\n'
        '📅 "e essa semana?" — últimos 7 dias\n'
        '🔴 "tem algo urgente?" — urgentes\n'
        '💰 "quanto vendi?" — vendas Eduzz/Hotmart\n'
        '👨‍💼 "ver profissional" — jurídico/tributário\n'
        '💳 "ver financeiro" — XP, Pix, banco\n'
        '👦 "email do davi?" — pessoal/escola\n'
        '🚨 "ver alertas" — palavras monitoradas\n'
        '✅ "zera a caixa" — marca tudo como lido\n\n'
        '🔔 Monitoramento automático ativo a cada 10 min!\n'
        'Alertas disparam para: clientes, SPED, PGFN, etc.'
    )


@only_authorized
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower().strip()

    if any(w in text for w in [
        'todos', 'tudo', '1000', '1.000', 'pendentes', 'quantos', 'fila',
        'geral', 'não lidos', 'nao lidos', 'caixa toda', 'caixa inteira',
        'ler tudo', 'ver tudo', 'listar tudo', 'quanto tem', 'quantos tem',
        'overview', 'visao geral', 'visão geral',
    ]):
        await cmd_todos(update, context)

    elif any(w in text for w in [
        'hoje', 'do dia', 'chegou hoje', 'recebi hoje',
        'ultimas 24', 'últimas 24', 'ultimo dia', 'último dia',
    ]):
        await cmd_hoje(update, context)

    elif any(w in text for w in [
        'semana', 'essa semana', 'esta semana', 'ultimos 7', 'últimos 7',
    ]):
        await cmd_semana(update, context)

    elif any(w in text for w in [
        'urgente', 'urgentes', 'importante', 'importantes', 'critico',
        'crítico', 'atenção', 'atencao', 'resolver', 'pendencia',
        'pendência', 'prazo', 'vencer', 'vencendo', 'gestta',
        'certificado', 'regularize', 'pgfn',
    ]):
        await cmd_urgente(update, context)

    elif any(w in text for w in [
        'venda', 'vendas', 'vendi', 'vendeu', 'vendemos',
        'hotmart', 'eduzz', 'faturamento', 'novo aluno', 'nova venda', 'quanto vendi',
    ]):
        await cmd_vendas(update, context)

    elif any(w in text for w in [
        'profissional', 'juridico', 'tributario', 'conjur', 'legisweb',
        'jurídico', 'tributário', 'reforma tributaria', 'boletim', 'legislacao',
    ]):
        await cmd_profissional(update, context)

    elif any(w in text for w in [
        'financeiro', 'financeira', 'xp', 'xpi', 'pix', 'banco',
        'extrato', 'transferencia', 'investimento',
    ]):
        await cmd_financeiro(update, context)

    elif any(w in text for w in [
        'pessoal', 'davi', 'escola', 'familia', 'família', 'colmaster', 'nota', 'filho',
    ]):
        await cmd_pessoal(update, context)

    elif any(w in text for w in [
        'email pessoal', 'emails pessoais', 'cnp', 'gmail pessoal',
        'meu pessoal', 'conta pessoal', 'ler pessoal',
    ]):
        await cmd_pessoal_gmail(update, context)

    elif any(w in text for w in [
        'varredura', 'varrer', 'buscar alertas', 'busca agora', 'checar agora',
        'verificar agora', 'procurar clientes', 'scan',
    ]):
        await cmd_varredura(update, context)

    elif any(w in text for w in [
        'alerta', 'alertas', 'monitorando', 'palavras', 'palavras-chave', 'keywords',
    ]):
        await cmd_alertas(update, context)

    elif any(w in text for w in [
        'marcar', 'lido', 'lidos', 'arquivar', 'limpar', 'zerar',
        'zera', 'limpa', 'marca lido', 'marca todos', 'marcar como lido',
    ]):
        await cmd_marcar_lido(update, context)

    elif any(w in text for w in [
        'email', 'emails', 'ler', 'leia', 'resumo', 'caixa', 'inbox',
        'mensagem', 'mensagens', 'me mostra', 'o que tem',
    ]):
        await cmd_todos(update, context)

    elif any(w in text for w in ['ajuda', 'help', 'comandos', 'menu']):
        await cmd_help(update, context)

    else:
        await update.message.reply_text(
            'Não entendi 😅\n\n'
            'Exemplos:\n'
            '• "ler os 1000 emails"\n'
            '• "tem algo urgente?"\n'
            '• "quanto vendi essa semana?"\n'
            '• "o que chegou hoje?"\n'
            '• "zera a caixa"\n'
            '• "ver alertas"\n\n'
            'Digite ajuda para tudo.'
        )


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Monitoramento automático a cada 10 minutos
    app.job_queue.run_repeating(check_alerts, interval=600, first=30)

    app.add_handler(CommandHandler('start', cmd_todos))
    app.add_handler(CommandHandler('todos', cmd_todos))
    app.add_handler(CommandHandler('emails', cmd_emails))
    app.add_handler(CommandHandler('hoje', cmd_hoje))
    app.add_handler(CommandHandler('semana', cmd_semana))
    app.add_handler(CommandHandler('urgente', cmd_urgente))
    app.add_handler(CommandHandler('vendas', cmd_vendas))
    app.add_handler(CommandHandler('profissional', cmd_profissional))
    app.add_handler(CommandHandler('financeiro', cmd_financeiro))
    app.add_handler(CommandHandler('pessoal', cmd_pessoal))
    app.add_handler(CommandHandler('cnp', cmd_pessoal_gmail))
    app.add_handler(CommandHandler('varredura', cmd_varredura))
    app.add_handler(CommandHandler('alertas', cmd_alertas))
    app.add_handler(CommandHandler('marcar', cmd_marcar_lido))
    app.add_handler(CommandHandler('ajuda', cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print('Bot rodando com monitoramento a cada 10 minutos...')
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()

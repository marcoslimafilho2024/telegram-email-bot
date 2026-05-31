import os
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
CHAT_ID = int(os.environ['CHAT_ID'])
GMAIL_USER = os.environ['GMAIL_USER']
GMAIL_APP_PASSWORD = os.environ['GMAIL_APP_PASSWORD']

SPAM_SENDERS = [
    'netshoes', 'nordresearch', 'avast', 'cvc.com', 'skyscanner',
    'grancursosonline', 'serasa', 'smiles.com', 'facebookmail',
    'insiderstore', 'wispr.ai', 'academia-mail', 'hotmilhas',
    'retornar.com', 'accor', 'reminders@', 'news.all@mail',
    'grancursos', 'empiricus', 'infomoney', 'spressovc',
]
SPAM_SUBJECTS = [
    '% off', 'cupom', 'oferta imperd', 'passagem', 'megapromo',
    'economize', 'desconto extra', 'prorrogamos', 'feirao', 'feirão',
    'voucher', 'milhas', 'resort', 'promo', 'black friday',
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
    'intimacao', 'intimação', 'auto de infração', 'embargo',
]
SALES_SENDERS = ['eduzz.com', 'hotmart.com']
PROFESSIONAL_SENDERS = [
    'conjur.com.br', 'legisweb.com.br', 'reformatributaria.com.br',
    'fbc.org.br', 'contabil', 'tributar',
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


def get_imap(readonly=True):
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    mail.select('inbox', readonly=readonly)
    return mail


def search_unseen(mail, hours=None):
    if hours:
        since = (datetime.now() - timedelta(hours=hours)).strftime('%d-%b-%Y')
        _, data = mail.search(None, f'(UNSEEN SINCE {since})')
    else:
        _, data = mail.search(None, 'UNSEEN')
    return data[0].split()


def fetch_emails(hours=None, only_cat=None, limit=100):
    try:
        mail = get_imap(readonly=True)
        ids = search_unseen(mail, hours)
        total_unseen = len(ids)

        buckets = {k: [] for k in ICONS}
        spam_count = 0

        for eid in ids[-limit:]:
            _, msg_data = mail.fetch(eid, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])')
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            sender = decode_str(msg.get('From', ''))
            subject = decode_str(msg.get('Subject', '(sem assunto)'))

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
        return buckets, total_unseen, spam_count, None
    except Exception as e:
        return None, 0, 0, str(e)


def count_by_category(hours=None, limit=500):
    """Conta emails por categoria sem listar detalhes — rápido para volumes grandes."""
    try:
        mail = get_imap(readonly=True)
        ids = search_unseen(mail, hours)
        total = len(ids)

        counts = {k: 0 for k in ICONS}
        spam = 0

        for eid in ids[-limit:]:
            _, msg_data = mail.fetch(eid, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])')
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            sender = decode_str(msg.get('From', ''))
            subject = decode_str(msg.get('Subject', ''))

            if is_spam(sender, subject):
                spam += 1
                continue
            counts[classify(sender, subject)] += 1

        mail.logout()
        return total, counts, spam, None
    except Exception as e:
        return 0, {}, 0, str(e)


def mark_as_read(hours=None):
    try:
        mail = get_imap(readonly=False)
        ids = search_unseen(mail, hours)
        if ids:
            # Marcar em lotes de 100 para evitar timeout
            for i in range(0, len(ids), 100):
                batch = ids[i:i+100]
                mail.store(b','.join(batch), '+FLAGS', '\\Seen')
        mail.logout()
        return len(ids)
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


def only_authorized(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != CHAT_ID:
            return
        await func(update, context)
    return wrapper


@only_authorized
async def cmd_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Lendo emails das últimas 24h...')
    buckets, total, spam, err = fetch_emails(hours=24, limit=100)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    msg = build_message(buckets, f'📬 Emails não lidos (24h) — {total} total, {spam} propagandas ignoradas')
    await update.message.reply_text(msg)


@only_authorized
async def cmd_todos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Contando TODOS os emails não lidos (pode demorar um pouco)...')
    total, counts, spam, err = count_by_category(hours=None, limit=500)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return

    lines = [f'📊 RESUMO GERAL — {total} emails não lidos\n']
    for cat, n in counts.items():
        if n > 0:
            lines.append(f'{ICONS[cat]} {cat}: {n}')
    lines.append(f'🗑️ Propaganda (ignorada): {spam}')
    nao_contados = total - sum(counts.values()) - spam
    if nao_contados > 0:
        lines.append(f'⚠️ Emails além do limite analisado: ~{nao_contados}')
    lines.append('\nUse "urgente", "vendas" ou "emails" para ver detalhes.')
    await update.message.reply_text('\n'.join(lines))


@only_authorized
async def cmd_urgente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Buscando urgentes (todos)...')
    buckets, total, spam, err = fetch_emails(hours=None, only_cat='URGENTE', limit=200)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    items = buckets.get('URGENTE', [])
    if not items:
        await update.message.reply_text('✅ Nenhum urgente.')
        return
    # Enviar em blocos se muitos itens
    header = f'🔴 URGENTE ({len(items)} emails)\n\n'
    msg = header + '\n'.join(items[:30])
    await update.message.reply_text(msg)
    if len(items) > 30:
        await update.message.reply_text(f'... e mais {len(items)-30} emails urgentes.')


@only_authorized
async def cmd_vendas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Buscando vendas (todos)...')
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
async def cmd_marcar_lido(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('⏳ Marcando TODOS os emails como lidos (pode demorar para caixas grandes)...')
    total = mark_as_read(hours=None)
    if total == -1:
        await update.message.reply_text('❌ Erro ao marcar emails.')
    elif total == 0:
        await update.message.reply_text('📭 Nenhum email não lido.')
    else:
        await update.message.reply_text(f'✅ {total} email(s) marcados como lidos!')


@only_authorized
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '🤖 O que você pode perguntar:\n\n'
        '📊 "ler os 1000 emails" — resumo geral\n'
        '☀️ "o que chegou hoje?" — últimas 24h\n'
        '📅 "e essa semana?" — últimos 7 dias\n'
        '🔴 "tem algo urgente?" — urgentes\n'
        '💰 "quanto vendi?" — vendas\n'
        '👨‍💼 "ver profissional" — jurídico/tributário\n'
        '💳 "ver financeiro" — XP, Pix, banco\n'
        '👦 "email do davi?" — pessoal/escola\n'
        '✅ "zera a caixa" — marca tudo como lido\n\n'
        'Fale naturalmente — eu entendo!'
    )


@only_authorized
async def cmd_hoje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Emails de hoje...')
    buckets, total, spam, err = fetch_emails(hours=24, limit=100)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    await update.message.reply_text(build_message(buckets, f'📬 Hoje — {total} emails, {spam} propagandas'))


@only_authorized
async def cmd_semana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Emails da semana (7 dias)...')
    buckets, total, spam, err = fetch_emails(hours=168, limit=200)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    await update.message.reply_text(build_message(buckets, f'📬 Semana — {total} emails, {spam} propagandas'))


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
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower().strip()

    # --- TODOS / GERAL ---
    if any(w in text for w in [
        'todos', 'tudo', '1000', '1.000', 'pendentes', 'quantos', 'fila',
        'geral', 'não lidos', 'nao lidos', 'caixa toda', 'caixa inteira',
        'ler tudo', 'ver tudo', 'listar tudo', 'me mostra tudo',
        'ultimos 1000', 'últimos 1000', 'quanto tem', 'quantos tem',
        'overview', 'visao geral', 'visão geral',
    ]):
        await cmd_todos(update, context)

    # --- HOJE ---
    elif any(w in text for w in [
        'hoje', 'do dia', 'chegou hoje', 'recebi hoje',
        'ultimas 24', 'últimas 24', 'ultimo dia', 'último dia',
    ]):
        await cmd_hoje(update, context)

    # --- SEMANA ---
    elif any(w in text for w in [
        'semana', 'essa semana', 'esta semana', 'ultimos 7', 'últimos 7',
        'semana toda', 'da semana',
    ]):
        await cmd_semana(update, context)

    # --- URGENTE ---
    elif any(w in text for w in [
        'urgente', 'urgentes', 'importante', 'importantes', 'critico',
        'criticos', 'crítico', 'críticos', 'atenção', 'atencao',
        'precisa de acao', 'precisa de ação', 'resolver', 'pendencia',
        'pendência', 'prazo', 'vencer', 'vencendo', 'gestta',
        'certificado', 'regularize', 'pgfn',
    ]):
        await cmd_urgente(update, context)

    # --- VENDAS ---
    elif any(w in text for w in [
        'venda', 'vendas', 'vendi', 'vendeu', 'vendemos',
        'hotmart', 'eduzz', 'faturamento', 'receita', 'compra',
        'novo aluno', 'nova venda', 'quanto vendi',
    ]):
        await cmd_vendas(update, context)

    # --- PROFISSIONAL ---
    elif any(w in text for w in [
        'profissional', 'juridico', 'tributario', 'conjur', 'legisweb',
        'jurídico', 'tributário', 'reforma tributaria', 'reforma tributária',
        'boletim', 'legislacao', 'legislação', 'norma',
    ]):
        await cmd_profissional(update, context)

    # --- FINANCEIRO ---
    elif any(w in text for w in [
        'financeiro', 'financeira', 'xp', 'xpi', 'pix', 'banco',
        'extrato', 'transferencia', 'transferência', 'investimento',
    ]):
        await cmd_financeiro(update, context)

    # --- PESSOAL ---
    elif any(w in text for w in [
        'pessoal', 'davi', 'escola', 'familia', 'família',
        'colmaster', 'nota', 'filho',
    ]):
        await cmd_pessoal(update, context)

    # --- MARCAR LIDO ---
    elif any(w in text for w in [
        'marcar', 'lido', 'lidos', 'arquivar', 'limpar', 'zerar',
        'zerar caixa', 'limpa tudo', 'marca lido', 'marca todos',
        'marcar todos', 'marcar como lido', 'mark as read',
        'limpa inbox', 'zera inbox', 'apagar notificacoes',
    ]):
        await cmd_marcar_lido(update, context)

    # --- EMAILS GERAIS (fallback) ---
    elif any(w in text for w in [
        'email', 'emails', 'ler', 'leia', 'resumo', 'caixa', 'inbox',
        'mensagem', 'mensagens', 'me mostra', 'o que tem',
    ]):
        await cmd_todos(update, context)

    # --- AJUDA ---
    elif any(w in text for w in ['ajuda', 'help', 'comandos', 'menu', 'o que voce faz', 'o que você faz']):
        await cmd_help(update, context)

    else:
        await update.message.reply_text(
            'Não entendi 😅\n\n'
            'Exemplos do que você pode dizer:\n\n'
            '• "ler os 1000 emails"\n'
            '• "tem algo urgente?"\n'
            '• "quanto vendi essa semana?"\n'
            '• "o que chegou hoje?"\n'
            '• "zera a caixa"\n'
            '• "tem email do davi?"\n'
            '• "ver financeiro"\n\n'
            'Digite ajuda para ver todos os comandos.'
        )


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
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
    app.add_handler(CommandHandler('marcar', cmd_marcar_lido))
    app.add_handler(CommandHandler('ajuda', cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print('Bot rodando...')
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()

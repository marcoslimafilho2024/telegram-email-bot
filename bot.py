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
]
SPAM_SUBJECTS = [
    '% off', 'cupom', 'oferta imperd', 'passagem', 'megapromo',
    'economize', 'desconto extra', 'prorrogamos', 'feirao', 'feirão',
    'voucher', 'milhas', 'resort', 'promo',
]
URGENT_SENDERS = [
    'atendimento@compliance-ce.com.br', 'pgfn.gov.br', 'qive.com.br',
    'sigmavaf.com.br', 'accounts.google.com', 'adveronix.com',
    'regularize', 'receita.fazenda', 'flex-wind.com', 'tailscale',
]
URGENT_SUBJECTS = [
    'gestta', 'vencer', 'certificado', 'regularize', 'seguranca',
    'segurança', 'prefeitura', 'pgfn', 'multa', 'vencimento',
    'prazo', 'pendencia', 'pendência', 'provisao', 'provisão',
    'reversao', 'reversão', 'trial ends',
]
SALES_SENDERS = ['eduzz.com', 'hotmart.com']
PROFESSIONAL_SENDERS = [
    'conjur.com.br', 'legisweb.com.br', 'reformatributaria.com.br',
    'fbc.org.br',
]
PERSONAL_SENDERS = ['colmaster.com.br']
FINANCIAL_SENDERS = ['xpi.com.br', 'xpi.com']

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


def fetch_emails(hours=24, only_cat=None):
    try:
        mail = imaplib.IMAP4_SSL('imap.gmail.com')
        mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        mail.select('inbox', readonly=True)

        since = (datetime.now() - timedelta(hours=hours)).strftime('%d-%b-%Y')
        _, data = mail.search(None, f'(UNSEEN SINCE {since})')
        ids = data[0].split()

        buckets = {k: [] for k in ICONS}

        for eid in ids[-60:]:
            _, msg_data = mail.fetch(eid, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])')
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            sender = decode_str(msg.get('From', ''))
            subject = decode_str(msg.get('Subject', '(sem assunto)'))

            if is_spam(sender, subject):
                continue

            cat = classify(sender, subject)
            if only_cat and cat != only_cat:
                continue

            name = sender.split('<')[0].strip().strip('"') or sender
            name = name[:35] + '...' if len(name) > 35 else name
            sub = subject[:55] + '...' if len(subject) > 55 else subject
            buckets[cat].append(f'• {sub}\n  ↳ {name}')

        mail.logout()
        return buckets, None
    except Exception as e:
        return None, str(e)


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


def mark_as_read(hours=24):
    try:
        mail = imaplib.IMAP4_SSL('imap.gmail.com')
        mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        mail.select('inbox', readonly=False)
        since = (datetime.now() - timedelta(hours=hours)).strftime('%d-%b-%Y')
        _, data = mail.search(None, f'(UNSEEN SINCE {since})')
        ids = data[0].split()
        if ids:
            mail.store(b','.join(ids), '+FLAGS', '\\Seen')
        mail.logout()
        return len(ids)
    except Exception as e:
        return -1


def only_authorized(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != CHAT_ID:
            return
        await func(update, context)
    return wrapper


@only_authorized
async def cmd_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Lendo emails das últimas 24h...')
    buckets, err = fetch_emails(hours=24)
    if err:
        await update.message.reply_text(f'❌ Erro ao ler Gmail: {err}')
        return
    await update.message.reply_text(build_message(buckets))


@only_authorized
async def cmd_urgente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Buscando urgentes (48h)...')
    buckets, err = fetch_emails(hours=48, only_cat='URGENTE')
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    items = buckets.get('URGENTE', [])
    msg = ('🔴 URGENTE\n\n' + '\n'.join(items)) if items else '✅ Nenhum urgente.'
    await update.message.reply_text(msg)


@only_authorized
async def cmd_vendas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Buscando vendas (48h)...')
    buckets, err = fetch_emails(hours=48, only_cat='VENDAS')
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    items = buckets.get('VENDAS', [])
    msg = ('💰 VENDAS\n\n' + '\n'.join(items)) if items else '📭 Nenhuma venda recente.'
    await update.message.reply_text(msg)


@only_authorized
async def cmd_profissional(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buckets, err = fetch_emails(hours=48, only_cat='PROFISSIONAL')
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    items = buckets.get('PROFISSIONAL', [])
    msg = ('👨‍💼 PROFISSIONAL\n\n' + '\n'.join(items)) if items else '📭 Nenhum email profissional.'
    await update.message.reply_text(msg)


@only_authorized
async def cmd_marcar_lido(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('✅ Marcando todos como lidos...')
    total = mark_as_read(hours=72)
    if total == -1:
        await update.message.reply_text('❌ Erro ao marcar emails.')
    elif total == 0:
        await update.message.reply_text('📭 Nenhum email não lido para marcar.')
    else:
        await update.message.reply_text(f'✅ {total} email(s) marcados como lidos!')


@only_authorized
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '🤖 Comandos disponíveis:\n\n'
        '📬 emails — resumo geral (24h)\n'
        '🔴 urgente — só os urgentes (48h)\n'
        '💰 vendas — novas vendas (48h)\n'
        '👨‍💼 profissional — jurídico, tributário\n'
        '✅ marcar lido — marca todos como lidos\n'
        '❓ ajuda — esta mensagem\n\n'
        'Pode escrever em texto livre também!'
    )


@only_authorized
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower().strip()

    if any(w in text for w in ['email', 'emails', 'ler', 'leia', 'resumo', 'caixa', 'inbox']):
        await cmd_emails(update, context)
    elif any(w in text for w in ['urgente', 'urgentes', 'importante', 'importantes', 'critico']):
        await cmd_urgente(update, context)
    elif any(w in text for w in ['venda', 'vendas', 'vendi', 'vendeu']):
        await cmd_vendas(update, context)
    elif any(w in text for w in ['profissional', 'juridico', 'tributario', 'conjur', 'legisweb']):
        await cmd_profissional(update, context)
    elif any(w in text for w in ['marcar', 'lido', 'lidos', 'arquivar', 'limpar']):
        await cmd_marcar_lido(update, context)
    elif any(w in text for w in ['ajuda', 'help', 'comandos', 'menu']):
        await cmd_help(update, context)
    else:
        await update.message.reply_text(
            'Não entendi 😅\n\nTente: emails, urgente, vendas, profissional, ajuda'
        )


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler('start', cmd_emails))
    app.add_handler(CommandHandler('emails', cmd_emails))
    app.add_handler(CommandHandler('urgente', cmd_urgente))
    app.add_handler(CommandHandler('vendas', cmd_vendas))
    app.add_handler(CommandHandler('profissional', cmd_profissional))
    app.add_handler(CommandHandler('marcar', cmd_marcar_lido))
    app.add_handler(CommandHandler('ajuda', cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print('Bot rodando...')
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
